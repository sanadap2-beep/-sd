"""اختبارات تثبيت لخطأ شاشة خدمات الرشق:

    Bad Request: can't parse entities: Unexpected end tag at byte offset 82

السبب الجذري كان أن نص شاشة القسم يُبنى كذلك::

    f"<b>{title}</b>{sub_desc}{I18nService.t('ux_games_282_17', language)}"

ومقطع الترجمة نفسه كان يبدأ بـ ``</b>`` (نتيجة تقسيم f-string آلياً)، فينتج
وسم إغلاق زائد يرفضه تيليجرام وتبقى شاشة القسم فارغة مع رسالة خطأ مضللة
«انتهت مهلة الاتصال بالمزود».

يغطي الملف:
1) مُصلِّح HTML (``services.html_guard``): الوسوم الزائدة/الناقصة والتهريب.
2) تهريب أسماء الأقسام والمنتجات وشرحها في شاشة المتجر.
3) مفاتيح الترجمة: لا تحمل وسوماً معلّقة ولا ``\n`` حرفية.
4) التشخيص الصحيح للخطأ بدل نسبة العطل للمزود.
5) شبكة الأمان: ``HtmlGuardedBot`` تصلح قبل الإرسال وتتحول للنص العادي عند الرفض.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendMessage

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    SubCategory,
)
from handlers.games import _show_subcategory, catalog_header
from services.dynamic_service import DynamicService
from services.error_diagnosis_service import diagnose
from services.html_guard import (
    HtmlGuardedBot,
    degrade_to_plain,
    esc,
    guard_method,
    is_parseable,
    repair_html,
    to_plain,
)
from services.i18n_service import I18nService

# مفاتيح شاشات المتجر التي كانت تحمل </b> معلّقاً
CATALOG_KEYS = (
    "ux_games_246_13",
    "ux_games_252_14",
    "ux_games_276_16",
    "ux_games_282_17",
)


# ────────────────────────── مُصلِّح HTML ──────────────────────────
def test_repair_drops_stray_closing_tag():
    """</b> زائد (سبب العطل المبلَّغ) يُحذف بدل أن تُرفض الرسالة."""
    broken = "<b>تيك توك</b>\n<i>شرح</i>\n</b>\n\nاختر المنتج:"
    fixed = repair_html(broken)
    assert is_parseable(fixed)
    assert fixed.count("</b>") == 1
    assert "اختر المنتج:" in fixed


def test_repair_closes_unclosed_tag():
    assert repair_html("<b>نص") == "<b>نص</b>"
    assert is_parseable(repair_html("<b>نص"))


def test_repair_escapes_literal_angle_brackets():
    """«<» في اسم خدمة (Followers < 1h) نصٌ حرفي لا بداية وسم."""
    fixed = repair_html("خدمة < 1h & HQ")
    assert "&lt;" in fixed
    assert is_parseable(fixed)
    assert repair_html(fixed) == fixed  # idempotent


def test_repair_keeps_valid_markup_untouched():
    valid = '<b>عريض</b> و<i>مائل</i> و<code>&lt;كود&gt;</code> و1000 > 500'
    assert repair_html(valid) == valid
    assert is_parseable(valid)


def test_repair_fixes_overlapping_tags():
    fixed = repair_html("<b>أ<i>ب</b>ج</i>")
    assert is_parseable(fixed)
    assert fixed == "<b>أ<i>ب</i></b>ج"


def test_repair_escapes_unknown_tags():
    fixed = repair_html("<service> و <b>عريض</b>")
    assert "&lt;service>" in fixed
    assert "<b>عريض</b>" in fixed
    assert is_parseable(fixed)


def test_to_plain_strips_markup():
    assert to_plain("<b>عنوان</b>\n<code>x</code>") == "عنوان\nx"


def test_esc_handles_none_and_specials():
    assert esc(None) == ""
    assert esc("a<b>&c") == "a&lt;b&gt;&amp;c"


# ─────────────────── تهريب نصوص قاعدة البيانات ───────────────────
def test_catalog_header_escapes_name_and_description():
    header = catalog_header("تيك توك </b>", "خدمات <b>رخيصة</b> & سريعة")
    assert is_parseable(header)
    assert "&lt;/b&gt;" in header
    assert header.startswith("<b>تيك توك &lt;/b&gt;</b>")


@pytest.mark.parametrize("key", CATALOG_KEYS)
def test_catalog_locale_keys_carry_no_dangling_tags(key):
    """لا وسوم داخل مقاطع الترجمة المفردة: الكود يملك التنسيق."""
    for language in ("ar", "en"):
        value = I18nService.t(key, language)
        assert "<" not in value, f"{key}/{language}: {value!r}"
        assert ">" not in value, f"{key}/{language}: {value!r}"


def test_no_locale_contains_literal_newline():
    """``\\n`` حرفية كانت تظهر للمستخدم بدل سطر جديد (خطأ الاقتباس الآلي)."""
    for locale in ("ar", "en"):
        data = json.loads((Path(__file__).resolve().parents[1] / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
        for key, value in data.items():
            if isinstance(value, str):
                assert "\\n" not in value, f"{locale}:{key} يحوي \\n حرفياً"


# ────────────── اختبار تكاملي: شاشة القسم الفرعي ──────────────
class _CapturingTarget:
    """بديل عن Message/CallbackQuery يلتقط نص الشاشة."""

    def __init__(self):
        self.text: str | None = None

    async def answer(self, text, **kwargs):
        self.text = text


@pytest.mark.asyncio
@pytest.mark.parametrize("with_products", (False, True))
async def test_show_subcategory_sends_parseable_message(with_products: bool):
    """الضغط على أي خدمة/تطبيق في الرشق يجب أن يرسل شاشة صالحة للتحليل."""
    async with async_session_maker() as session:
        category = Category(
            name_ar="رشق <b>", emoji="📈", type=CategoryType.SMM, is_active=True
        )
        session.add(category)
        await session.flush()
        sub = SubCategory(
            category_id=category.id,
            name_ar="تيك توك </b>",
            emoji="🎵",
            description="متابعين < 1 ساعة & أرخص سعر",
            is_active=True,
        )
        session.add(sub)
        await session.flush()
        if with_products:
            session.add(
                Product(
                    sub_category_id=sub.id,
                    name_ar="متابعين HQ <fast>",
                    price_usd=1.25,
                    status=ProductStatus.ACTIVE,
                    fulfillment_type=ProductFulfillmentType.API,
                    description="بدء <10 دقائق",
                )
            )
        await session.commit()

        fresh = await DynamicService.get_sub_category(session, sub.id)
        target = _CapturingTarget()
        await _show_subcategory(target, session, fresh, "ar")

        assert target.text, "لم يُبنَ نص الشاشة"
        assert is_parseable(target.text), target.text
        assert "</b></b>" not in target.text
        assert "تيك توك" in target.text


@pytest.mark.asyncio
async def test_category_products_count_shown():
    """التأكد أن مسار «لا توجد منتجات» يعمل هو الآخر دون خطأ تنسيق."""
    async with async_session_maker() as session:
        category = Category(name_ar="تطبيقات", emoji="📦", type=CategoryType.APPS, is_active=True)
        session.add(category)
        await session.flush()
        sub = SubCategory(category_id=category.id, name_ar="نتفلكس", emoji="🎬", is_active=True)
        session.add(sub)
        await session.commit()
        target = _CapturingTarget()
        await _show_subcategory(target, session, sub, "en")
        assert is_parseable(target.text)
        assert "No products" in target.text or "منتجات" in target.text


# ─────────────────────── شبكة الأمان عند الإرسال ───────────────────────
def _bot(**kwargs) -> HtmlGuardedBot:
    return HtmlGuardedBot(
        token="123456:TEST_TOKEN_FOR_TESTS_1234567890",
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        **kwargs,
    )


def test_guard_method_repairs_html_text():
    bot = _bot()
    method = EditMessageText(chat_id=1, message_id=2, text="<b>س</b></b>")
    assert guard_method(bot, method) is True
    assert method.text == "<b>س</b>"


def test_guard_method_respects_explicit_markdown():
    """لا نتدخل في الرسائل التي لا تُرسَل بوضع HTML."""
    bot = _bot()
    method = SendMessage(chat_id=1, text="**bold**</b>", parse_mode=ParseMode.MARKDOWN)
    assert guard_method(bot, method) is False
    assert method.text == "**bold**</b>"


def test_guard_method_skips_manual_entities():
    """حين يمرَّر entities صراحةً تصبح المسافات حساسة، فلا نعدّل النص."""
    from aiogram.types import MessageEntity

    bot = _bot()
    method = SendMessage(
        chat_id=1,
        text="<b>x</b></b>",
        entities=[MessageEntity(type="bold", offset=0, length=3)],
    )
    assert guard_method(bot, method) is False


def test_degrade_to_plain_removes_markup_and_parse_mode():
    method = EditMessageText(chat_id=1, message_id=2, text="<b>عنوان</b> & فقرة")
    assert degrade_to_plain(method) is True
    assert method.parse_mode is None
    assert method.text == "عنوان & فقرة"


class _RecordingSession:
    """جلسة وهمية تسجّل ما أُرسل فعلاً، وترفض التنسيق مرة واحدة إن طُلب."""

    def __init__(self, reject_times: int = 0):
        self.sent: list[tuple[str, object]] = []
        self.reject_times = reject_times

    async def __call__(self, bot, method, timeout=None):
        self.sent.append((method.text, method.parse_mode))
        if self.reject_times > 0:
            self.reject_times -= 1
            raise TelegramBadRequest(
                method="EditMessageText",
                message="Telegram server says - Bad Request: can't parse entities: Unexpected end tag at byte offset 82",
            )
        return True


@pytest.mark.asyncio
async def test_guarded_bot_repairs_before_sending():
    bot = _bot()
    bot.session = _RecordingSession()
    await bot(EditMessageText(chat_id=1, message_id=2, text="<b>تيك توك</b></b>\n</b>"))
    assert len(bot.session.sent) == 1
    assert is_parseable(bot.session.sent[0][0])


@pytest.mark.asyncio
async def test_guarded_bot_falls_back_to_plain_text():
    """إن رفض تيليجرام التنسيق رغم الإصلاح، تصل الرسالة نصاً لا تختفي."""
    bot = _bot()
    bot.session = _RecordingSession(reject_times=1)
    await bot(EditMessageText(chat_id=1, message_id=2, text="<b>عنوان</b>"))
    assert len(bot.session.sent) == 2
    text, parse_mode = bot.session.sent[1]
    assert text == "عنوان"
    assert parse_mode is None


@pytest.mark.asyncio
async def test_guarded_bot_propagates_other_errors():
    """أخطاء تيليجرام الحقيقية لا تُبتلع: الشبكة تخصّ التنسيق فقط."""
    bot = _bot()
    sent: list[str] = []

    async def failing(bot_, method, timeout=None):
        sent.append(method.text)
        raise TelegramBadRequest(method="EditMessageText", message="Bad Request: message to edit not found")

    bot.session = failing
    with pytest.raises(TelegramBadRequest):
        await bot(EditMessageText(chat_id=1, message_id=2, text="<b>س</b>"))
    # محاولة واحدة فقط: لا إعادة إرسال عند خطأ غير متعلق بالتنسيق
    assert sent == ["<b>س</b>"]


# ───────────────────── تشخيص الخطأ للأدمن ─────────────────────
_TRACEBACK_WITH_TIMEOUT_WORDS = """
  File "/site-packages/aiogram/client/session/aiohttp.py", line 189, in make_request
    return await self.session(self, method, timeout=request_timeout)
  File "/home/container/handlers/games.py", line 197, in _show_subcategory
    await target.message.edit_text(text, reply_markup=markup)
"""


def test_diagnose_reports_html_parse_error_not_provider_timeout():
    exc = TelegramBadRequest(
        method="EditMessageText",
        message="Telegram server says - Bad Request: can't parse entities: Unexpected end tag at byte offset 82",
    )
    diagnosis = diagnose(exc, _TRACEBACK_WITH_TIMEOUT_WORDS, context="CallbackQuery subcat:39")
    assert "HTML" in diagnosis.title
    assert diagnosis.severity == "medium"
    assert "مهلة" not in diagnosis.title


def test_diagnose_still_maps_real_provider_timeout():
    exc = TimeoutError("انتهت مهلة الاتصال بالمزود")
    diagnosis = diagnose(exc)
    assert "مهلة" in diagnosis.title
    assert diagnosis.severity == "high"


def test_diagnose_does_not_guess_from_traceback_noise():
    """كلمة json في التتبّع لا تعني أن رد المزود فاسد."""
    exc = RuntimeError("غير متوقع")
    diagnosis = diagnose(exc, "File '/home/container/json_tools.py', line 403")
    assert "json" not in diagnosis.title.lower()
    assert "مفتاح API" not in diagnosis.title
