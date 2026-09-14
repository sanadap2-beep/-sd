"""
قسم واتساب — واجهة المستخدم (القسم الرئيسي الثاني).

التدفق:
1) 📱 واتساب → وصف القسم + الحالة:
   - بلا اشتراك → باقات (1/3/7/30 يوم) → دفع من الرصيد.
   - مشترك وليس مربوطاً → «أرسل رقم واتساب» → كود ربط من الجسر.
   - مشترك ومربوط → قائمة البوت الثاني (أزراره الحقيقية من الجسر)
     وكل ضغطة تنفذ أمراً فيه وتعيد الرسم.
2) كل استخدام يفحص الاشتراك النشط، وكل أمر يمر بالجسر باسم
   Telegram user id نفسه — البوت الثاني هو مصدر الحقيقة.
3) العقد الموسّع (v2): أزرار تحتاج كتابة (``kind:"input"``)، وملفات وردود
   غنية (``files``/``buttons``)، وترقيم صفحات ``menu``. الأشكال القديمة
   (جسر بلا ``kind``/``files``) تشتغل كما هي.
"""

from __future__ import annotations

import base64
import logging
import re
import time
import uuid
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import User, WaLinkState
from services import wa_bridge_client
from services.balance_service import InsufficientBalanceError
from services.feature_service import FeatureService
from services.i18n_service import I18nService
from services.settings_service import SettingsService
from services.whatsapp_section_service import WhatsAppSectionService
from states.states import WaStates

logger = logging.getLogger(__name__)
router = Router(name="whatsapp")

_BUY_RE = re.compile(r"^wa:buy:(\d+)$")
_TOKEN = r"[a-f0-9]{8}"
_ACT_RE = re.compile(rf"^wa:act:({_TOKEN}):(\d+)$")
_PAGE_RE = re.compile(rf"^wa:page:({_TOKEN}):(\d+)$")
_MAX_MENU_ITEMS = 40

_esc = I18nService.t


def _lang(db_user) -> str:
    return getattr(db_user, "language_code", "ar") or "ar"


async def _feature_on() -> bool:
    return await FeatureService.enabled("whatsapp_section")


async def _section_description(language: str) -> str:
    return (await SettingsService.get("wa_section_description", "")) or (
        _esc("wa_default_description", language)
    )


def _fmt_until(until) -> str:
    return until.strftime("%Y-%m-%d %H:%M") if until else "—"


# ══════════════════ حالة القوائم (كاش مؤقت بـ TTL) ══════════════════


@dataclass
class _MenuState:
    """قائمة جلبت من الجسر لحظتها: الصفحات + عناصر هذه الصفحة."""

    tg_id: int
    items: list[dict]
    page: int = 0
    pages: int = 1
    status_text: str = ""
    expires_at: float = 0.0

    def expired(self) -> bool:
        """منتهية إذا لم تُحدَّد مدة أو تجاوزناها — تُعاد الجلب بدل زر ميت."""
        return not self.expires_at or time.monotonic() > self.expires_at


# token → الحالة. تُفقد عند إعادة التشغيل، لكن الواجهة تعيد الجلب تلقائياً
# فلا تنكسر الأزرار القديمة (المشكلة التي كانت في النسخة الأولى).
_MENU_STATES: dict[str, _MenuState] = {}


async def _save_menu(state_obj: _MenuState, ttl_minutes: int) -> str:
    token = uuid.uuid4().hex[:8]
    state_obj.expires_at = time.monotonic() + ttl_minutes * 60
    _MENU_STATES[token] = state_obj
    _prune_menu_states()
    return token


def _prune_menu_states(max_items: int = 2000) -> None:
    for key in [k for k, v in _MENU_STATES.items() if v.expired()]:
        _MENU_STATES.pop(key, None)
    while len(_MENU_STATES) > max_items:
        oldest = min(_MENU_STATES.items(), key=lambda kv: kv[1].expires_at)[0]
        _MENU_STATES.pop(oldest, None)


def _get_menu(token: str, tg_id: int) -> _MenuState | None:
    state_obj = _MENU_STATES.get(token)
    if state_obj is None or state_obj.expired() or state_obj.tg_id != tg_id:
        return None
    return state_obj


def _menu_kb(
    token: str,
    items: list[dict],
    language: str,
    page: int = 0,
    pages: int = 1,
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for row in extra_rows or []:
        rows.append(row)
    for index, item in enumerate(items[:_MAX_MENU_ITEMS]):
        label = str(item.get("label") or item.get("id") or f"#{index}").strip()[:48]
        if item.get("disabled"):
            rows.append([InlineKeyboardButton(text=f"🚫 {label}", callback_data="wa:noop")])
            continue
        kind = str(item.get("kind") or "action")
        if kind == "url" and item.get("url"):
            rows.append([InlineKeyboardButton(text=label, url=str(item["url"]))])
            continue
        prefix = "✍️ " if kind == "input" else ""
        rows.append(
            [InlineKeyboardButton(text=f"{prefix}{label}", callback_data=f"wa:act:{token}:{index}")]
        )
    if pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀️", callback_data=f"wa:page:{token}:{max(0, page - 1)}"
                )
            )
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="wa:noop"))
        if page + 1 < pages:
            nav.append(
                InlineKeyboardButton(
                    text="▶️", callback_data=f"wa:page:{token}:{page + 1}"
                )
            )
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(
                text=_esc("wa_refresh", language), callback_data="wa:refresh"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=_esc("wa_back_home", language), callback_data="wa:home"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _clear_wa_state(state: FSMContext) -> None:
    """يمسح حالة إدخال النص إذا خرج المستخدم من تسلسل واتساب (حتى لا يضيع
    أول نص بعده في غير موضعه)."""
    current = await state.get_state()
    if current and current.split(":", 1)[0] == "WaStates":
        await state.clear()


async def _require_active(session, db_user) -> bool:
    return WhatsAppSectionService.is_active(
        await WhatsAppSectionService.get_sub(session, db_user.id)
    )


# ══════════════════ الرد/الأزرار القادمة من الجسر ══════════════════


async def _send_result_files(event: CallbackQuery | Message, result: dict) -> None:
    """يرسل ملفات البوت الثاني (url أو content_b64) كمستندات."""
    # duck-typing: CallbackQuery له .message، وMessage هو نفسه.
    message = getattr(event, "message", None) or event
    if message is None:
        return
    for entry in result.get("files") or []:
        try:
            if entry.get("content_b64"):
                payload = base64.b64decode(entry["content_b64"], validate=True)
                document = BufferedInputFile(payload, filename=entry["name"])
            elif entry.get("url"):
                document = entry["url"]
            else:
                continue
            await message.answer_document(document=document)
        except Exception as exc:  # noqa: BLE001 — ملف لا يوقف الرد النصي
            logger.warning("تعذّر إرسال ملف من الجسر (%s): %s", entry.get("name"), exc)


def _result_kb(
    token: str | None,
    items: list[dict] | None,
    result: dict,
    language: str,
    page: int = 0,
    pages: int = 1,
) -> InlineKeyboardMarkup:
    extra: list[list[InlineKeyboardButton]] = []
    buttons = result.get("buttons") if result else None
    if buttons:
        extra.append(
            [
                InlineKeyboardButton(text=str(b["text"])[:32], url=str(b["url"])[:500])
                for b in buttons
            ]
        )
    if items:
        return _menu_kb(
            token or "", items, language, page=page, pages=pages, extra_rows=extra or None
        )
    b = InlineKeyboardBuilder()
    for btn in extra:
        for button in btn:
            b.button(text=button.text, url=button.url)
    b.button(text=_esc("wa_open_menu", language), callback_data="wa:menu", style="primary")
    b.button(text=_esc("wa_back_home", language), callback_data="wa:home")
    b.adjust(2 if extra else 1)
    return b.as_markup()


async def _reply_result(
    callback: CallbackQuery, result: dict, token: str | None, items: list[dict] | None,
    language: str, page: int = 0, pages: int = 1,
) -> None:
    text = (result.get("text") or "").strip() or _esc("wa_action_done", language)
    markup = _result_kb(token, items, result, language, page=page, pages=pages)
    if result.get("alert") and len(text) <= 200:
        await callback.answer(text[:200], show_alert=True)
        return
    if callback.message is None:
        await callback.message.answer(text[:4000], reply_markup=markup)
        return
    await _edit_or_answer(callback.message, text[:4000], markup)
    await _send_result_files(callback, result)


# ══════════════════ الشاشة الرئيسية للقسم ══════════════════


async def _edit_or_answer(message, text: str, reply_markup=None) -> None:
    """يعدّل الرسالة الحالية، وإن رفض تليجرام (نفس النص/رسالة قديمة) يرسل جديدة.

    أزرار القسم كثيرة إعادة الضغط (wa:home/wa:check_link/...)، وتعديل نص غير
    متغيّر يرمي TelegramBadRequest «message is not modified» فيظهر خطأ للمستخدم
    وهو لم يفعل شيئاً خاطئاً.
    """
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=reply_markup)


async def _show_home(callback: CallbackQuery, session, db_user: User | None = None):
    language = _lang(db_user)
    t = lambda key, **kw: _esc(key, language, **kw)  # noqa: E731
    sub = await WhatsAppSectionService.get_sub(session, db_user.id)
    active = WhatsAppSectionService.is_active(sub)
    description = await _section_description(language)
    bridge_ready = await wa_bridge_client.configured()

    lines = [f"📱 <b>{t('wa_section_title')}</b>\n"]
    if description:
        lines.append(f"{description}\n")
    if not bridge_ready:
        lines.append(f"\n🔴 {t('wa_bridge_unconfigured')}")

    b = InlineKeyboardBuilder()
    if not active:
        lines.append(f"\n💰 {t('wa_need_subscription')}")
        for pkg in await WhatsAppSectionService.packages():
            b.button(
                text=t("wa_package", days=pkg["days"], price=f"{pkg['price_usd']:g}$"),
                callback_data=f"wa:buy:{pkg['days']}",
                style="success",
                disabled=not bridge_ready,
            )
        if sub is not None:
            b.button(
                text=t("wa_autorenew_on") if sub.auto_renew else t("wa_autorenew_off"),
                callback_data="wa:autorenew",
            )
    else:
        lines.append(f"\n🟢 {t('wa_active_until', until=_fmt_until(sub.active_until))}")
        state = sub.link_state
        if state in (
            WaLinkState.NONE.value,
            WaLinkState.EXPIRED.value,
            WaLinkState.PENDING.value,
        ):
            if state == WaLinkState.PENDING.value:
                lines.append(f"\n⏳ {t('wa_link_pending')}")
            else:
                lines.append(f"\n🔗 {t('wa_not_linked')}")
            b.button(
                text=t("wa_send_phone"),
                callback_data="wa:send_phone",
                style="success",
                disabled=not bridge_ready,
            )
            if state == WaLinkState.PENDING.value:
                b.button(text=t("wa_check_link"), callback_data="wa:check_link", style="primary")
        else:
            lines.append(f"\n🔗 {t('wa_linked')}")
            if sub.connected_since:
                lines.append(f"   🕒 {sub.connected_since}")
            b.button(text=t("wa_open_menu"), callback_data="wa:menu", style="success")
            b.button(text=t("wa_unlink"), callback_data="wa:unlink")
        b.button(
            text=t("wa_autorenew_on") if sub.auto_renew else t("wa_autorenew_off"),
            callback_data="wa:autorenew",
        )

    b.button(text=t("wa_back_home"), callback_data="back_to_main")
    b.adjust(1)
    await _edit_or_answer(callback.message, "\n".join(lines), b.as_markup())
    await callback.answer()


@router.callback_query(F.data == "wa:noop")
async def wa_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "wa:home")
async def wa_home(
    callback: CallbackQuery, session, state: FSMContext, db_user: User | None = None
):
    if not await _feature_on():
        await callback.answer(_esc("wa_disabled", _lang(db_user)), show_alert=True)
        return
    if db_user is None:
        await callback.answer()
        return
    await _clear_wa_state(state)
    await _show_home(callback, session, db_user)


# ══════════════════ الباقات ══════════════════


@router.callback_query(_BUY_RE)
async def wa_buy(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(_esc("wa_disabled", _lang(db_user)), show_alert=True)
        return
    if db_user is None:
        await callback.answer()
        return
    if not await wa_bridge_client.configured():
        # لا يُعقل بيع اشتراك في قسم لا يعمل جسرُه.
        await callback.answer(
            _esc("wa_bridge_unconfigured", _lang(db_user)), show_alert=True
        )
        return
    days = int(callback.data.split(":")[2])
    package = next(
        (p for p in await WhatsAppSectionService.packages() if p["days"] == days), None
    )
    if package is None:
        await _show_home(callback, session, db_user)
        return
    language = _lang(db_user)
    try:
        await WhatsAppSectionService.purchase(
            session, db_user, days, package["price_usd"]
        )
    except InsufficientBalanceError:
        await callback.answer(
            _esc("wa_insufficient", language, price=f"{package['price_usd']:g}$"),
            show_alert=True,
        )
        return
    await _show_home(callback, session, db_user)
    await callback.answer(_esc("wa_purchase_ok", language))


@router.callback_query(F.data == "wa:autorenew")
async def wa_autorenew(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(_esc("wa_disabled", _lang(db_user)), show_alert=True)
        return
    if db_user is None:
        await callback.answer()
        return
    from database.models import WaSubscription

    sub = await WhatsAppSectionService.get_sub(session, db_user.id)
    if sub is None:
        sub = WaSubscription(user_id=db_user.id, auto_renew=False)
        session.add(sub)
        await session.flush()
    sub.auto_renew = not sub.auto_renew
    await session.commit()
    await _show_home(callback, session, db_user)
    await callback.answer()


# ══════════════════ ربط رقم الواتساب ══════════════════


@router.callback_query(F.data == "wa:send_phone")
async def wa_send_phone(
    callback: CallbackQuery, session, state: FSMContext, db_user: User | None = None
):
    if not await _feature_on():
        await callback.answer(_esc("wa_disabled", _lang(db_user)), show_alert=True)
        return
    if db_user is None:
        await callback.answer()
        return
    if not await _require_active(session, db_user):
        await _show_home(callback, session, db_user)
        return
    await state.set_state(WaStates.waiting_phone)
    await _edit_or_answer(callback.message, _esc("wa_phone_prompt", _lang(db_user)))
    await callback.answer()


@router.message(WaStates.waiting_phone, F.text)
async def wa_phone_received(
    message: Message, state: FSMContext, session, db_user: User | None = None
):
    from services.whatsapp_section_service import WaSectionError

    if db_user is None:
        await state.clear()
        return
    raw = (message.text or "").strip()
    if raw == "/cancel":
        await state.clear()
        await message.answer(_esc("wa_cancelled", _lang(db_user)))
        return
    phone = re.sub(r"[^\d+]", "", raw)
    if not re.fullmatch(r"\+?\d{8,15}", phone):
        await message.answer(_esc("wa_phone_invalid", _lang(db_user)))
        return
    if not await _require_active(session, db_user):
        await state.clear()
        await message.answer(_esc("wa_expired_notice", _lang(db_user)))
        return
    language = _lang(db_user)
    try:
        result = await WhatsAppSectionService.start_link(session, db_user, phone)
    except (wa_bridge_client.WaBridgeError, WaSectionError) as exc:
        await state.clear()
        await message.answer(f"⚠️ {exc}", reply_markup=_home_kb(language))
        return
    await state.clear()
    t = lambda key, **kw: _esc(key, language, **kw)  # noqa: E731
    text = (
        f"✅ {t('wa_link_started', phone=phone)}\n\n"
        f"🔑 <b>{t('wa_link_code')}</b>\n<code>{result['link_code']}</code>\n\n"
        + (f"{result['instructions']}\n" if result.get("instructions") else "")
        + f"\n{t('wa_link_wait')}"
    )
    b = InlineKeyboardBuilder()
    b.button(text=t("wa_check_link"), callback_data="wa:check_link", style="primary")
    b.button(text=t("wa_back_home"), callback_data="wa:home")
    b.adjust(2)
    await message.answer(text, reply_markup=b.as_markup())


@router.callback_query(F.data == "wa:check_link")
async def wa_check_link(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(_esc("wa_disabled", _lang(db_user)), show_alert=True)
        return
    if db_user is None:
        await callback.answer()
        return
    language = _lang(db_user)
    await callback.answer(_esc("wa_working", language))
    try:
        sub = await WhatsAppSectionService.check_link(session, db_user)
    except wa_bridge_client.WaBridgeError as exc:
        await callback.message.answer(f"⚠️ {exc}", reply_markup=_home_kb(language))
        return
    if sub.link_state == WaLinkState.LINKED.value:
        await _edit_or_answer(
            callback.message, _esc("wa_linked_ok", language), _menu_entry_kb(language)
        )
    else:
        await _edit_or_answer(
            callback.message, _esc("wa_link_pending", language), _home_kb(language)
        )


@router.callback_query(F.data == "wa:unlink")
async def wa_unlink(callback: CallbackQuery, session, db_user: User | None = None):
    """يفصل الجلسة عند البوت الثاني ويلغي الربط محلياً."""
    if not await _feature_on() or db_user is None:
        await callback.answer()
        return
    sub = await WhatsAppSectionService.get_sub(session, db_user.id)
    if sub is None:
        await callback.answer()
        return
    language = _lang(db_user)
    await callback.answer(_esc("wa_working", language))
    try:
        pushed = await WhatsAppSectionService.unlink(session, sub, db_user)
    except wa_bridge_client.WaBridgeError as exc:
        logger.warning("فشل unlink عند الجسر: %s", exc)
        pushed = False
    await _show_home(callback, session, db_user)
    await callback.answer(
        _esc("wa_unlink_done" if pushed else "wa_unlink_local", language)
    )


# ══════════════════ قائمة أزرار البوت الثاني ══════════════════


async def _render_menu(
    callback: CallbackQuery, session, db_user: User, page: int = 0
) -> bool:
    """يجلب قائمة الجسر ويعرضها. يرجع False عند فشل الجسر (قد عولج داخلياً)."""
    language = _lang(db_user)
    try:
        data = await wa_bridge_client.menu(db_user.telegram_id, page=page)
    except wa_bridge_client.WaBridgeError as exc:
        await callback.message.answer(
            f"⚠️ {exc}", reply_markup=_menu_entry_kb(language)
        )
        return False
    items = data["menu"]
    if not items:
        await _edit_or_answer(
            callback.message, _esc("wa_menu_empty", language), _home_kb(language)
        )
        return True
    ttl = await WhatsAppSectionService.menu_cache_ttl_minutes()
    state_obj = _MenuState(
        tg_id=db_user.telegram_id,
        items=items,
        page=data["page"],
        pages=data["pages"],
        status_text=data["status_text"],
    )
    token = await _save_menu(state_obj, ttl)
    text = _esc("wa_menu_header", language)
    if data["status_text"]:
        text += f"\n\n{data['status_text']}"
    await _edit_or_answer(
        callback.message,
        text[:4000],
        _menu_kb(token, items, language, data["page"], data["pages"]),
    )
    return True


@router.callback_query(F.data == "wa:menu")
async def wa_menu(callback: CallbackQuery, session, state: FSMContext, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(_esc("wa_disabled", _lang(db_user)), show_alert=True)
        return
    if db_user is None:
        await callback.answer()
        return
    await _clear_wa_state(state)
    if not await _require_active(session, db_user):
        await _show_home(callback, session, db_user)
        return
    await callback.answer(_esc("wa_working", _lang(db_user)))
    await _render_menu(callback, session, db_user)


@router.callback_query(F.data == "wa:refresh")
async def wa_refresh(
    callback: CallbackQuery, session, state: FSMContext, db_user: User | None = None
):
    """يعيد جلب قائمة البوت الثاني (تُحدَّث فور أي تغيير عنده)."""
    if not await _feature_on() or db_user is None:
        await callback.answer()
        return
    await _clear_wa_state(state)
    if not await _require_active(session, db_user):
        await _show_home(callback, session, db_user)
        return
    await callback.answer()
    await _render_menu(callback, session, db_user)


@router.callback_query(_PAGE_RE)
async def wa_page(callback: CallbackQuery, session, db_user: User | None = None):
    """تنقّل بين صفحات قائمة البوت الثاني (الجسر هو من يقسّمها)."""
    if not await _feature_on() or db_user is None:
        await callback.answer()
        return
    if not await _require_active(session, db_user):
        await _show_home(callback, session, db_user)
        return
    parts = callback.data.split(":")
    token, page = parts[2], int(parts[3])
    current = _get_menu(token, db_user.telegram_id)
    if current and page == current.page:
        await callback.answer()
        return
    await callback.answer()
    await _render_menu(callback, session, db_user, page=page)


@router.callback_query(_ACT_RE)
async def wa_action(callback: CallbackQuery, session, state: FSMContext, db_user: User | None = None):
    """ضغطة زر من قائمة البوت الثاني → تنفيذ عبر الجسر."""
    if not await _feature_on():
        await callback.answer()
        return
    if db_user is None:
        await callback.answer()
        return
    parts = callback.data.split(":")
    token, index = parts[2], int(parts[3])
    language = _lang(db_user)
    menu_state = _get_menu(token, db_user.telegram_id)
    if menu_state is None:
        # انتهت المدة أو أُعيد تشغيل البوت: نعيد الجلب بدل ترك الأزرار ميتة.
        await callback.answer(_esc("wa_menu_expired", language))
        if await _require_active(session, db_user):
            await _render_menu(callback, session, db_user)
        return
    if index >= len(menu_state.items):
        await callback.answer(_esc("wa_menu_expired", language))
        return
    item = menu_state.items[index]
    if item.get("disabled"):
        await callback.answer()
        return
    if not await _require_active(session, db_user):
        await _show_home(callback, session, db_user)
        return

    kind = str(item.get("kind") or "action")
    if kind == "url" and item.get("url"):
        await callback.answer()
        return
    if kind == "input":
        await state.set_state(WaStates.waiting_action_input)
        await state.update_data(wa_token=token, wa_index=index)
        prompt = str(item.get("prompt") or _esc("wa_input_prompt", language))
        b = InlineKeyboardBuilder()
        b.button(text=_esc("wa_cancel_input", language), callback_data="wa:cancel_input")
        await _edit_or_answer(
            callback.message,
            f"✍️ {prompt}\n\n{_esc('wa_input_hint', language)}",
            b.as_markup(),
        )
        await callback.answer()
        return

    # تنفيذ فوري — نرد على الضغط أولاً حتى لا تعلق «ساعة» تليجرام 45 ثانية.
    await callback.answer(_esc("wa_action_working", language))
    try:
        result = await wa_bridge_client.action(
            db_user.telegram_id, str(item.get("id")), item.get("data") or None
        )
    except wa_bridge_client.WaBridgeError as exc:
        await callback.message.answer(f"⚠️ {exc}", reply_markup=_menu_entry_kb(language))
        return
    await _dispatch_result(
        callback, result, token, menu_state, language, session, db_user, state
    )


async def _dispatch_result(
    callback: CallbackQuery,
    result: dict,
    token: str,
    menu_state: _MenuState,
    language: str,
    session,
    db_user: User,
    state: FSMContext | None = None,
) -> None:
    """يعرض رد الجسر: نص + ملفات + أزرار، ويحدّث الكاش لو جاء قائمة جديدة."""
    awaiting = result.get("awaiting_input")
    new_items = result.get("menu")
    if new_items:
        ttl = await WhatsAppSectionService.menu_cache_ttl_minutes()
        fresh = _MenuState(
            tg_id=db_user.telegram_id,
            items=new_items,
            page=int(result.get("page") or 0),
            pages=max(1, int(result.get("pages") or 1)),
            status_text=menu_state.status_text,
        )
        token = await _save_menu(fresh, ttl)
        menu_state = fresh
    if awaiting:
        # البوت الثاني ينتظر نصاً (رد على سؤاله) — نلتقطه من المستخدم.
        await callback.message.answer(
            f"✍️ {str(awaiting.get('prompt') or _esc('wa_input_prompt', language))}\n\n"
            f"{_esc('wa_input_hint', language)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=_esc("wa_cancel_input", language),
                            callback_data="wa:cancel_input",
                        )
                    ]
                ]
            ),
        )
        if state is not None:
            await state.set_state(WaStates.waiting_action_input)
            await state.update_data(wa_token=token, wa_index=-1)
    await _reply_result(
        callback,
        result,
        token if new_items else None,
        menu_state.items if new_items else None,
        language,
        page=menu_state.page,
        pages=menu_state.pages,
    )


@router.callback_query(F.data == "wa:cancel_input")
async def wa_cancel_input(callback: CallbackQuery, state: FSMContext, db_user: User | None = None):
    if db_user is None:
        await callback.answer()
        return
    await state.clear()
    await callback.answer(_esc("wa_cancelled", _lang(db_user)))
    await callback.message.answer(_esc("wa_menu_header", _lang(db_user)),
                                  reply_markup=_menu_entry_kb(_lang(db_user)))


@router.message(WaStates.waiting_action_input, F.text)
async def wa_input_received(
    message: Message, state: FSMContext, session, db_user: User | None = None
):
    """نص كتبه المستخدم لأمر ``kind:"input"`` → يُرسل للجسر كما هو."""
    from services.whatsapp_section_service import WaSectionError

    if db_user is None:
        await state.clear()
        return
    language = _lang(db_user)
    if not await _feature_on():
        await state.clear()
        await message.answer(_esc("wa_disabled", language))
        return
    if (message.text or "").strip() == "/cancel":
        await state.clear()
        await message.answer(_esc("wa_cancelled", language))
        return
    data = await state.get_data()
    token = data.get("wa_token")
    index = int(data.get("wa_index", -1))
    menu_state = _get_menu(token, db_user.telegram_id) if token else None
    payload: dict = {}
    if menu_state is not None and 0 <= index < len(menu_state.items):
        payload = menu_state.items[index].get("data") or {}
    if not await _require_active(session, db_user):
        await state.clear()
        await message.answer(_esc("wa_expired_notice", language))
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer(_esc("wa_input_prompt", language))
        return
    try:
        result = await wa_bridge_client.send_input(db_user.telegram_id, text, payload)
    except (wa_bridge_client.WaBridgeError, WaSectionError) as exc:
        await state.clear()
        await message.answer(f"⚠️ {exc}", reply_markup=_menu_entry_kb(language))
        return

    await state.clear()
    reply_text = (result.get("text") or "").strip() or _esc("wa_action_done", language)
    markup = None
    if result.get("menu"):
        ttl = await WhatsAppSectionService.menu_cache_ttl_minutes()
        fresh = _MenuState(
            tg_id=db_user.telegram_id,
            items=result["menu"],
            page=int(result.get("page") or 0),
            pages=max(1, int(result.get("pages") or 1)),
        )
        new_token = await _save_menu(fresh, ttl)
        markup = _menu_kb(new_token, fresh.items, language, fresh.page, fresh.pages)
    elif result.get("awaiting_input"):
        # البوت الثاني ما زال ينتظر (نموذج متعدد الخطوات) → نبقى في نفس الحالة.
        await state.set_state(WaStates.waiting_action_input)
        await state.update_data(wa_token=token, wa_index=index)
        markup = _menu_entry_kb(language)
    else:
        markup = _menu_entry_kb(language)
    try:
        await message.answer(reply_text[:4000], reply_markup=markup)
    except TelegramBadRequest:
        await message.answer(_esc("wa_action_done", language), reply_markup=markup)
    await _send_result_files(message, result)


def _home_kb(language: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=_esc("wa_check_link", language), callback_data="wa:check_link")
    b.button(text=_esc("wa_open_menu", language), callback_data="wa:menu", style="primary")
    b.button(text=_esc("wa_back_home", language), callback_data="wa:home")
    b.adjust(1)
    return b.as_markup()


def _menu_entry_kb(language: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text=_esc("wa_open_menu", language),
        callback_data="wa:menu",
        style="primary",
    )
    b.button(text=_esc("wa_back_home", language), callback_data="wa:home")
    b.adjust(1)
    return b.as_markup()
