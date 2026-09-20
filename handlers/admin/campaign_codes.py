"""
إدارة أكواد الحملات (ميزة campaign_codes) من لوحة الأدمن.

كود الحملة = كوبون + وسم تتبّع (حركة#حملة) ليعرف الأدمن من أي
إعلان جاء كل شراء، مع تقرير الاستخدام لكل حملة.
"""

import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import CampaignCode, User
from services.campaign_service import CampaignService
from services.feature_service import FeatureService
from states.states import AdminCampaignCodeStates
from keyboards.admin import (
    admin_campaign_codes_kb,
    admin_campaign_detail_kb,
    admin_back_kb,
)
from filters.admin_filter import IsAdmin

router = Router(name="admin_campaign_codes")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _enabled() -> bool:
    return await FeatureService.enabled("campaign_codes")


# ══════════════ قائمة أكواد الحملات ══════════════


@router.callback_query(F.data == "admin:campaigns")
async def campaigns_list(callback: CallbackQuery, session):
    if not await _enabled():
        await callback.answer("أكواد الحملات معطلة في مركز الإضافات.", show_alert=True)
        return
    campaigns = await CampaignService.get_all(session)
    await callback.message.edit_text(
        "🎟 <b>إدارة أكواد الحملات</b>\n\n"
        "🟢 = مفعّل | ⚪ = معطّل\n"
        "الأكواد مرتبة من الأحدث:\n"
        "الاستخدام (X/Y) = المستخدمات/الحد الأقصى.",
        reply_markup=admin_campaign_codes_kb(campaigns),
    )


# ══════════════ إنشاء كود حملة ══════════════


@router.callback_query(F.data == "admin:campaign_add")
async def campaign_add_start(callback: CallbackQuery, state: FSMContext):
    if not await _enabled():
        await callback.answer("أكواد الحملات معطلة في مركز الإضافات.", show_alert=True)
        return
    await callback.message.edit_text(
        "🎟 <b>إنشاء كود حملة جديد</b>\n\n"
        "أرسل <b>كود الخصم</b>:\n"
        "(مثال: RAMADAN2026 — سيُظهره المستخدم عند الشراء)",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminCampaignCodeStates.waiting_code)


@router.message(AdminCampaignCodeStates.waiting_code)
async def campaign_code_received(message: Message, state: FSMContext, session):
    code = (message.text or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{2,32}", code):
        await message.answer(
            "⚠️ الكود يجب أن يكون من 2 إلى 32 رمزاً: أحرف إنجليزية أو أرقام أو _ أو -."
        )
        return

    existing = await CampaignService.get_by_code(session, code)
    if existing:
        await message.answer("⚠️ يوجد كود حملة بهذا الاسم مسبقاً. أرسل اسماً آخر.")
        return

    await state.update_data(campaign_code=code)
    await message.answer(
        "📊 أرسل <b>وسم التتبّع</b> (حركة#حملة):\n"
        "(مثال: fb_news → Facebook، snap → سناب، سمّيه براحتك)\n\n"
        "كل شراء بهذا الكود يُحصى تحت هذا الوسم لترى كم طلب جلبت الحملة.",
    )
    await state.set_state(AdminCampaignCodeStates.waiting_tracking)


@router.message(AdminCampaignCodeStates.waiting_tracking)
async def campaign_tracking_received(message: Message, state: FSMContext):
    tracking = (message.text or "").strip().replace("#", "")[:64]
    if not tracking:
        await message.answer("⚠️ أرسل وسم تتبّع غير فارغ، مثال: fb_campaign1")
        return
    await state.update_data(campaign_tracking=tracking)

    b = InlineKeyboardBuilder()
    b.button(text="📊 نسبة مئوية (%)", callback_data="admin:campaign_dtype:percent")
    b.button(text="💵 مبلغ ثابت ($)", callback_data="admin:campaign_dtype:fixed")
    b.adjust(1)
    await message.answer(
        "اختر نوع الخصم:",
        reply_markup=b.as_markup(),
    )
    await state.set_state(AdminCampaignCodeStates.waiting_discount_type)


@router.callback_query(F.data.startswith("admin:campaign_dtype:"))
async def campaign_dtype_selected(callback: CallbackQuery, state: FSMContext):
    dtype = callback.data.split(":")[2]
    await state.update_data(campaign_dtype=dtype)

    label = "النسبة (%)" if dtype == "percent" else "المبلغ ($)"
    await callback.message.edit_text(f"🔢 أرسل قيمة الخصم ({label}):\n(مثال: 10)")
    await state.set_state(AdminCampaignCodeStates.waiting_discount_value)
    await callback.answer()


@router.message(AdminCampaignCodeStates.waiting_discount_value)
async def campaign_value_received(message: Message, state: FSMContext):
    try:
        value = Decimal((message.text or "").strip())
        if not value.is_finite() or value <= 0:
            raise InvalidOperation
        data = await state.get_data()
        if data.get("campaign_dtype") == "percent" and value > 100:
            await message.answer("⚠️ النسبة لا يمكن أن تتجاوز 100%.")
            return
    except InvalidOperation:
        await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    await state.update_data(campaign_value=str(value))
    await message.answer("🔢 أرسل الحد الأقصى لعدد الاستخدامات:\n(مثال: 500)")
    await state.set_state(AdminCampaignCodeStates.waiting_max_uses)


@router.message(AdminCampaignCodeStates.waiting_max_uses)
async def campaign_max_uses_received(message: Message, state: FSMContext):
    try:
        max_uses = int(message.text.strip())
        if max_uses <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    await state.update_data(campaign_max_uses=max_uses)
    await message.answer("💰 أرسل الحد الأدنى لقيمة الطلب بالدولار:\n(أو أرسل 0 لبدون حد أدنى)")
    await state.set_state(AdminCampaignCodeStates.waiting_min_order)


@router.message(AdminCampaignCodeStates.waiting_min_order)
async def campaign_min_order_received(message: Message, state: FSMContext):
    try:
        min_order = Decimal((message.text or "").strip())
        if not min_order.is_finite() or min_order < 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("⚠️ أرسل رقماً صحيحاً غير سالب.")
        return

    await state.update_data(campaign_min_order=str(min_order))
    await message.answer("📅 أرسل عدد أيام صلاحية الكود:\n(أو أرسل 0 لبدون تاريخ انتهاء)")
    await state.set_state(AdminCampaignCodeStates.waiting_expires_days)


@router.message(AdminCampaignCodeStates.waiting_expires_days)
async def campaign_expires_received(
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

    campaign = await CampaignService.create(
        session=session,
        code=data["campaign_code"],
        tracking=data["campaign_tracking"],
        discount_type=data["campaign_dtype"],
        discount_value=Decimal(data["campaign_value"]),
        max_uses=data["campaign_max_uses"],
        min_order_usd=Decimal(data.get("campaign_min_order", "0")),
        expires_at=expires_at,
        created_by=db_user.id,
    )

    dtype_label = (
        f"{campaign.discount_value}%"
        if campaign.discount_type == "percent"
        else f"{campaign.discount_value}$"
    )
    expires_label = expires_at.strftime("%Y-%m-%d") if expires_at else "بدون انتهاء"

    await message.answer(
        f"✅ تم إنشاء كود الحملة بنجاح!\n\n"
        f"🎟 الكود: <code>{campaign.code}</code>\n"
        f"📊 الوسم: <b>{campaign.tracking or '-'}</b>\n"
        f"💰 الخصم: {dtype_label}\n"
        f"🔢 الاستخدامات: {campaign.max_uses}\n"
        f"💵 حد أدنى للطلب: {campaign.min_order_usd}$\n"
        f"📅 الانتهاء: {expires_label}"
    )
    await state.clear()


# ══════════════ تفاصيل كود الحملة ══════════════


@router.callback_query(F.data.startswith("admin:campaign_view:"))
async def campaign_view(callback: CallbackQuery, session):
    campaign_id = int(callback.data.split(":")[2])
    campaign = await session.get(CampaignCode, campaign_id)
    if not campaign:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    status = "🟢 مفعّل" if campaign.is_active else "⚪ معطّل"
    dtype_label = (
        f"{campaign.discount_value}%"
        if campaign.discount_type == "percent"
        else f"{campaign.discount_value}$"
    )
    expires_label = campaign.expires_at.strftime("%Y-%m-%d") if campaign.expires_at else "بدون انتهاء"
    usages = await CampaignService.usage_count(session, campaign.id)

    await callback.message.edit_text(
        f"🎟 <b>كود حملة: {campaign.code}</b>\n\n"
        f"📊 الوسم: <b>{campaign.tracking or '-'}</b>\n"
        f"الحالة: {status}\n"
        f"💰 الخصم: {dtype_label}\n"
        f"🔢 الاستخدامات: {usages} (سجلات) | {campaign.used_count}/{campaign.max_uses}\n"
        f"💵 حد أدنى: {campaign.min_order_usd}$\n"
        f"📅 الانتهاء: {expires_label}\n"
        f"📅 تاريخ الإنشاء: {campaign.created_at.strftime('%Y-%m-%d')}\n\n"
        "💡 لمعرفة عدد الطلبات من حملة ما: وسّم كل كود بنفس اسم الحملة "
        "ثم اجمع أعداد استخداماته.",
        reply_markup=admin_campaign_detail_kb(campaign),
    )


# ══════════════ تفعيل/تعطيل ══════════════


@router.callback_query(F.data.startswith("admin:campaign_toggle:"))
async def campaign_toggle(callback: CallbackQuery, session):
    campaign_id = int(callback.data.split(":")[2])
    campaign = await CampaignService.toggle(session, campaign_id)
    if campaign:
        await callback.answer("✅ تم التحديث.")
        await campaign_view(callback, session)
    else:
        await callback.answer("⚠️ غير موجود.", show_alert=True)


# ══════════════ حذف ══════════════


@router.callback_query(F.data.startswith("admin:campaign_delete:"))
async def campaign_delete(callback: CallbackQuery, session):
    campaign_id = int(callback.data.split(":")[2])
    success = await CampaignService.delete(session, campaign_id)
    if success:
        await callback.answer("🗑 تم حذف كود الحملة.")
    else:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
    await campaigns_list(callback, session)