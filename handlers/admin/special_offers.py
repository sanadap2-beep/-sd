"""Admin panel for 24-hour special offers."""

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import desc, select

from database.models import ApiProvider, SpecialOffer, SpecialOfferOrder
from filters.admin_filter import IsAdmin
from services.special_offer_service import SpecialOfferError, SpecialOfferService
from states.states import AdminSpecialOfferStates

router = Router(name="admin_special_offers")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _admin_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ إضافة عرض جديد", callback_data="admin:so_new")],
        [InlineKeyboardButton(text="🧑‍💼 طلبات العروض اليدوية", callback_data="admin:so_manual_orders", style="primary")],
        [InlineKeyboardButton(text="🔥 العروض النشطة", callback_data="admin:so_active")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")],
    ])


@router.callback_query(F.data == "admin:special_offers")
async def admin_special_offers_home(callback: CallbackQuery, session):
    stats = await SpecialOfferService.stats(session)
    await callback.message.edit_text(
        "🔥 <b>قسم العروض الخاصة 24</b>\n\n"
        f"عروض اليوم: <b>{stats['offers_today']}</b>\n"
        f"مبيعات عروض اليوم: <b>{stats['sales_today']}</b>\n"
        f"نسبة نجاح العروض: <b>{stats['success_rate']}%</b>\n\n"
        "يمكنك إنشاء عرض يدوي أو تلقائي مربوط بمزود API.",
        reply_markup=_admin_home_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:so_new")
async def new_offer(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "➕ <b>إضافة عرض خاص جديد</b>\n\nاختر نوع العرض:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚡ تلقائي عبر مزود", callback_data="admin:so_type:api")],
            [InlineKeyboardButton(text="🧑‍💼 يدوي", callback_data="admin:so_type:manual")],
            [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:special_offers")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:so_type:"))
async def offer_type(callback: CallbackQuery, state: FSMContext, session):
    offer_type = callback.data.rsplit(":", 1)[1]
    await state.update_data(offer_type=offer_type)
    if offer_type == "api":
        providers = list((await session.execute(select(ApiProvider).where(ApiProvider.is_active.is_(True)))).scalars().all())
        if not providers:
            await callback.message.edit_text(
                "⚠️ لا يوجد مزود متجر مفعّل. أضف مزوداً من زر مزودو المتجر أولاً.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔌 مزودو المتجر", callback_data="admin:api_providers")]]),
            )
            await callback.answer()
            return
        rows = [[InlineKeyboardButton(text=f"#{p.id} {p.name} ({p.type.value})", callback_data=f"admin:so_provider:{p.id}")] for p in providers[:30]]
        rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:so_new")])
        await callback.message.edit_text("🔌 اختر المزود الذي سيُنفّذ العرض:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        await state.set_state(AdminSpecialOfferStates.waiting_name)
        await callback.message.answer("📝 أرسل اسم العرض:")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:so_provider:"))
async def offer_provider(callback: CallbackQuery, state: FSMContext):
    provider_id = int(callback.data.rsplit(":", 1)[1])
    await state.update_data(provider_id=provider_id)
    await state.set_state(AdminSpecialOfferStates.waiting_service_id)
    await callback.message.answer(
        "🆔 أرسل آيدي الخدمة عند المزود لهذا العرض.\n"
        "إذا كنت لا تعرفه افتح خدمات المزود بعد المزامنة وانسخ external id."
    )
    await callback.answer()


@router.message(AdminSpecialOfferStates.waiting_service_id)
async def offer_service_id(message: Message, state: FSMContext):
    service_id = (message.text or "").strip()
    if not service_id:
        await message.answer("⚠️ أرسل آيدي خدمة صالح.")
        return
    await state.update_data(service_id=service_id)
    await state.set_state(AdminSpecialOfferStates.waiting_name)
    await message.answer("📝 أرسل اسم العرض:")


@router.message(AdminSpecialOfferStates.waiting_name)
async def offer_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("⚠️ الاسم قصير جداً.")
        return
    await state.update_data(name=name)
    await state.set_state(AdminSpecialOfferStates.waiting_description)
    await message.answer("📄 أرسل معلومات العرض ووصفه:")


@router.message(AdminSpecialOfferStates.waiting_description)
async def offer_description(message: Message, state: FSMContext):
    await state.update_data(description=(message.text or "").strip())
    await state.set_state(AdminSpecialOfferStates.waiting_price)
    await message.answer("💰 أرسل سعر العرض بالدولار، مثال: 0.3")


@router.message(AdminSpecialOfferStates.waiting_price)
async def offer_price(message: Message, state: FSMContext):
    try:
        price = Decimal((message.text or "").strip().replace("$", ""))
        if price <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ أرسل سعراً صحيحاً أكبر من صفر.")
        return
    await state.update_data(price=str(price))
    await state.set_state(AdminSpecialOfferStates.waiting_eta)
    await message.answer("⏱ أرسل الوقت التقريبي للتنفيذ، مثال: من 5 إلى 30 دقيقة")


@router.message(AdminSpecialOfferStates.waiting_eta)
async def offer_eta(message: Message, state: FSMContext):
    await state.update_data(eta=(message.text or "").strip())
    await state.set_state(AdminSpecialOfferStates.waiting_input_label)
    await message.answer("❓ ماذا نطلب من المستخدم؟ مثال: أرسل رابط الحساب / أرسل ID اللاعب / أرسل الرقم")


@router.message(AdminSpecialOfferStates.waiting_input_label)
async def offer_input_label(message: Message, state: FSMContext, session, db_user, bot):
    data = await state.get_data()
    input_label = (message.text or "").strip() or "أرسل المطلوب"
    try:
        if data.get("offer_type") == "api":
            offer = await SpecialOfferService.create_api(
                session,
                admin_id=db_user.id,
                provider_id=int(data["provider_id"]),
                external_service_id=data["service_id"],
                name=data["name"],
                description=data.get("description", ""),
                price_usd=Decimal(data["price"]),
                eta_text=data.get("eta", ""),
                input_label=input_label,
            )
        else:
            offer = await SpecialOfferService.create_manual(
                session,
                admin_id=db_user.id,
                name=data["name"],
                description=data.get("description", ""),
                price_usd=Decimal(data["price"]),
                eta_text=data.get("eta", ""),
                input_label=input_label,
            )
    except SpecialOfferError as exc:
        await message.answer(f"⚠️ {exc}")
        await state.clear()
        return
    await state.clear()
    await message.answer(
        "✅ تم تجهيز العرض. اضغط نشر ليظهر للمستخدمين لمدة 24 ساعة.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 نشر العرض", callback_data=f"admin:so_publish:{offer.id}")],
            [InlineKeyboardButton(text="⬅️ قسم العروض", callback_data="admin:special_offers")],
        ]),
    )


@router.callback_query(F.data.startswith("admin:so_publish:"))
async def publish_offer(callback: CallbackQuery, session, bot):
    offer = await session.get(SpecialOffer, int(callback.data.rsplit(":", 1)[1]))
    if offer is None or offer.status not in {"draft", "expired"}:
        await callback.answer("لا يمكن نشر هذا العرض.", show_alert=True)
        return
    await SpecialOfferService.publish(session, offer, bot)
    await callback.message.edit_text("🚀 تم نشر العرض وإرسال إشعار للمستخدمين والقناة العامة.", reply_markup=_admin_home_kb())
    await callback.answer()


async def _show_offer_list(callback: CallbackQuery, session, status: str):
    offers = list((await session.execute(select(SpecialOffer).where(SpecialOffer.status == status).order_by(desc(SpecialOffer.created_at)).limit(20))).scalars().all())
    if not offers:
        await callback.message.edit_text("لا توجد عروض.", reply_markup=_admin_home_kb())
        await callback.answer()
        return
    rows = [[InlineKeyboardButton(text=f"#{o.id} {o.name[:28]} · {o.price_usd}$", callback_data=f"admin:so_view:{o.id}")] for o in offers]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:special_offers")])
    await callback.message.edit_text(f"🔥 عروض {status}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "admin:so_active")
async def active_offers(callback: CallbackQuery, session):
    await _show_offer_list(callback, session, "active")


@router.callback_query(F.data.startswith("admin:so_view:"))
async def offer_view(callback: CallbackQuery, session):
    offer = await session.get(SpecialOffer, int(callback.data.rsplit(":", 1)[1]))
    if offer is None:
        await callback.answer("العرض غير موجود.", show_alert=True)
        return
    left = "—"
    if offer.ends_at:
        from datetime import datetime
        left = f"{max(0, int((offer.ends_at - datetime.utcnow()).total_seconds() // 3600))} ساعة"
    await callback.message.edit_text(
        f"🔥 <b>{offer.name}</b>\n\n"
        f"النوع: {offer.offer_type}\nالسعر: {offer.price_usd}$\nالحالة: {offer.status}\n"
        f"المبيعات: {offer.sales_count} | نجاح: {offer.success_count} | فشل: {offer.failed_count}\n"
        f"تصويتات التمديد: {offer.extend_votes}/50\nالوقت المتبقي: {left}\n\n"
        f"{offer.description or '—'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 حذف العرض فوراً", callback_data=f"admin:so_delete:{offer.id}", style="danger")],
            [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:special_offers")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:so_delete:"))
async def offer_delete(callback: CallbackQuery, session):
    offer = await session.get(SpecialOffer, int(callback.data.rsplit(":", 1)[1]))
    if offer is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    offer.status = "deleted"
    await session.commit()
    await callback.answer("🗑 تم حذف العرض.")
    await admin_special_offers_home(callback, session)


@router.callback_query(F.data == "admin:so_manual_orders")
async def manual_orders(callback: CallbackQuery, session):
    orders = list((await session.execute(select(SpecialOfferOrder).where(SpecialOfferOrder.status == "manual_pending").order_by(SpecialOfferOrder.created_at).limit(30))).scalars().all())
    if not orders:
        await callback.message.edit_text("لا توجد طلبات عروض يدوية معلقة.", reply_markup=_admin_home_kb())
        await callback.answer()
        return
    rows = [[InlineKeyboardButton(text=f"#{o.id} عرض #{o.offer_id} · {o.price_usd}$", callback_data=f"admin:so_order:{o.id}", style="primary")] for o in orders]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:special_offers")])
    await callback.message.edit_text("🧑‍💼 <b>طلبات العروض اليدوية</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:so_order:"))
async def manual_order_view(callback: CallbackQuery, session):
    order = await session.get(SpecialOfferOrder, int(callback.data.rsplit(":", 1)[1]))
    if order is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    offer = await session.get(SpecialOffer, order.offer_id)
    await callback.message.edit_text(
        f"🧑‍💼 <b>طلب عرض يدوي #{order.id}</b>\n\n"
        f"العرض: {offer.name if offer else '—'}\n"
        f"المطلوب من المستخدم:\n<code>{order.target}</code>\n"
        f"السعر: {order.price_usd}$",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ تم تنفيذ العرض بنجاح", callback_data=f"admin:so_order_done:{order.id}", style="primary")],
            [InlineKeyboardButton(text="↩️ تعذر التنفيذ واسترجاع", callback_data=f"admin:so_order_refund:{order.id}", style="danger")],
            [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:so_manual_orders")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:so_order_done:"))
async def manual_order_done(callback: CallbackQuery, session, bot):
    ok = await SpecialOfferService.complete_order(session, int(callback.data.rsplit(":", 1)[1]), bot)
    await callback.answer("✅ تم التنفيذ." if ok else "لا يمكن تنفيذ الطلب.", show_alert=not ok)
    await manual_orders(callback, session)


@router.callback_query(F.data.startswith("admin:so_order_refund:"))
async def manual_order_refund(callback: CallbackQuery, session, bot):
    ok = await SpecialOfferService.refund_order_by_id(session, int(callback.data.rsplit(":", 1)[1]), bot)
    await callback.answer("↩️ تم الاسترجاع." if ok else "لا يمكن الاسترجاع.", show_alert=not ok)
    await manual_orders(callback, session)
