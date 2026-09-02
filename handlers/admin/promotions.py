"""إنشاء وإدارة العروض الزمنية من لوحة الأدمن."""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from database.models import (
    Product,
    ProductFulfillmentType,
    ProductStatus,
    Promotion,
    PromotionDiscountType,
)
from filters.admin_filter import IsAdmin
from keyboards.admin import admin_back_kb
from keyboards.promotions import (
    admin_promotion_detail_kb,
    admin_promotion_types_kb,
    admin_promotions_kb,
)
from services.promotion_service import PromotionService
from states.states import AdminPromotionStates

router = Router(name="admin_promotions")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _get_promotion(session, promotion_id: int):
    result = await session.execute(
        select(Promotion)
        .options(selectinload(Promotion.product))
        .where(Promotion.id == promotion_id)
    )
    return result.scalar_one_or_none()


@router.callback_query(F.data == "admin:promotions")
async def promotions_list(callback: CallbackQuery, session):
    result = await session.execute(
        select(Promotion)
        .options(selectinload(Promotion.product))
        .order_by(desc(Promotion.created_at))
    )
    promotions = list(result.scalars().all())
    await callback.answer()
    await callback.message.edit_text(
        "🔥 <b>إدارة العروض</b>\n\nالعروض تنتهي تلقائياً حسب وقت الانتهاء، ويمكنك تعطيلها يدوياً.",
        reply_markup=admin_promotions_kb(promotions),
    )


@router.callback_query(F.data == "admin:promo_add")
async def promo_add_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AdminPromotionStates.waiting_product_id)
    await callback.answer()
    await callback.message.edit_text(
        "🔥 <b>إنشاء عرض جديد</b>\n\nأرسل رقم المنتج الداخلي الذي سيطبق عليه العرض:",
        reply_markup=admin_back_kb(),
    )


@router.message(AdminPromotionStates.waiting_product_id)
async def promo_product_received(
    message: Message,
    state: FSMContext,
    session,
):
    try:
        product_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("⚠️ أرسل رقم منتج صحيح.")
        return
    product = await session.get(Product, product_id)
    if (
        product is None
        or product.status != ProductStatus.ACTIVE
        or product.fulfillment_type == ProductFulfillmentType.MANUAL
    ):
        await message.answer("⚠️ المنتج غير موجود أو غير قابل للبيع تلقائياً.")
        return
    await state.update_data(product_id=product_id)
    await message.answer("📝 أرسل اسم العرض، مثال: عرض نهاية الأسبوع:")
    await state.set_state(AdminPromotionStates.waiting_name)


@router.message(AdminPromotionStates.waiting_name)
async def promo_name_received(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 128:
        await message.answer("⚠️ اسم العرض يجب أن يكون بين 2 و128 حرفاً.")
        return
    await state.update_data(name=name)
    await message.answer(
        "اختر نوع الخصم:",
        reply_markup=admin_promotion_types_kb(),
    )


@router.callback_query(F.data.startswith("admin:promo_type:"))
async def promo_type_selected(callback: CallbackQuery, state: FSMContext):
    discount_type = callback.data.split(":")[2]
    if discount_type not in ("percent", "fixed"):
        await callback.answer("⚠️ نوع غير صالح.", show_alert=True)
        return
    await state.update_data(discount_type=discount_type)
    await state.set_state(AdminPromotionStates.waiting_discount_value)
    await callback.answer()
    await callback.message.edit_text("🔢 أرسل قيمة الخصم (مثال: 15 أو 0.50):")


@router.message(AdminPromotionStates.waiting_discount_value)
async def promo_value_received(message: Message, state: FSMContext):
    try:
        value = Decimal((message.text or "").strip())
        data = await state.get_data()
        if not value.is_finite() or value <= 0:
            raise InvalidOperation
        if data.get("discount_type") == "percent" and value > 100:
            await message.answer("⚠️ النسبة لا يمكن أن تتجاوز 100%.")
            return
    except InvalidOperation:
        await message.answer("⚠️ أرسل قيمة موجبة صحيحة.")
        return
    await state.update_data(discount_value=str(value))
    await message.answer("⏱ أرسل مدة العرض بالساعات (من 1 إلى 8760):")
    await state.set_state(AdminPromotionStates.waiting_duration_hours)


@router.message(AdminPromotionStates.waiting_duration_hours)
async def promo_duration_received(message: Message, state: FSMContext):
    try:
        hours = int((message.text or "").strip())
        if hours < 1 or hours > 8760:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ أرسل عدداً بين 1 و8760 ساعة.")
        return
    await state.update_data(duration_hours=hours)
    await message.answer("🔢 الحد الأقصى لاستخدام العرض؟ أرسل 0 لاستخدامات غير محدودة:")
    await state.set_state(AdminPromotionStates.waiting_max_uses)


@router.message(AdminPromotionStates.waiting_max_uses)
async def promo_max_uses_received(
    message: Message,
    state: FSMContext,
    session,
    db_user,
):
    try:
        max_uses = int((message.text or "").strip())
        if max_uses < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ أرسل صفراً أو عدداً صحيحاً موجباً.")
        return

    data = await state.get_data()
    now = datetime.utcnow()
    promotion = await PromotionService.create(
        session=session,
        product_id=data["product_id"],
        name=data["name"],
        discount_type=PromotionDiscountType(data["discount_type"]),
        discount_value=Decimal(data["discount_value"]),
        starts_at=now,
        ends_at=now + timedelta(hours=data["duration_hours"]),
        created_by=db_user.id,
        max_uses=max_uses,
    )
    await state.clear()
    await message.answer(
        f"✅ تم إنشاء العرض <b>#{promotion.id}</b> بنجاح.",
        reply_markup=admin_back_kb(),
    )


@router.callback_query(F.data.startswith("admin:promo_view:"))
async def promo_view(callback: CallbackQuery, session):
    promotion = await _get_promotion(session, int(callback.data.split(":")[2]))
    if promotion is None:
        await callback.answer("⚠️ العرض غير موجود.", show_alert=True)
        return
    product_name = promotion.product.name_ar if promotion.product else "—"
    discount = (
        f"{promotion.discount_value}%"
        if promotion.discount_type == PromotionDiscountType.PERCENT
        else f"{promotion.discount_value}$"
    )
    uses = (
        f"{promotion.used_count}/{promotion.max_uses}"
        if promotion.max_uses
        else f"{promotion.used_count}/∞"
    )
    await callback.message.edit_text(
        "🔥 <b>تفاصيل العرض</b>\n\n"
        f"🆔 #{promotion.id}\n"
        f"📝 {escape(promotion.name)}\n"
        f"📦 المنتج: {escape(product_name)}\n"
        f"💸 الخصم: <b>{discount}</b>\n"
        f"📅 من: {promotion.starts_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"📅 إلى: {promotion.ends_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"📊 الاستخدام: {uses}\n"
        f"📌 الحالة: {'مفعّل' if promotion.is_active else 'معطّل'}",
        reply_markup=admin_promotion_detail_kb(promotion),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:promo_toggle:"))
async def promo_toggle(callback: CallbackQuery, session):
    promotion = await PromotionService.toggle(session, int(callback.data.split(":")[2]))
    if promotion is None:
        await callback.answer("⚠️ العرض غير موجود.", show_alert=True)
        return
    await promo_view(callback, session)


@router.callback_query(F.data.startswith("admin:promo_delete:"))
async def promo_delete(callback: CallbackQuery, session):
    await PromotionService.delete(session, int(callback.data.split(":")[2]))
    await promotions_list(callback, session)
