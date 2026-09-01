"""اختبارات تثبيت لإصلاحات أخطاء التشغيل المبلغ عنها.

يغطي:
1) خطأ TelegramBadRequest: message is not modified
   (ضغط نفس الزر مرتين) — يجب أن يُتجاهل بهدوء في
   ErrorReportingMiddleware دون إبلاغ الأدمن.
2) خطأ SQLAlchemy MissingGreenlet عند عرض قائمة الأقسام
   (admin:categories) وقائمة الأقسام الفرعية — يجب أن تجلب
   العلاقات محمّلة مسبقاً (selectinload).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, User as AiogramUser

from database.engine import async_session_maker
from database.models import Category, CategoryType, SubCategory
from keyboards.admin_categories_v2 import (
    categories_list_kb,
    sub_categories_list_kb,
)
from middlewares.error_middleware import (
    ErrorReportingMiddleware,
    _is_benign_telegram_error,
)
from services.dynamic_service import DynamicService


def _telegram_bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method="", message=message)


def test_benign_error_detector_accepts_message_not_modified():
    """خطأ «message is not modified» يُصنّف حميداً."""
    exc = _telegram_bad_request(
        "Bad Request: message is not modified: specified new message content "
        "and reply markup are exactly the same as a current content and reply "
        "markup of the message"
    )
    assert _is_benign_telegram_error(exc) is True


def test_benign_error_detector_accepts_expired_query():
    """خطأ انتهاء صلاحية الضغطة يُصنّف حميداً أيضاً."""
    exc = _telegram_bad_request(
        "Bad Request: query is too old and response timeout expired or query id is invalid"
    )
    assert _is_benign_telegram_error(exc) is True


def test_benign_error_detector_rejects_real_errors():
    """الأخطاء الحقيقية الأخرى لا تُتجاهل أبداً."""
    for message in (
        "Bad Request: message to edit not found",
        "Bad Request: chat not found",
        "Not Found",
    ):
        assert _is_benign_telegram_error(_telegram_bad_request(message)) is False


def _make_callback_query() -> tuple[CallbackQuery, list]:
    """ينشئ CallbackQuery حقيقياً مع تسجيل الاستجابات في قائمة خارجية.

    ملاحظة: نموذج CallbackQuery مجمّد (frozen) في aiogram،
    لذا نستخدم object.__setattr__ لتظليل دالة answer.
    """
    answered: list[dict] = []
    callback = CallbackQuery(
        id="test-query",
        from_user=AiogramUser(
            id=8971396510, is_bot=False, first_name="Test", username="tester"
        ),
        chat_instance="test-instance",
        data="store:section:apps",
    )

    async def _record_answer(text=None, show_alert=False, **kwargs):
        answered.append({"text": text, "show_alert": show_alert})

    object.__setattr__(callback, "answer", _record_answer)
    return callback, answered


class _FakeBot:
    """Bot وهمي يلتقط رسائل الإبلاغ للأدمن."""

    def __init__(self):
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(text)


@pytest.mark.asyncio
async def test_middleware_swallows_not_modified_without_admin_alert(monkeypatch):
    """الضغط المكرر لا يرسل تنبيهاً للأدمن ولا يظهر تحذيراً للمستخدم."""
    import middlewares.error_middleware as em

    monkeypatch.setattr(em.settings, "ADMIN_NOTIFY_CHAT_ID", -1001)

    bot = _FakeBot()
    callback, answered = _make_callback_query()
    middleware = ErrorReportingMiddleware()

    async def handler(event, data):
        raise _telegram_bad_request("Bad Request: message is not modified")

    result = await middleware(handler, callback, {"bot": bot})

    assert result is None
    # لا إبلاغ للأدمن ولا تحذير مرئي للمستخدم
    assert bot.sent == []
    assert answered == [{"text": None, "show_alert": False}]


@pytest.mark.asyncio
async def test_middleware_still_reports_real_errors(monkeypatch):
    """الأخطاء الحقيقية تستمر في الإبلاغ كما هي."""
    import middlewares.error_middleware as em

    monkeypatch.setattr(em.settings, "ADMIN_NOTIFY_CHAT_ID", -1001)

    bot = _FakeBot()
    callback, answered = _make_callback_query()
    middleware = ErrorReportingMiddleware()

    async def handler(event, data):
        raise _telegram_bad_request("Bad Request: message to edit not found")

    result = await middleware(handler, callback, {"bot": bot})

    assert result is None
    # وصل إبلاغ للأدمن بالخطأ الحقيقي
    assert len(bot.sent) == 1
    assert "message to edit not found" in bot.sent[0]
    # المستخدم رأى تحذيراً
    assert answered and answered[0]["show_alert"] is True


@pytest.mark.asyncio
async def test_admin_categories_list_no_lazy_io():
    """قائمة الأقسام الرئيسية تعمل دون تحميل كسول (إصلاح MissingGreenlet)."""
    async with async_session_maker() as session:
        category = Category(
            name_ar="قسم تجريبي",
            emoji="📦",
            type=CategoryType.GAMES,
            is_active=True,
        )
        session.add(category)
        await session.flush()
        session.add_all(
            [
                SubCategory(
                    category_id=category.id,
                    name_ar="فرعي ١",
                    emoji="🎮",
                    is_active=True,
                ),
                SubCategory(
                    category_id=category.id,
                    name_ar="فرعي ٢",
                    emoji="🎯",
                    is_active=False,
                ),
            ]
        )
        await session.commit()

        # هذه الجملة كانت ترفع MissingGreenlet قبل الإصلاح عند بناء الكيبورد
        categories = await DynamicService.get_all_categories(session)
        keyboard = categories_list_kb(categories)

        row_texts = [
            btn.text for row in keyboard.inline_keyboard for btn in row
        ]
        assert any("(2)" in t for t in row_texts), row_texts

        # التأكد أن العلاقة محمّلة مسبقاً بالفعل (لا IO معلق)
        assert len(categories[0].sub_categories) == 2


@pytest.mark.asyncio
async def test_admin_sub_categories_list_no_lazy_io():
    """قائمة الأقسام الفرعية تعمل دون تحميل كسول (إصلاح MissingGreenlet)."""
    from database.models import Product, ProductFulfillmentType, ProductStatus

    async with async_session_maker() as session:
        category = Category(
            name_ar="قسم تجريبي ٢",
            emoji="🧩",
            type=CategoryType.SMM,
            is_active=True,
        )
        session.add(category)
        await session.flush()
        sub = SubCategory(
            category_id=category.id,
            name_ar="فرعي بمنتجات",
            emoji="📈",
            is_active=True,
        )
        session.add(sub)
        await session.flush()
        session.add_all(
            [
                Product(
                    sub_category_id=sub.id,
                    name_ar="منتج ١",
                    price_usd=1.5,
                    status=ProductStatus.ACTIVE,
                    fulfillment_type=ProductFulfillmentType.API,
                ),
                Product(
                    sub_category_id=sub.id,
                    name_ar="منتج ٢",
                    price_usd=2.5,
                    status=ProductStatus.INACTIVE,
                    fulfillment_type=ProductFulfillmentType.API,
                ),
            ]
        )
        await session.commit()

        subs = await DynamicService.get_all_sub_categories(session, category.id)
        keyboard = sub_categories_list_kb(category.id, subs)

        row_texts = [
            btn.text for row in keyboard.inline_keyboard for btn in row
        ]
        assert any("2 منتج" in t for t in row_texts), row_texts

        # العلاقة محمّلة مسبقاً بالفعل (لا IO معلق)
        assert len(subs[0].products) == 2
