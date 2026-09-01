"""إنشاء وإلغاء بطاقات الهدايا من لوحة الأدمن."""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import desc, select

from database.models import GiftCode
from filters.admin_filter import IsAdmin
from keyboards.admin import admin_back_kb
from keyboards.gift import (
    admin_gift_codes_kb,
    admin_gift_max_uses_kb,
    admin_gift_recent_kb,
)
from services.gift_service import GiftCodeError, GiftService
from states.states import AdminGiftStates

router = Router(name="admin_gift_codes")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:gift_codes")
async def gift_codes_menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🎁 <b>بطاقات الهدايا</b>\n\nأنشئ بطاقات رصيد قابلة للتوزيع على العملاء والمسوقين.",
        reply_markup=admin_gift_codes_kb(),
    )


@router.callback_query(F.data == "admin:gift_create")
async def gift_create_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AdminGiftStates.waiting_amount)
    await callback.answer()
    await callback.message.edit_text(
        "🎁 أرسل قيمة بطاقة الهدية بالدولار (مثال: 5):",
        reply_markup=admin_back_kb(),
    )


@router.message(AdminGiftStates.waiting_amount)
async def gift_amount_received(message: Message, state: FSMContext):
    try:
        amount = Decimal((message.text or "").strip())
        if not amount.is_finite() or amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("⚠️ أرسل مبلغاً موجباً صحيحاً.")
        return
    await state.update_data(amount_usd=str(amount))
    await message.answer(
        "🔢 اختر عدد مرات استخدام البطاقة:",
        reply_markup=admin_gift_max_uses_kb(),
    )
    await state.set_state(AdminGiftStates.waiting_max_uses)


@router.callback_query(F.data.startswith("admin:gift_uses:"))
async def gift_max_uses_selected(callback: CallbackQuery, state: FSMContext):
    try:
        max_uses = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("⚠️ قيمة غير صالحة.", show_alert=True)
        return
    await state.update_data(max_uses=max_uses)
    await state.set_state(AdminGiftStates.waiting_expires_days)
    await callback.answer()
    await callback.message.edit_text(
        "📅 أرسل مدة صلاحية البطاقة بالأيام، أو 0 بدون انتهاء:",
        reply_markup=admin_back_kb(),
    )


@router.message(AdminGiftStates.waiting_expires_days)
async def gift_expiry_received(
    message: Message,
    state: FSMContext,
    session,
    db_user,
):
    try:
        days = int((message.text or "").strip())
        if days < 0 or days > 3650:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ أرسل عدداً بين 0 و3650.")
        return
    data = await state.get_data()
    expires_at = datetime.utcnow() + timedelta(days=days) if days else None
    try:
        gift = await GiftService.create(
            session,
            Decimal(data["amount_usd"]),
            int(data["max_uses"]),
            db_user.id,
            expires_at,
        )
    except GiftCodeError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.clear()
    await message.answer(
        "✅ <b>تم إنشاء بطاقة الهدية</b>\n\n"
        f"🔑 الكود: <code>{gift.code}</code>\n"
        f"💰 القيمة: {gift.amount_usd}$\n"
        f"🔢 الاستخدامات: {gift.max_uses}\n\n"
        "احتفظ بالكود ووزعه فقط للمستفيد المقصود.",
        reply_markup=admin_gift_codes_kb(),
    )


@router.callback_query(F.data == "admin:gift_recent")
async def gift_recent(callback: CallbackQuery, session):
    result = await session.execute(select(GiftCode).order_by(desc(GiftCode.created_at)).limit(30))
    gifts = list(result.scalars().all())
    await callback.answer()
    await callback.message.edit_text(
        "📋 <b>آخر بطاقات الهدايا</b>\n\n"
        + ("\n".join(f"{gift.code} · {gift.amount_usd}$" for gift in gifts) or "لا توجد بطاقات."),
        reply_markup=admin_gift_recent_kb(gifts),
    )


@router.callback_query(F.data.startswith("admin:gift_revoke:"))
async def gift_revoke(callback: CallbackQuery, session):
    gift_id = int(callback.data.split(":")[2])
    gift = await session.get(GiftCode, gift_id)
    if gift is None:
        await callback.answer("⚠️ البطاقة غير موجودة.", show_alert=True)
        return
    gift.is_active = False
    await session.commit()
    await gift_recent(callback, session)
