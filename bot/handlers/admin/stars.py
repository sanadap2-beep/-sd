"""
إدارة باقات نجوم تليجرام من لوحة الأدمن.
"""

from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from services.dynamic_service import DynamicService
from states.states import AdminStarsStates
from keyboards.admin import (
    admin_stars_kb,
    admin_star_detail_kb,
    admin_back_kb,
)
from filters.admin_filter import IsAdmin

router = Router(name="admin_stars")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════ قائمة الباقات ══════════════


@router.callback_query(F.data == "admin:stars")
async def stars_list(callback: CallbackQuery, session):
    packages = await DynamicService.get_all_stars_packages(session)
    await callback.message.edit_text(
        "⭐ <b>إدارة باقات نجوم تليجرام</b>\n\n"
        "🟢 = مفعّلة | ⚪ = معطّلة\n\n"
        "هذه الباقات تظهر للمستخدم عند اختيار شحن بالنجوم.",
        reply_markup=admin_stars_kb(packages),
    )


# ══════════════ إضافة باقة ══════════════


@router.callback_query(F.data == "admin:star_add")
async def star_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "➕ <b>إضافة باقة نجوم جديدة</b>\n\nأرسل عدد النجوم:\n(مثال: 100)",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminStarsStates.waiting_stars_amount)


@router.message(AdminStarsStates.waiting_stars_amount)
async def star_amount_received(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    await state.update_data(star_amount=amount)
    await message.answer(
        "💰 أرسل المبلغ بالدولار الذي سيُضاف للرصيد عند شراء هذه الباقة:\n(مثال: 1.30)"
    )
    await state.set_state(AdminStarsStates.waiting_usd_amount)


@router.message(AdminStarsStates.waiting_usd_amount)
async def star_usd_received(message: Message, state: FSMContext):
    try:
        usd = Decimal((message.text or "").strip())
        if not usd.is_finite() or usd <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    await state.update_data(star_usd=str(usd))
    data = await state.get_data()
    default_label = f"⭐ {data['star_amount']} نجمة"

    await message.answer(f"📝 أرسل عنوان الباقة:\n(أو أرسل - لاستخدام: {default_label})")
    await state.set_state(AdminStarsStates.waiting_label)


@router.message(AdminStarsStates.waiting_label)
async def star_label_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    label = message.text.strip()
    if label == "-":
        label = f"⭐ {data['star_amount']} نجمة"

    package = await DynamicService.create_stars_package(
        session=session,
        stars_amount=data["star_amount"],
        usd_amount=Decimal(data["star_usd"]),
        label=label,
    )

    await message.answer(
        f"✅ تم إنشاء الباقة بنجاح!\n\n"
        f"⭐ النجوم: {package.stars_amount}\n"
        f"💰 المبلغ: {package.usd_amount}$\n"
        f"📝 العنوان: {package.label}"
    )
    await state.clear()


# ══════════════ تفاصيل الباقة ══════════════


@router.callback_query(F.data.startswith("admin:star_view:"))
async def star_view(callback: CallbackQuery, session):
    pkg_id = int(callback.data.split(":")[2])
    package = await DynamicService.get_stars_package(session, pkg_id)
    if not package:
        await callback.answer("⚠️ الباقة غير موجودة.", show_alert=True)
        return

    status = "🟢 مفعّلة" if package.is_active else "⚪ معطّلة"
    await callback.message.edit_text(
        f"⭐ <b>{package.label}</b>\n\n"
        f"عدد النجوم: {package.stars_amount}\n"
        f"المبلغ: {package.usd_amount}$\n"
        f"الحالة: {status}\n"
        f"الترتيب: {package.sort_order}\n"
        f"تاريخ الإنشاء: "
        f"{package.created_at.strftime('%Y-%m-%d')}",
        reply_markup=admin_star_detail_kb(package),
    )


# ══════════════ تفعيل/تعطيل ══════════════


@router.callback_query(F.data.startswith("admin:star_toggle:"))
async def star_toggle(callback: CallbackQuery, session):
    pkg_id = int(callback.data.split(":")[2])
    package = await DynamicService.get_stars_package(session, pkg_id)
    if not package:
        await callback.answer("⚠️ الباقة غير موجودة.", show_alert=True)
        return

    await DynamicService.update_stars_package(session, pkg_id, is_active=not package.is_active)
    await callback.answer("✅ تم التحديث.")
    await star_view(callback, session)


# ══════════════ حذف الباقة ══════════════


@router.callback_query(F.data.startswith("admin:star_delete:"))
async def star_delete(callback: CallbackQuery, session):
    pkg_id = int(callback.data.split(":")[2])
    success = await DynamicService.delete_stars_package(session, pkg_id)
    if success:
        await callback.answer("🗑 تم حذف الباقة.")
    else:
        await callback.answer("⚠️ الباقة غير موجودة.", show_alert=True)
    await stars_list(callback, session)
