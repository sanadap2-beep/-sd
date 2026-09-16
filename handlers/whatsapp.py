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
"""

from __future__ import annotations

import logging
import re
import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import User, WaLinkState, WaSubscription
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
_ACT_RE = re.compile(r"^wa:act:([a-f0-9]{8}):(\d+)$")

# كاش مؤقت لقوائم الجسر: token → (telegram_id, [items])
# (يُفقد عند إعادة التشغيل — المستخدم يضغط «تحديث» ويعود كل شيء)
_menu_cache: dict[str, tuple[int, list[dict]]] = {}


def _lang(db_user) -> str:
    return getattr(db_user, "language_code", "ar") or "ar"


async def _feature_on() -> bool:
    return await FeatureService.enabled("whatsapp_section")


async def _section_description(language: str) -> str:
    return (await SettingsService.get("wa_section_description", "")) or (
        I18nService.t("wa_default_description", language)
    )


def _fmt_until(until) -> str:
    return until.strftime("%Y-%m-%d %H:%M") if until else "—"


async def _save_menu(token: str, tg_id: int, items: list[dict]) -> None:
    _menu_cache[token] = (tg_id, items)
    # حد بسيط للحجم.
    if len(_menu_cache) > 2000:
        oldest = list(_menu_cache.keys())[:500]
        for key in oldest:
            _menu_cache.pop(key, None)


def _menu_kb(token: str, items: list[dict], language: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, item in enumerate(items[:40]):
        label = str(item.get("label") or item.get("id") or f"#{index}").strip()[:48]
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"wa:act:{token}:{index}")]
        )
    rows.append(
        [InlineKeyboardButton(text=I18nService.t("wa_refresh", language), callback_data="wa:refresh")]
    )
    rows.append(
        [InlineKeyboardButton(text=I18nService.t("wa_back_home", language), callback_data="wa:home")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_home(callback: CallbackQuery, session, db_user: User):
    language = _lang(db_user)
    t = lambda key, **kw: I18nService.t(key, language, **kw)  # noqa: E731
    sub = await WhatsAppSectionService.get_sub(session, db_user.id)
    active = WhatsAppSectionService.is_active(sub)
    description = await _section_description(language)

    lines = [f"📱 <b>{t('wa_section_title')}</b>\n"]
    if description:
        lines.append(f"{description}\n")

    b = InlineKeyboardBuilder()
    if not active:
        lines.append(f"\n💰 {t('wa_need_subscription')}")
        for pkg in WhatsAppSectionService.packages():
            b.button(
                text=t(
                    "wa_package",
                    days=pkg["days"],
                    price=f"{pkg['price_usd']:g}$",
                ),
                callback_data=f"wa:buy:{pkg['days']}",
                style="success",
            )
        if sub is not None:
            b.button(
                text=(
                    t("wa_autorenew_on")
                    if sub.auto_renew
                    else t("wa_autorenew_off")
                ),
                callback_data="wa:autorenew",
            )
    else:
        lines.append(f"\n🟢 {t('wa_active_until', until=_fmt_until(sub.active_until))}")
        state = sub.link_state if sub else WaLinkState.NONE.value
        if state in (WaLinkState.NONE.value, WaLinkState.EXPIRED.value, WaLinkState.PENDING.value):
            lines.append(f"\n🔗 {t('wa_not_linked')}")
            b.button(
                text=t("wa_send_phone"),
                callback_data="wa:send_phone",
                style="success",
            )
        else:
            lines.append(f"\n🔗 {t('wa_linked')}")
            b.button(
                text=t("wa_open_menu"),
                callback_data="wa:menu",
                style="success",
            )
        b.button(
            text=(
                t("wa_autorenew_on") if sub.auto_renew else t("wa_autorenew_off")
            ),
            callback_data="wa:autorenew",
        )

    b.button(text=t("wa_back_home"), callback_data="back_to_main")
    b.adjust(1)
    await callback.message.edit_text("\n".join(lines), reply_markup=b.as_markup())
    await callback.answer()


@router.callback_query(F.data == "wa:home")
async def wa_home(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(I18nService.t("wa_disabled", _lang(db_user)), show_alert=True)
        return
    await _show_home(callback, session, db_user)


@router.callback_query(F.data.regexp(_BUY_RE))
async def wa_buy(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(I18nService.t("wa_disabled", _lang(db_user)), show_alert=True)
        return
    days = int(callback.data.split(":")[2])
    package = next(
        (p for p in WhatsAppSectionService.packages() if p["days"] == days), None
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
            I18nService.t("wa_insufficient", language, price=f"{package['price_usd']:g}$"),
            show_alert=True,
        )
        return
    await _show_home(callback, session, db_user)
    await callback.answer(I18nService.t("wa_purchase_ok", language))


@router.callback_query(F.data == "wa:autorenew")
async def wa_autorenew(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(I18nService.t("wa_disabled", _lang(db_user)), show_alert=True)
        return
    sub = await WhatsAppSectionService.get_sub(session, db_user.id)
    if sub is None:
        sub = WaSubscription(user_id=db_user.id)
        session.add(sub)
        await session.flush()
    sub.auto_renew = not sub.auto_renew
    await session.commit()
    await _show_home(callback, session, db_user)
    await callback.answer()


@router.callback_query(F.data == "wa:send_phone")
async def wa_send_phone(callback: CallbackQuery, session, state: FSMContext, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(I18nService.t("wa_disabled", _lang(db_user)), show_alert=True)
        return
    if not WhatsAppSectionService.is_active(
        await WhatsAppSectionService.get_sub(session, db_user.id)
    ):
        await _show_home(callback, session, db_user)
        return
    await state.set_state(WaStates.waiting_phone)
    await callback.message.edit_text(
        I18nService.t("wa_phone_prompt", _lang(db_user))
    )
    await callback.answer()


@router.message(WaStates.waiting_phone, F.text)
async def wa_phone_received(message: Message, state: FSMContext, session, db_user: User | None = None):
    from services.whatsapp_section_service import WaSectionError

    if db_user is None:
        await state.clear()
        return
    raw = (message.text or "").strip()
    phone = re.sub(r"[^\d+]", "", raw)
    if not re.fullmatch(r"\+?\d{8,15}", phone):
        await message.answer(I18nService.t("wa_phone_invalid", _lang(db_user)))
        return
    if not WhatsAppSectionService.is_active(
        await WhatsAppSectionService.get_sub(session, db_user.id)
    ):
        await state.clear()
        await message.answer(I18nService.t("wa_expired_notice", _lang(db_user)))
        return
    language = _lang(db_user)
    try:
        result = await WhatsAppSectionService.start_link(session, db_user, phone)
    except (wa_bridge_client.WaBridgeError, WaSectionError) as exc:
        await state.clear()
        await message.answer(f"⚠️ {exc}", reply_markup=_home_kb(language))
        return
    await state.clear()
    t = lambda key, **kw: I18nService.t(key, language, **kw)  # noqa: E731
    text = (
        f"✅ {t('wa_link_started', phone=phone)}\n\n"
        f"🔑 <b>{t('wa_link_code')}</b>\n<code>{result['link_code']}</code>\n\n"
        + (f"\n{result['instructions']}\n" if result.get("instructions") else "")
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
        await callback.answer(I18nService.t("wa_disabled", _lang(db_user)), show_alert=True)
        return
    language = _lang(db_user)
    try:
        sub = await WhatsAppSectionService.check_link(session, db_user)
    except wa_bridge_client.WaBridgeError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    if sub.link_state == WaLinkState.LINKED.value:
        await callback.message.edit_text(t("wa_linked"), reply_markup=_menu_entry_kb(language))
    else:
        await callback.message.edit_text(
            t("wa_link_pending"), reply_markup=_home_kb(language)
        )
    await callback.answer()


@router.callback_query(F.data == "wa:menu")
async def wa_menu(callback: CallbackQuery, session, db_user: User | None = None):
    if not await _feature_on():
        await callback.answer(I18nService.t("wa_disabled", _lang(db_user)), show_alert=True)
        return
    if not WhatsAppSectionService.is_active(
        await WhatsAppSectionService.get_sub(session, db_user.id)
    ):
        await _show_home(callback, session, db_user)
        return
    language = _lang(db_user)
    try:
        data = await wa_bridge_client.menu(db_user.telegram_id)
    except wa_bridge_client.WaBridgeError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    items = data.get("menu") or []
    if not items:
        await callback.message.edit_text(
            I18nService.t("wa_menu_empty", language),
            reply_markup=_home_kb(language),
        )
        await callback.answer()
        return
    token = uuid.uuid4().hex[:8]
    await _save_menu(token, db_user.telegram_id, items)
    text = I18nService.t("wa_menu_header", language)
    if data.get("status_text"):
        text += f"\n\n{data['status_text']}"
    await callback.message.edit_text(text, reply_markup=_menu_kb(token, items, language))
    await callback.answer()


@router.callback_query(F.data == "wa:refresh")
async def wa_refresh(callback: CallbackQuery, session, db_user: User | None = None):
    """يعيد جلب قائمة البوت الثاني (تُحدَّث فور أي تغيير)."""
    if not await _feature_on():
        await callback.answer()
        return
    await wa_menu(callback, session, db_user)


@router.callback_query(F.data.regexp(_ACT_RE))
async def wa_action(callback: CallbackQuery, session, db_user: User | None = None):
    """ضغطة زر من قائمة البوت الثاني → تنفيذ عبر الجسر."""
    if not await _feature_on():
        await callback.answer()
        return
    _token, index = (
        callback.data.split(":")[2],
        int(callback.data.split(":")[3]),
    )
    cached = _menu_cache.get(_token)
    if cached is None or cached[0] != db_user.telegram_id:
        await callback.answer(I18nService.t("wa_menu_expired", _lang(db_user)), show_alert=True)
        return
    items = cached[1]
    if index >= len(items):
        await callback.answer("؟", show_alert=True)
        return
    item = items[index]
    if not WhatsAppSectionService.is_active(
        await WhatsAppSectionService.get_sub(session, db_user.id)
    ):
        await _show_home(callback, session, db_user)
        return
    language = _lang(db_user)
    await callback.answer(I18nService.t("wa_action_working", language))
    try:
        result = await wa_bridge_client.action(
            db_user.telegram_id, str(item.get("id")), item.get("data") or None
        )
    except wa_bridge_client.WaBridgeError as exc:
        await callback.message.answer(
            f"⚠️ {exc}", reply_markup=_menu_entry_kb(language)
        )
        return

    new_menu = result.get("menu")
    if new_menu:
        token = uuid.uuid4().hex[:8]
        await _save_menu(token, db_user.telegram_id, new_menu)
        kb = _menu_kb(token, new_menu, language)
    else:
        kb = _menu_entry_kb(language)

    text = result.get("text") or I18nService.t("wa_action_done", language)
    try:
        await callback.message.edit_text(text[:4000], reply_markup=kb)
    except Exception:  # noqa: BLE001 — الرسالة قد تكون أقدم من 48 ساعة
        await callback.message.answer(text[:4000], reply_markup=kb)


def _home_kb(language: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=I18nService.t("wa_check_link", language), callback_data="wa:check_link")
    b.button(text=I18nService.t("wa_back_home", language), callback_data="wa:home")
    b.adjust(1)
    return b.as_markup()


def _menu_entry_kb(language: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text=I18nService.t("wa_open_menu", language),
        callback_data="wa:menu",
        style="primary",
    )
    b.button(text=I18nService.t("wa_back_home", language), callback_data="wa:home")
    b.adjust(1)
    return b.as_markup()
