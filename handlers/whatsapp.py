"""
واجهة المستخدم لقسم واتساب.

المسار: زر «📱 واتساب» في القائمة الرئيسية ← شاشة القسم.

- القسم يتطلب اشتراكاً يومياً (سعره من اللوحة، افتراضي 1$).
- الربط: المستخدم يرسل رقم واتساب ← الجسر (البوت الثاني) يعيد كود اقتران
  ← المستخدم يدخله في تطبيق واتساب (الأجهزة المرتبطة ← ربط جهاز).
- بعد الربط تظهر أزرار البوت الثاني داخل هذا البوت: نعرض القائمة التي
  يرجعها الجسر ونمرّر كل ضغطة إليه — كل أوامر البوت الثاني تعمل من هنا.
"""

from __future__ import annotations

import html as html_module

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User, WALinkStatus, WhatsAppLink
from keyboards.ai_sections import (
    wa_bridge_menu_kb,
    wa_home_active_kb,
    wa_home_inactive_kb,
    wa_pairing_kb,
    wa_status_kb,
)
from services.balance_service import InsufficientBalanceError
from services.whatsapp_bridge_service import (
    WABridgeError,
    WALinkService,
    WASubscriptionService,
    WASettings,
)
from states.states import WhatsAppStates

router = Router(name="whatsapp")


def _fmt_until(until) -> str:
    return until.strftime("%Y-%m-%d %H:%M") if until else "—"




# ══════════════ الشاشة الرئيسية للقسم ══════════════


async def _render_wa_home(message: Message, session, db_user: User) -> None:
    """يرسم شاشة قسم واتساب (تُستخدم من أكثر من مسار)."""
    link = await WALinkService.get_link(session, db_user.id)
    has_link = link is not None and link.status in (WALinkStatus.PENDING, WALinkStatus.LINKED)

    if not await WASettings.configured():
        await message.edit_text(
            "📱 <b>قسم واتساب</b>\n\nالقسم قيد التجهيز حالياً. راجع الإدارة.",
            reply_markup=wa_home_inactive_kb(has_link),
        )
        return

    paid_until = await WASubscriptionService.active_until(session, db_user.id)
    admin_description = await WASettings.description()

    if paid_until:
        text_parts = ["📱 <b>قسم واتساب</b>"]
        if admin_description:
            text_parts.extend(["", html_module.escape(admin_description)])
        text_parts.extend([
            "",
            f"✅ اشتراكك فعال حتى: <b>{_fmt_until(paid_until)}</b>",
        ])
        if link and link.status == WALinkStatus.LINKED:
            text_parts.append(f"🔗 الرقم المرتبط: <code>{html_module.escape(link.phone)}</code>")
        elif link and link.status == WALinkStatus.PENDING:
            text_parts.append("⏳ عندك كود اقتران قيد الانتظار — كمّل الربط أو اطلب رقماً جديداً.")
        kb = wa_home_active_kb()
    else:
        price = await WASettings.daily_price()
        text_parts = ["📱 <b>قسم واتساب</b>"]
        if admin_description:
            text_parts.extend(["", html_module.escape(admin_description)])
        text_parts.extend([
            "",
            f"💳 استخدام هذا القسم يتطلب اشتراكاً يومياً: <b>${price}</b> / يوم.",
            "الاشتراك يتيح لك ربط رقم واتساب واستخدام كل أوامر البوت طوال اليوم.",
        ])
        kb = wa_home_inactive_kb(has_link)

    await message.edit_text("\n".join(text_parts), reply_markup=kb)


@router.callback_query(F.data == "wa:home")
async def wa_home(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    await state.clear()
    if not await WASettings.enabled():
        await callback.answer("قسم واتساب معطّل حالياً.", show_alert=True)
        return
    await _render_wa_home(callback.message, session, db_user)
    await callback.answer()


# ══════════════ الاشتراك اليومي ══════════════


@router.callback_query(F.data == "wa:subscribe")
async def wa_subscribe(callback: CallbackQuery, session, db_user: User):
    if not await WASettings.enabled():
        await callback.answer("قسم واتساب معطّل حالياً.", show_alert=True)
        return
    try:
        sub, amount = await WASubscriptionService.subscribe(session, db_user, days=1)
    except InsufficientBalanceError as exc:
        await callback.answer(f"❌ {exc}", show_alert=True)
        return
    except WABridgeError as exc:
        await callback.answer(f"❌ {exc}", show_alert=True)
        return
    await callback.answer(f"✅ تم الاشتراك! خصم ${amount} — فعال حتى {_fmt_until(sub.paid_until)}", show_alert=True)
    await wa_home(callback, session=session, db_user=db_user, state=None)


# ══════════════ ربط رقم واتساب ══════════════


@router.callback_query(F.data == "wa:link")
async def wa_link_start(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    paid_until = await WASubscriptionService.active_until(session, db_user.id)
    if paid_until is None:
        await callback.answer("اشترك أولاً ليوم واحد ثم اربط رقمك.", show_alert=True)
        return
    if not await WASettings.configured():
        await callback.answer("جسر واتساب غير مضبوط. راجع الإدارة.", show_alert=True)
        return
    await state.set_state(WhatsAppStates.waiting_phone)
    await state.update_data(subscribed_until=_fmt_until(paid_until))
    await callback.message.edit_text(
        "📲 <b>ربط رقم واتساب</b>\n\n"
        "أرسل رقمك بالصيغة الدولية بدون مسافات، مثال:\n"
        "<code>+963955123456</code>\n\n"
        "⚠️ تأكد أن الرقم هو نفسه الذي ستدخل به كود الاقتران في واتساب.\n"
        "❌ للإلغاء اضغط زر الرجوع بالأسفل أو أرسل /start.",
        reply_markup=wa_home_active_kb(),
    )
    await callback.answer()


@router.message(WhatsAppStates.waiting_phone, F.text)
async def wa_phone_received(message: Message, state: FSMContext, session, db_user: User):
    raw = (message.text or "").strip()
    if raw.lower() in ("/start", "الغاء", "إلغاء", "cancel"):
        await state.clear()
        return
    try:
        link = await WALinkService.start_pairing(session, db_user.id, raw)
    except WABridgeError as exc:
        await message.answer(f"❌ {exc}\n\nجرّب إرسال الرقم مرة أخرى أو أرسل /start للإلغاء.")
        return

    await state.clear()
    code_line = (
        f"\n🔑 <b>كود الاقتران:</b> <code>{html_module.escape(link.pairing_code)}</code>"
        if link.pairing_code
        else "\n🔑 لم يصل كود بعد — اضغط «فحص الحالة» بعد لحظات."
    )
    await message.answer(
        "📲 <b>خطوات الربط</b>\n"
        f"{code_line}\n\n"
        "1️⃣ افتح واتساب على جهازك\n"
        "2️⃣ الإعدادات ← <b>الأجهزة المرتبطة</b>\n"
        "3️⃣ اختر <b>ربط جهاز</b>\n"
        "4️⃣ اضغط «<b>ربط برقم الهاتف بدلاً من ذلك</b>» وأدخل الكود أعلاه\n\n"
        "بعد ثوانٍ اضغط «فحص الحالة» وستشتغل أزرار البوت هنا مباشرة 👇",
        reply_markup=wa_pairing_kb(),
    )


# ══════════════ حالة الاتصال ══════════════


@router.callback_query(F.data == "wa:status")
async def wa_status(callback: CallbackQuery, session, db_user: User):
    paid_until = await WASubscriptionService.active_until(session, db_user.id)
    if paid_until is None:
        await callback.answer("اشترك أولاً ليوم واحد.", show_alert=True)
        return
    link = await WALinkService.get_link(session, db_user.id)
    if link is None or not link.bridge_session_id:
        await callback.message.edit_text(
            "🔌 لا يوجد رقم مربوط. اربط رقمك أولاً 👇",
            reply_markup=wa_status_kb(WALinkStatus.DISCONNECTED),
        )
        await callback.answer()
        return
    try:
        status = await WALinkService.refresh_status(session, link)
    except WABridgeError as exc:
        await callback.answer(f"❌ {exc}", show_alert=True)
        return

    labels = {
        WALinkStatus.PENDING: "⏳ بانتظار إدخال كود الاقتران في واتساب",
        WALinkStatus.LINKED: "✅ الجلسة مربوطة وتعمل",
        WALinkStatus.EXPIRED: "⌛ انتهت صلاحية كود الاقتران — اطلب كوداً جديداً",
        WALinkStatus.DISCONNECTED: "❌ الجلسة غير متصلة — اربط الرقم من جديد",
    }
    await callback.message.edit_text(
        "🔌 <b>حالة الاتصال</b>\n\n"
        f"📱 الرقم: <code>{html_module.escape(link.phone)}</code>\n"
        f"الحالة: {labels.get(status, status.value)}\n"
        f"📅 الاشتراك فعال حتى: <b>{_fmt_until(paid_until)}</b>",
        reply_markup=wa_status_kb(status),
    )
    await callback.answer()


# ══════════════ أزرار البوت الثاني (الجسر) ══════════════


@router.callback_query(F.data == "wa:menu")
async def wa_menu(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    await state.clear()
    paid_until = await WASubscriptionService.active_until(session, db_user.id)
    if paid_until is None:
        await callback.answer("اشترك أولاً ليوم واحد.", show_alert=True)
        return
    link = await WALinkService.get_link(session, db_user.id)
    if link is None or not link.bridge_session_id:
        await callback.answer("اربط رقمك أولاً.", show_alert=True)
        return
    if link.status != WALinkStatus.LINKED:
        try:
            link.status = await WALinkService.refresh_status(session, link)
        except WABridgeError as exc:
            await callback.answer(f"❌ {exc}", show_alert=True)
            return
        if link.status != WALinkStatus.LINKED:
            await callback.answer("الجلسة غير مربوطة بعد — أكمل إدخال الكود في واتساب.", show_alert=True)
            return

    try:
        data = await WALinkService.run_command(session, link, action="menu")
    except WABridgeError as exc:
        await callback.answer(f"❌ {exc}", show_alert=True)
        return

    text = (data.get("text") or "").strip() or "🧭 قائمة أوامر واتساب:"
    kb = wa_bridge_menu_kb(link)
    if not WALinkService.parse_buttons(link):  # الجسر لم يرجع أزراراً
        text += "\n\n(البوت الثاني لم يرسل أزراراً — راجع إعداد قائمته)"
    await callback.message.edit_text(
        f"🧭 <b>أوامر واتساب</b>\n\n{html_module.escape(text[:3500])}",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("wa:go:"))
async def wa_bridge_action(callback: CallbackQuery, session, db_user: User):
    _, _, link_id, index = callback.data.split(":")
    link = await WALinkService.get_link(session, db_user.id)
    if link is None or str(link.id) != link_id:
        await callback.answer("الجلسة قديمة، افتح القائمة من جديد.", show_alert=True)
        return
    buttons = WALinkService.parse_buttons(link)
    try:
        position = int(index)
        action = buttons[position]["action"]
    except (IndexError, ValueError, KeyError):
        await callback.answer("الزر غير معروف، حدّث القائمة.", show_alert=True)
        return

    try:
        data = await WALinkService.run_command(session, link, action=action)
    except WABridgeError as exc:
        await callback.answer(f"❌ {exc}", show_alert=True)
        return

    text = (data.get("text") or "").strip() or "✅ تم التنفيذ."
    kb = wa_bridge_menu_kb(link)
    await callback.message.edit_text(
        f"{html_module.escape(text[:3500])}",
        reply_markup=kb,
    )
    await callback.answer()


# ══════════════ فصل الرقم ══════════════


@router.callback_query(F.data == "wa:unlink")
async def wa_unlink(callback: CallbackQuery, session, db_user: User):
    link = await WALinkService.get_link(session, db_user.id)
    if link is None:
        await callback.answer("لا يوجد رقم مربوط.", show_alert=True)
        return
    await WALinkService.unlink(session, link)
    await callback.answer("🔓 تم فصل الرقم.", show_alert=True)
    await wa_home(callback, session=session, db_user=db_user, state=None)
