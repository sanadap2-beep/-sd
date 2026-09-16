"""User interface for 24-hour special offers."""

from decimal import Decimal

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import desc, select

from database.models import SpecialOffer, SpecialOfferOrder
from keyboards.main_menu import insufficient_balance_kb
from services.currency_service import CurrencyService
from services.notification_service import NotificationService
from services.special_offer_service import SpecialOfferError, SpecialOfferService
from states.states import SpecialOfferOrderStates

router = Router(name="special_offers")


@router.callback_query(F.data == "special:home")
async def special_home(callback: CallbackQuery, session, db_user):
    offers = list(
        (
            await session.execute(
                select(SpecialOffer)
                .where(SpecialOffer.status == "active")
                .order_by(desc(SpecialOffer.created_at))
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    if not offers:
        await callback.message.edit_text(
            "🔥 <b>العروض الخاصة 24</b>\n\nلا توجد عروض نشطة حالياً. تابع الإشعارات لتعرف عند نزول عرض جديد.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="back_to_main")]]),
        )
        await callback.answer()
        return
    rows = []
    lines = ["🔥 <b>العروض الخاصة 24</b>", ""]
    timer_enabled = await _flash_timer_enabled()
    for offer in offers:
        price = await CurrencyService.format_dual(offer.price_usd, db_user, session)
        mode = "⚡ تلقائي" if offer.offer_type == "api" else "🧑‍💼 يدوي"
        countdown = ""
        if timer_enabled and offer.ends_at:
            countdown = f" ⏱{_countdown_text(offer.ends_at)}"
        lines.append(f"#{offer.id} {mode} · <b>{offer.name}</b> — {price}{countdown}")
        rows.append([InlineKeyboardButton(text=f"🔥 {offer.name[:28]} · {price}", callback_data=f"special:view:{offer.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="back_to_main")])
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


async def _flash_timer_enabled() -> bool:
    from services.feature_service import FeatureService
    return await FeatureService.enabled("flash_sale_timer")


def _countdown_text(ends_at) -> str:
    """عدّ تنازلي (HH:MM:SS) للعرض حتى انتهاء."""
    from datetime import datetime
    from datetime import timezone

    remaining = (ends_at - datetime.utcnow()).total_seconds()
    if remaining <= 0:
        return "انتهى"
    secs = int(remaining)
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


@router.callback_query(F.data.startswith("special:view:"))
async def special_view(callback: CallbackQuery, session, db_user):
    offer = await session.get(SpecialOffer, int(callback.data.rsplit(":", 1)[1]))
    if offer is None or offer.status != "active":
        await callback.answer("العرض غير متاح.", show_alert=True)
        return
    price = await CurrencyService.format_dual(offer.price_usd, db_user, session)
    mode = "⚡ تلقائي" if offer.offer_type == "api" else "🧑‍💼 يدوي يحتاج بعض الوقت"
    left = "—"
    if offer.ends_at:
        from datetime import datetime
        total_minutes = max(0, int((offer.ends_at - datetime.utcnow()).total_seconds() // 60))
        left = f"{total_minutes // 60} ساعة و {total_minutes % 60} دقيقة"
    await callback.message.edit_text(
        f"🔥 <b>{offer.name}</b>\n\n"
        f"{mode}\n"
        f"💰 السعر: <b>{price}</b>\n"
        f"⏱ التنفيذ التقريبي: {offer.eta_text or 'حسب الضغط'}\n"
        f"⏳ المتبقي: {left}\n\n"
        f"📝 {offer.description or '—'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛒 شراء العرض", callback_data=f"special:buy:{offer.id}")],
            [InlineKeyboardButton(text="⬅️ العروض", callback_data="special:home")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("special:buy:"))
async def special_buy_start(callback: CallbackQuery, state: FSMContext, session, db_user):
    offer = await session.get(SpecialOffer, int(callback.data.rsplit(":", 1)[1]))
    if offer is None or offer.status != "active":
        await callback.answer("العرض غير متاح.", show_alert=True)
        return
    if Decimal(str(db_user.balance or 0)) < offer.price_usd:
        await NotificationService(callback.bot).notify_insufficient_balance(
            db_user.telegram_id,
            str(offer.price_usd),
            f"{db_user.balance:.2f}",
            reply_markup=insufficient_balance_kb(),
        )
        return
    await state.set_state(SpecialOfferOrderStates.waiting_target)
    await state.update_data(offer_id=offer.id)
    await callback.message.answer(f"❓ {offer.input_label}")
    await callback.answer()


@router.message(SpecialOfferOrderStates.waiting_target)
async def special_target(message: Message, state: FSMContext, session, db_user):
    target = (message.text or "").strip()
    if len(target) < 2:
        await message.answer("⚠️ أرسل قيمة صحيحة.")
        return
    data = await state.get_data()
    offer = await session.get(SpecialOffer, int(data.get("offer_id")))
    if offer is None or offer.status != "active":
        await state.clear()
        await message.answer("العرض لم يعد متاحاً.")
        return
    # خصم الوكيل يظهر للمستخدم قبل التأكيد
    from services.agent_service import AgentService

    pay_price = await AgentService.apply_discount(session, db_user.id, offer.price_usd)
    price = await CurrencyService.format_dual(pay_price, db_user, session)
    agent_note = (
        f"💼 (شامل خصم وكيلك)\n" if pay_price != offer.price_usd else ""
    )
    await state.update_data(target=target)
    await message.answer(
        f"✅ <b>تأكيد شراء العرض</b>\n\n"
        f"📦 {offer.name}\n"
        f"{agent_note}"
        f"💰 السعر: {price}\n"
        f"🎯 المطلوب: <code>{target}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ تأكيد الشراء", callback_data="special:confirm")],
            [InlineKeyboardButton(text="❌ إلغاء", callback_data="special:home")],
        ]),
    )


@router.callback_query(F.data == "special:confirm")
async def special_confirm(callback: CallbackQuery, state: FSMContext, session, db_user, bot):
    data = await state.get_data()
    offer_id = int(data.get("offer_id"))
    target = data.get("target", "")
    # خصم الوكيل على سعر العرض (إن كان وكلاً)
    from services.agent_service import AgentService

    offer = await session.get(SpecialOffer, offer_id)
    pay_price = (
        await AgentService.apply_discount(session, db_user.id, offer.price_usd)
        if offer is not None
        else None
    )
    try:
        order = await SpecialOfferService.purchase(
            session, offer_id, db_user.id, target, bot, price_override=pay_price
        )
    except SpecialOfferError as exc:
        await callback.answer(str(exc), show_alert=True)
        await state.clear()
        return
    offer = await session.get(SpecialOffer, offer_id)
    await state.clear()
    try:
        from services.weekly_challenge_service import WeeklyChallengeService

        price = getattr(order, "price_usd", None) or Decimal("0")
        await WeeklyChallengeService.record_event(session, db_user.id, amount=price, event="orders")
        await WeeklyChallengeService.record_event(session, db_user.id, amount=price, event="spend_usd")
    except Exception:
        pass
    await callback.message.edit_text(
        f"✅ تم استلام طلب العرض الخاص.\n\n"
        f"🆔 الطلب: #{order.id}\n"
        f"📦 العرض: {offer.name if offer else '—'}\n"
        f"📊 الحالة: {order.status_message}\n"
        "سيصلك إشعار عند اكتمال التنفيذ أو الاسترجاع."
    )
    if order.status == "manual_pending":
        await NotificationService(bot).notify_admin(
            "🧑‍💼 <b>طلب عرض يدوي جديد</b>\n\n"
            f"🆔 الطلب: #{order.id}\n"
            f"📦 العرض: {offer.name if offer else '—'}\n"
            f"👤 المستخدم: <code>{db_user.telegram_id}</code>\n"
            f"🎯 المطلوب: <code>{target}</code>\n"
            f"💰 السعر: {order.price_usd}$",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ تم التنفيذ", callback_data=f"admin:so_order_done:{order.id}")],
                [InlineKeyboardButton(text="↩️ تعذر واسترجاع", callback_data=f"admin:so_order_refund:{order.id}")],
            ]),
        )
    await callback.answer("✅ تم الشراء.")


@router.callback_query(F.data.startswith("special:vote_extend:"))
async def vote_extend(callback: CallbackQuery, session, db_user):
    order_id = int(callback.data.rsplit(":", 1)[1])
    try:
        votes = await SpecialOfferService.vote_extend(session, order_id, db_user.id)
    except SpecialOfferError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(f"✅ وصل التصويت إلى {votes}/50")
