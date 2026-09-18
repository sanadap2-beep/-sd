"""
إدارة كوبونات الخصم من لوحة الأدمن.
"""

import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import User
from services.coupon_service import CouponService
from states.states import AdminCouponStates
from keyboards.admin import (
    admin_coupons_kb,
    admin_coupon_detail_kb,
    admin_back_kb,
)
from filters.admin_filter import IsAdmin

router = Router(name="admin_coupons")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════ قائمة الكوبونات ══════════════


@router.callback_query(F.data == "admin:coupons")
async def coupons_list(callback: CallbackQuery, session):
    coupons = await CouponService.get_all_coupons(session)
    await callback.message.edit_text(
        "🎟 <b>إدارة الكوبونات</b>\n\n🟢 = مفعّل | ⚪ = معطّل",
        reply_markup=admin_coupons_kb(coupons),
    )


# ══════════════ إنشاء كوبون ══════════════


@router.callback_query(F.data == "admin:coupon_add")
async def coupon_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🎟 <b>إنشاء كوبون جديد</b>\n\nأرسل كود الكوبون:\n(مثال: SAVE10)",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminCouponStates.waiting_code)


@router.message(AdminCouponStates.waiting_code)
async def coupon_code_received(message: Message, state: FSMContext, session):
    code = (message.text or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{2,32}", code):
        await message.answer(
            "⚠️ الكود يجب أن يكون من 2 إلى 32 رمزاً: أحرف إنجليزية أو أرقام أو _ أو -."
        )
        return

    existing = await CouponService.get_coupon_by_code(session, code)
    if existing:
        await message.answer("⚠️ يوجد كوبون بهذا الكود مسبقاً. أرسل كوداً آخر.")
        return

    await state.update_data(coupon_code=code)

    b = InlineKeyboardBuilder()
    b.button(
        text="📊 نسبة مئوية (%)",
        callback_data="admin:coupon_dtype:percent", style="primary",
    )
    b.button(
        text="💵 مبلغ ثابت ($)",
        callback_data="admin:coupon_dtype:fixed", style="primary",
    )
    b.adjust(1)
    await message.answer(
        "اختر نوع الخصم:",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data.startswith("admin:coupon_dtype:"))
async def coupon_dtype_selected(callback: CallbackQuery, state: FSMContext):
    dtype = callback.data.split(":")[2]
    await state.update_data(coupon_dtype=dtype)

    label = "النسبة (%)" if dtype == "percent" else "المبلغ ($)"
    await callback.message.edit_text(f"🔢 أرسل قيمة الخصم ({label}):\n(مثال: 10)")
    await state.set_state(AdminCouponStates.waiting_discount_value)
    await callback.answer()


@router.message(AdminCouponStates.waiting_discount_value)
async def coupon_value_received(message: Message, state: FSMContext):
    try:
        value = Decimal((message.text or "").strip())
        if not value.is_finite() or value <= 0:
            raise InvalidOperation
        data = await state.get_data()
        if data.get("coupon_dtype") == "percent" and value > 100:
            await message.answer("⚠️ النسبة لا يمكن أن تتجاوز 100%.")
            return
    except InvalidOperation:
        await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    await state.update_data(coupon_value=str(value))
    await message.answer("🔢 أرسل الحد الأقصى لعدد الاستخدامات:\n(مثال: 100)")
    await state.set_state(AdminCouponStates.waiting_max_uses)


@router.message(AdminCouponStates.waiting_max_uses)
async def coupon_max_uses_received(message: Message, state: FSMContext):
    try:
        max_uses = int(message.text.strip())
        if max_uses <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    await state.update_data(coupon_max_uses=max_uses)
    await message.answer("💰 أرسل الحد الأدنى لقيمة الطلب بالدولار:\n(أو أرسل 0 لبدون حد أدنى)")
    await state.set_state(AdminCouponStates.waiting_min_order)


@router.message(AdminCouponStates.waiting_min_order)
async def coupon_min_order_received(message: Message, state: FSMContext):
    try:
        min_order = Decimal((message.text or "").strip())
        if not min_order.is_finite() or min_order < 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("⚠️ أرسل رقماً صحيحاً غير سالب.")
        return

    await state.update_data(coupon_min_order=str(min_order))
    await message.answer("📅 أرسل عدد أيام صلاحية الكوبون:\n(أو أرسل 0 لبدون تاريخ انتهاء)")
    await state.set_state(AdminCouponStates.waiting_expires_days)


@router.message(AdminCouponStates.waiting_expires_days)
async def coupon_expires_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
):
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ أرسل رقماً صحيحاً.")
        return

    data = await state.get_data()

    expires_at = None
    if days > 0:
        expires_at = datetime.utcnow() + timedelta(days=days)

    coupon = await CouponService.create_coupon(
        session=session,
        code=data["coupon_code"],
        discount_type=data["coupon_dtype"],
        discount_value=Decimal(data["coupon_value"]),
        max_uses=data["coupon_max_uses"],
        min_order_usd=Decimal(data.get("coupon_min_order", "0")),
        expires_at=expires_at,
        created_by=db_user.id,
    )

    dtype_label = (
        f"{coupon.discount_value}%"
        if coupon.discount_type == "percent"
        else f"{coupon.discount_value}$"
    )
    expires_label = expires_at.strftime("%Y-%m-%d") if expires_at else "بدون انتهاء"

    await message.answer(
        f"✅ تم إنشاء الكوبون بنجاح!\n\n"
        f"🎟 الكود: <code>{coupon.code}</code>\n"
        f"💰 الخصم: {dtype_label}\n"
        f"🔢 الاستخدامات: {coupon.max_uses}\n"
        f"💵 حد أدنى للطلب: {coupon.min_order_usd}$\n"
        f"📅 الانتهاء: {expires_label}"
    )
    await state.clear()


# ══════════════ تفاصيل الكوبون ══════════════


@router.callback_query(F.data.startswith("admin:coupon_view:"))
async def coupon_view(callback: CallbackQuery, session):
    coupon_id = int(callback.data.split(":")[2])
    coupon = await session.get(
        __import__("database.models", fromlist=["Coupon"]).Coupon,
        coupon_id,
    )
    if not coupon:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    status = "🟢 مفعّل" if coupon.is_active else "⚪ معطّل"
    dtype_label = (
        f"{coupon.discount_value}%"
        if coupon.discount_type == "percent"
        else f"{coupon.discount_value}$"
    )
    expires_label = coupon.expires_at.strftime("%Y-%m-%d") if coupon.expires_at else "بدون انتهاء"

    await callback.message.edit_text(
        f"🎟 <b>كوبون: {coupon.code}</b>\n\n"
        f"الحالة: {status}\n"
        f"💰 الخصم: {dtype_label}\n"
        f"🔢 الاستخدامات: {coupon.used_count}/{coupon.max_uses}\n"
        f"💵 حد أدنى: {coupon.min_order_usd}$\n"
        f"📅 الانتهاء: {expires_label}\n"
        f"📅 تاريخ الإنشاء: "
        f"{coupon.created_at.strftime('%Y-%m-%d')}",
        reply_markup=admin_coupon_detail_kb(coupon),
    )


# ══════════════ تفعيل/تعطيل ══════════════


@router.callback_query(F.data.startswith("admin:coupon_toggle:"))
async def coupon_toggle(callback: CallbackQuery, session):
    coupon_id = int(callback.data.split(":")[2])
    coupon = await CouponService.toggle_coupon(session, coupon_id)
    if coupon:
        await callback.answer("✅ تم التحديث.")
        await coupon_view(callback, session)
    else:
        await callback.answer("⚠️ غير موجود.", show_alert=True)


# ══════════════ حذف ══════════════


@router.callback_query(F.data.startswith("admin:coupon_delete:"))
async def coupon_delete(callback: CallbackQuery, session):
    coupon_id = int(callback.data.split(":")[2])
    success = await CouponService.delete_coupon(session, coupon_id)
    if success:
        await callback.answer("🗑 تم حذف الكوبون.")
    else:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
    await coupons_list(callback, session)
