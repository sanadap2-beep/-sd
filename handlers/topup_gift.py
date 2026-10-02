"""
«اشحن لأهلك» — تحويلات الشتات (ميزة mobile_topup_gift).

يختار المستخدم مشغّل الجوال في الوطن والمبلغ ورقم المستلم وملاحظة
اختيارية، فيصبح الطلب قيد المراجعة لدى الأدمن (لا يكتمل المشغّل تلقائياً).
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import TopupGiftRequest
from services.feature_service import FeatureService
from services.notification_service import NotificationService
from states.states import TopupGiftStates
from keyboards.admin import admin_topup_gift_decision_kb
from keyboards.main_menu import back_to_main_kb

logger = logging.getLogger(__name__)

router = Router(name="topup_gift")

OPERATOR_LABELS = {"mtn": "MTN", "syriatel": "سيرياتل"}

OPERATOR_QUANTITIES: dict[str, list[Decimal]] = {
    "syriatel": [
        Decimal("1.92"), Decimal("2.88"), Decimal("3.84"), Decimal("4.80"),
        Decimal("5.76"), Decimal("9.61"), Decimal("20.19"), Decimal("23.07"),
        Decimal("24.03"), Decimal("25.96"), Decimal("30.78"), Decimal("40.38"),
        Decimal("45.19"), Decimal("48.07"), Decimal("52.88"), Decimal("62.5"),
        Decimal("68.26"), Decimal("72.11"), Decimal("77.88"), Decimal("81.73"),
        Decimal("86.53"), Decimal("96.15"), Decimal("100.96"), Decimal("105.76"),
        Decimal("115.38"), Decimal("125"), Decimal("125.00"), Decimal("130.76"),
        Decimal("144.23"), Decimal("160.57"), Decimal("163.46"), Decimal("173.07"),
        Decimal("183.65"), Decimal("192.30"), Decimal("211.53"), Decimal("240.38"),
        Decimal("288.46"), Decimal("317.30"), Decimal("370.19"), Decimal("432.69"),
        Decimal("480.76"), Decimal("576.92"),
    ],
    "mtn": [
        Decimal("10"), Decimal("12"), Decimal("15"), Decimal("20"), Decimal("25"),
        Decimal("30"), Decimal("35"), Decimal("40"), Decimal("50"), Decimal("60"),
        Decimal("70"), Decimal("85"), Decimal("90"), Decimal("100"), Decimal("110"),
        Decimal("150"), Decimal("170"), Decimal("190"), Decimal("200"), Decimal("230"),
        Decimal("260"), Decimal("280"), Decimal("300"), Decimal("320"), Decimal("340"),
        Decimal("360"), Decimal("400"), Decimal("420"), Decimal("440"), Decimal("460"),
        Decimal("480"), Decimal("500"), Decimal("550"), Decimal("600"),
    ],
}

UNIT_PRICE = Decimal("0.25")


async def _enabled() -> bool:
    return await FeatureService.enabled("mobile_topup_gift")


async def _operators() -> list[str]:
    raw = await FeatureService.config("mobile_topup_gift", "operators", "mtn,syriatel")
    return [op.strip().lower() for op in str(raw).split(",") if op.strip()]


def _operator_kb(enabled_ops: list[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for op in enabled_ops:
        b.button(text=OPERATOR_LABELS.get(op, op), callback_data=f"topup:op:{op}", style="primary")
    b.button(text="🔙 رجوع", callback_data="extras:home")
    b.adjust(1)
    return b.as_markup()


def _quantity_kb(operator: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    quantities = OPERATOR_QUANTITIES.get(operator, [])
    for qty in quantities:
        label = f"{qty.normalize():f}" if qty == qty.to_integral() else f"{qty}f"
        b.button(text=label, callback_data=f"topup:qty:{operator}:{qty}", style="success")
    b.button(text="🔙 رجوع", callback_data="extras:topup_gift")
    b.adjust(3)
    return b.as_markup()


def _parse_amount(value: str | None) -> Decimal | None:
    try:
        raw = (value or "").replace(",", ".").strip()
        amount = Decimal(raw)
    except (InvalidOperation, AttributeError):
        return None
    if amount <= 0 or amount.is_nan():
        return None
    return amount


def _skip_note_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⏭ تخطّي الملاحظة", callback_data="topup:note_skip")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الدخول من «خدمات البوت الأخرى» ══════════════


@router.callback_query(F.data == "extras:topup_gift")
async def topup_gift_start(callback: CallbackQuery, state: FSMContext):
    if not await _enabled():
        await callback.answer("⏳ هذه الخدمة غير متاحة حالياً.", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    ops = await _operators()
    if not ops:
        await callback.answer("لا يوجد مشغّلون مضبوطون.", show_alert=True)
        return
    labels = "\n".join(f"• {OPERATOR_LABELS[op]}" for op in ops)
    await callback.message.edit_text(
        "🎁 <b>اشحن لأهلك</b>\n\n"
        "اختر مشغّل الجوال في الوطن الذي تريد شحنه للمستلم:\n\n"
        f"{labels}",
        reply_markup=_operator_kb(ops),
    )
    await state.set_state(TopupGiftStates.waiting_operator)


@router.callback_query(F.data.startswith("topup:op:"))
async def topup_operator_selected(callback: CallbackQuery, state: FSMContext):
    if not await _enabled():
        await callback.answer("⏳ غير متاحة حالياً.", show_alert=True)
        return
    operator = (callback.data or "").split(":", 2)[-1]
    ops = await _operators()
    if operator not in ops:
        await callback.answer("مشغّل غير متاح.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(operator=operator)
    quantities = OPERATOR_QUANTITIES.get(operator, [])
    if quantities:
        await callback.message.edit_text(
            f"🎁 <b>اشحن لأهلك — {OPERATOR_LABELS.get(operator, operator)}</b>\n\n"
            f"اختر <b>الكمية</b> التي تريد شحنها:\n"
            f"(سعر الوحدة: {UNIT_PRICE}$ لكل كمية)",
            reply_markup=_quantity_kb(operator),
        )
        await state.set_state(TopupGiftStates.waiting_quantity)
    else:
        min_amount = await FeatureService.config_decimal("mobile_topup_gift", "min_amount_usd", 1)
        max_amount = await FeatureService.config_int("mobile_topup_gift", "max_amount_usd", 100)
        await callback.message.edit_text(
            f"🎁 <b>اشحن لأهلك — {OPERATOR_LABELS.get(operator, operator)}</b>\n\n"
            f"أرسل <b>المبلغ بالدولار</b> الذي تريد شحنه:\n"
            f"(من {min_amount:.0f}$ إلى {max_amount}$)",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 رجوع", callback_data="extras:topup_gift")]]
            ),
        )
        await state.set_state(TopupGiftStates.waiting_amount)


@router.callback_query(F.data.startswith("topup:qty:"))
async def topup_quantity_selected(callback: CallbackQuery, state: FSMContext):
    if not await _enabled():
        await callback.answer("⏳ غير متاحة حالياً.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("⚠️ بيانات غير صالحة.", show_alert=True)
        return
    operator = parts[2]
    try:
        quantity = Decimal(parts[3])
    except (InvalidOperation, ValueError):
        await callback.answer("⚠️ كمية غير صالحة.", show_alert=True)
        return

    await callback.answer()
    await state.update_data(operator=operator, quantity=str(quantity))
    amount_usd = (quantity * UNIT_PRICE).quantize(Decimal("0.01"))
    await state.update_data(amount_usd=str(amount_usd))
    await state.set_state(TopupGiftStates.waiting_recipient)
    await callback.message.edit_text(
        f"📦 الكمية: <b>{quantity}</b>\n"
        f"💵 المبلغ: <b>{amount_usd}$</b>\n\n"
        "📱 الآن أرسل <b>رقم جوال المستلم</b> في الوطن:\n"
        "(مثال: 0933556677)",
    )


@router.message(TopupGiftStates.waiting_amount)
async def topup_amount_received(message: Message, state: FSMContext):
    if not await _enabled():
        await message.answer("⏳ هذه الخدمة غير متاحة حالياً.")
        await state.clear()
        return
    amount = _parse_amount(message.text)
    if amount is None:
        await message.answer("⚠️ أرسل رقماً صحيحاً بالدولار، مثال: 5")
        return
    min_amount = await FeatureService.config_decimal("mobile_topup_gift", "min_amount_usd", 1)
    max_amount = await FeatureService.config_int("mobile_topup_gift", "max_amount_usd", 100)
    if amount < min_amount or amount > max_amount:
        await message.answer(f"⚠️ المبلغ يجب أن يكون بين {min_amount:.0f}$ و {max_amount}$.")
        return

    await state.update_data(amount_usd=str(amount))
    await state.set_state(TopupGiftStates.waiting_recipient)
    await message.answer(
        f"💵 المبلغ: <b>{amount}$</b>\n\n"
        "📱 الآن أرسل <b>رقم جوال المستلم</b> في الوطن:\n"
        "(مثال: 0933556677)",
    )


@router.message(TopupGiftStates.waiting_recipient)
async def topup_recipient_received(message: Message, state: FSMContext):
    if not await _enabled():
        await message.answer("⏳ غير متاحة حالياً.")
        await state.clear()
        return
    recipient = (message.text or "").strip()
    if not recipient.isdigit() or not (8 <= len(recipient) <= 14):
        await message.answer("⚠️ أرسل رقم جوال صحيح (أرقام فقط، من 8 إلى 14 خانة).")
        return

    await state.update_data(recipient_number=recipient)
    await state.set_state(TopupGiftStates.waiting_note)
    await message.answer(
        f"📱 رقم المستلم: <code>{recipient}</code>\n\n"
        "💬 أرسل <b>ملاحظة اختيارية</b> للأدمن (اسم المستلم، المنطقة...)\n"
        "أو اضغط «تخطّي الملاحظة»:",
        reply_markup=_skip_note_kb(),
    )


@router.callback_query(F.data == "topup:note_skip")
async def topup_note_skipped(callback: CallbackQuery, state: FSMContext, session):
    if not await _enabled():
        await callback.answer("⏳ غير متاحة حالياً.", show_alert=True)
        return
    await state.update_data(note="")
    await _submit_topup_request(callback.message, state, session, callback.from_user.id, callback.bot)
    await callback.answer()


@router.message(TopupGiftStates.waiting_note)
async def topup_note_received(message: Message, state: FSMContext, session, db_user, bot):
    if not await _enabled():
        await message.answer("⏳ غير متاحة حالياً.")
        await state.clear()
        return
    note = (message.text or "").strip()[:255]
    await state.update_data(note=note)
    await _submit_topup_request(message, state, session, db_user.id, bot)


async def _submit_topup_request(target, state: FSMContext, session, user_id: int, bot):
    data = await state.get_data()
    operator = data.get("operator", "")
    amount_usd = Decimal(data.get("amount_usd", "0"))
    quantity = Decimal(data.get("quantity", "1"))
    recipient_number = data.get("recipient_number", "")
    note = data.get("note", "") or ""

    request = TopupGiftRequest(
        user_id=user_id,
        operator=operator,
        recipient_number=recipient_number,
        quantity=quantity,
        amount_usd=amount_usd,
        note=note or None,
        status="pending",
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)

    text = (
        "🎁 <b>طلب «اشحن لأهلك» جديد</b>\n\n"
        f"🆔 رقم الطلب: #{request.id}\n"
        f"📡 المشغّل: <b>{OPERATOR_LABELS.get(operator, operator)}</b>\n"
        f"📦 الكمية: <b>{quantity}</b>\n"
        f"💵 المبلغ: <b>{amount_usd}$</b>\n"
        f"📱 رقم المستلم: <code>{recipient_number}</code>\n"
    )
    if note:
        text += f"💬 الملاحظة: {note}\n"
    text += f"⏰ الوقت: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"

    notifier = NotificationService(bot)
    sent_msg_id = await notifier.notify_admin(
        text,
        reply_markup=admin_topup_gift_decision_kb(request.id),
    )
    if sent_msg_id:
        request.admin_chat_message_id = sent_msg_id
        await session.commit()

    await target.answer(
        "✅ تم إرسال طلب الشحن بنجاح!\n\n"
        f"📡 المشغّل: <b>{OPERATOR_LABELS.get(operator, operator)}</b>\n"
        f"📦 الكمية: <b>{quantity}</b>\n"
        f"💵 المبلغ: <b>{amount_usd}$</b>\n"
        f"📱 رقم المستلم: <code>{recipient_number}</code>\n\n"
        "⏳ بانتظار موافقة الإدارة وسيُشحن خلال وقت قصير.\n"
        "سيصلك إشعار فور التنفيذ.",
        reply_markup=back_to_main_kb(),
    )
    await state.clear()