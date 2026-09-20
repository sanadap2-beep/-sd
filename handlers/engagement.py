"""
واجهة المستخدم للميزات الجديدة التفاعلية.

كل زر هنا يفحص الميزة المقابلة عبر FeatureService، فإن كانت موقوفة
لا يستجيب ولا يظهر. هذه الواجهات تربط الخدمات بلوحة أزرار تليجرام.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.models import User
from services.feature_service import FeatureService
from services.i18n_service import I18nService

router = Router(name="engagement")


class PriceAlertFSM(StatesGroup):
    waiting_service = State()


class ResaleFSM(StatesGroup):
    waiting_details = State()


class ReviewFSM(StatesGroup):
    waiting_rating = State()
    waiting_comment = State()


async def _enabled(key: str) -> bool:
    return await FeatureService.enabled(key)


def _lang(db_user) -> str:
    return getattr(db_user, "language_code", "ar") or "ar"


def _back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")]])


# ══════════════ عجلة الحظ اليومية ══════════════


@router.callback_query(F.data == "engage:spin")
async def engage_spin(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("daily_spin"):
        await callback.answer("هذه الميزة معطلة.", show_alert=True)
        return
    from services.spin_service import SpinError, SpinService

    can_spin, already_spun, last = await SpinService.can_spin_today(session, db_user.id)
    prizes = await SpinService.get_active_prizes(session)
    if not prizes:
        await callback.answer("العجلة غير جاهزة حالياً.", show_alert=True)
        return
    prize_labels = "، ".join(p.name_ar for p in prizes[:5])
    text = f"🎰 <b>عجلة الحظ اليومية</b>\n\n"
    text += f"الجوائز: {prize_labels}...\n"
    if already_spun:
        text += f"\n✅ لفّت اليوم! آخر جائزة: <b>{last.prize_label}</b>.\nعد غداً للفة جديدة."
        buttons = []
    else:
        text += "\nلفّ الآن واحصل على مكافأة مجانية!"
        buttons = [[InlineKeyboardButton(text="🎰 اضغط للّف!", callback_data="engage:spin_go")]]
    buttons.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data == "engage:spin_go")
async def engage_spin_go(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("daily_spin"):
        await callback.answer("هذه الميزة معطلة.", show_alert=True)
        return
    from services.spin_service import SpinError, SpinService

    try:
        result = await SpinService.spin(session, db_user.id)
    except SpinError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    prize = result["prize"]
    outcome = result["outcome"]
    text = f"🎰 <b>النتيجة: {prize.name_ar}</b>\n\n"
    if prize.prize_type == "balance":
        text += f"💰 وصلتك <b>{prize.value:g}$</b> في رصيدك!"
    elif prize.prize_type == "points":
        text += f"⭐ وصلتك <b>{int(prize.value)}</b> نقطة ولاء!"
    else:
        text += "حظ أوفر المرة القادمة!"
    text += "\n\nعد غداً للفة جديدة."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")]])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("🎉 تهانينا!")


# ══════════════ لوحة أبطال الأسبوع ══════════════


@router.callback_query(F.data == "engage:leaderboard")
async def engage_leaderboard(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("top_buyers"):
        await callback.answer("هذه الميزة معطلة.", show_alert=True)
        return
    from services.leaderboard_service import LeaderboardService

    text = await LeaderboardService.render_board(session, include_self_user_id=db_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")]])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ══════════════ تحديات أسبوعية ══════════════


@router.callback_query(F.data == "engage:challenges")
async def engage_challenges(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("weekly_challenges"):
        await callback.answer("هذه الميزة معطلة.", show_alert=True)
        return
    from services.weekly_challenge_service import WeeklyChallengeService

    challenge = await WeeklyChallengeService.active_challenge(session)
    if challenge is None:
        text = "🏅 <b>التحديات الأسبوعية</b>\n\nلا يوجد تحدٍّ نشط هذا الأسبوع — تابعنا قريباً!"
        kb = _back()
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer()
        return
    progress = await WeeklyChallengeService.get_progress(session, challenge.id, db_user.id)
    current = progress.progress if progress else Decimal("0")
    pct = min(100, int(current * Decimal("100") / max(Decimal("1"), challenge.target_value)))
    claimable = (
        current >= challenge.target_value and progress and not progress.claimed
    )
    text = (
        f"{challenge.emoji} <b>{challenge.title}</b>\n\n"
        f"{challenge.description}\n\n"
        f"📈 التقدم: <b>{current:g}</b> / {challenge.target_value:g} ({pct}%)\n"
        f"🎁 المكافأة: {challenge.reward_usd:g}$"
    )
    if challenge.reward_points:
        text += f" + {challenge.reward_points} نقطة"
    buttons = []
    if claimable:
        buttons.append([InlineKeyboardButton(text="🎉 استلام المكافأة!", callback_data="engage:challenge_claim")])
    if progress and progress.claimed:
        text += "\n\n✅ استلمت مكافأتك هذا الأسبوع."
    buttons.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data == "engage:challenge_claim")
async def engage_challenge_claim(callback: CallbackQuery, session, db_user: User):
    from services.weekly_challenge_service import WeeklyChallengeService

    result = await WeeklyChallengeService.claim_reward(session, db_user.id)
    if result is None:
        await callback.answer("لا توجد مكافأة للاستلام.", show_alert=True)
        return
    text = f"🎉 <b>تهانينا!</b>\n\n{name_from_user(db_user)} حصلت على مكافأة التحدي الأسبوعي!"
    if result["reward_usd"] > 0:
        text += f"\n💰 +{result['reward_usd']:g}$ في رصيدك."
    if result["reward_points"] > 0:
        text += f"\n⭐ +{result['reward_points']} نقطة ولاء."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")]])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer("🎉 تهانينا!")


# ══════════════ إنذارات السعر ══════════════

PRICE_ALERT_CANCEL_STATES: dict = {}


@router.callback_query(F.data == "engage:price_alerts")
async def engage_price_alerts(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("price_alerts"):
        await callback.answer("هذه الميزة معطلة.", show_alert=True)
        return
    from services.price_alert_service import PriceAlertService

    alerts = await PriceAlertService.user_alerts(session, db_user.id)
    lines = ["🔔 <b>إنذارات السعر الخاصة بك</b>\n"]
    if alerts:
        for a in alerts[:10]:
            mark = "🟢" if a.status == "active" else "⏸" if a.status == "paused" else "✅"
            lines.append(
                f"{mark} {a.service_code}/{a.country_code} ← {a.target_price_usd:g}$"
            )
        lines.append(f"\n({len(alerts)} تنبيه نشط)")
    else:
        lines.append("لا تنبيهات بعد. أضف تنبيهاً ليصلك إشعار فور انخفاض السعر.")
    buttons = [
        [InlineKeyboardButton(text="➕ تنبيه جديد", callback_data="engage:alert_new")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")],
    ]
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data == "engage:alert_new")
async def engage_alert_new(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    await state.set_state(PriceAlertFSM.waiting_service)
    await callback.message.answer(
        "أرسل الخدمة (مثلاً: whatsapp) والدولة (مثلاً: turkey) والسعر المستهدف بالدولار:\n\n"
        "<code>whatsapp turkey 3.5</code>\n\nأو أرسل «إلغاء»:",
        reply_markup=_back(),
    )
    await callback.answer()


@router.message(PriceAlertFSM.waiting_service)
async def engage_alert_save(message: Message, session, db_user: User, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in {"إلغاء", "cancel", "الغاء"}:
        await state.clear()
        await message.answer("تم الإلغاء.")
        return
    parts = text.split()
    if len(parts) < 3:
        await message.answer("الصيغة: <code>service country price</code>\nمثال: <code>whatsapp turkey 3.5</code>")
        return
    service_code, country_code, price_str = parts[0], parts[1], parts[2]
    try:
        price = Decimal(price_str)
    except (InvalidOperation, ValueError):
        await message.answer("السعر يجب أن يكون رقماً صالحاً.")
        return
    if price <= 0:
        await message.answer("السعر يجب أن يكون أكبر من صفر.")
        return
    from services.price_alert_service import PriceAlertError, PriceAlertService

    try:
        alert = await PriceAlertService.add_alert(
            session, db_user.id, service_code.lower(), country_code.lower(), price
        )
    except PriceAlertError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        f"✅ تم إنشاء التنبيه: <b>{service_code}/{country_code}</b> ← {price:g}$\n"
        "سنُشعرك فور انخفض السعر.",
        reply_markup=_back(),
    )


# ══════════════ سوق الأرقام المستعملة ══════════════


@router.callback_query(F.data == "engage:resale")
async def engage_resale(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("number_resale_market"):
        await callback.answer("هذه الميزة معطلة.", show_alert=True)
        return
    from services.resale_service import ResaleService

    listings = await ResaleService.open_listings(session, exclude_seller_id=db_user.id)
    lines = ["🔄 <b>سوق الأرقام المستعملة</b>\n"]
    if listings:
        for listing in listings[:8]:
            lines.append(
                f"📱 {listing.service_name} ({listing.country_name}) — "
                f"{listing.price_usd:g}$\n"
                f"   <code>{listing.phone_number}</code>"
            )
    else:
        lines.append("لا أرقام مستعملة للبيع حالياً.")
    buttons = [
        [InlineKeyboardButton(text="➕ عرض رقم للبيع", callback_data="engage:resale_sell")],
        [InlineKeyboardButton(text="📥 إعلاناتي", callback_data="engage:resale_mine")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")],
    ]
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data == "engage:resale_mine")
async def engage_resale_mine(callback: CallbackQuery, session, db_user: User):
    from services.resale_service import ResaleService

    my = await ResaleService.seller_listings(session, db_user.id)
    lines = ["📥 <b>إعلاناتي</b>\n"]
    if my:
        for l in my[:10]:
            mark = "🟢" if l.status == "open" else "✅" if l.status == "sold" else "❌"
            lines.append(f"{mark} {l.service_name} — {l.price_usd:g}$ [{l.status}]")
    else:
        lines.append("لا إعلانات لك بعد.")
    buttons = [[InlineKeyboardButton(text="➕ عرض رقم", callback_data="engage:resale_sell")]]
    buttons.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="engage:resale")])
    await callback.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data == "engage:resale_sell")
async def engage_resale_sell(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    await state.set_state(ResaleFSM.waiting_details)
    await callback.message.answer(
        "أرسل بيانات الرقم المراد بيعه بصيغة:\n\n"
        "<code>رقم_الطلب service_name country_name السعر</code>\n\n"
        "مثال:\n<code>12345 whatsapp turkey 2.5</code>\n\n"
        "أو أرسل «إلغاء»:",
        reply_markup=_back(),
    )
    await callback.answer()


@router.message(ResaleFSM.waiting_details)
async def engage_resale_save(message: Message, session, db_user: User, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in {"إلغاء", "cancel", "الغاء"}:
        await state.clear()
        await message.answer("تم الإلغاء.")
        return
    parts = text.split()
    if len(parts) < 4:
        await message.answer("الصيغة: <code>order_id service country price</code>")
        return
    try:
        source_order_id = int(parts[0])
        price = Decimal(parts[3])
    except (ValueError, InvalidOperation):
        await message.answer("رقم الطلب يجب أن يكون عدداً، والسعر رقماً.")
        return
    from services.resale_service import ResaleError, ResaleService

    try:
        listing = await ResaleService.create_listing(
            session, db_user.id, source_order_id, parts[1], parts[2], price
        )
    except ResaleError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        f"✅ تم عرض الرقم للبيع بسعر <b>{price:g}$</b>.",
        reply_markup=_back(),
    )


@router.callback_query(F.data.startswith("engage:resale_buy:"))
async def engage_resale_buy(callback: CallbackQuery, session, db_user: User):
    listing_id = int(callback.data.split(":")[3])
    from services.resale_service import ResaleError, ResaleService

    try:
        result = await ResaleService.buy_listing(session, listing_id, db_user.id)
    except ResaleError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("✅ تمت العملية بنجاح!")
    await callback.message.edit_text(
        f"✅ <b>تم شراء الرقم بنجاح!</b>\n\n"
        f"📱 الخدمة: {result['service_name']}\n"
        f"📞 الرقم: <code>{result['phone_number']}</code>\n"
        f"💰 المبلغ: {result['price_usd']:g}$\n\n"
        f"🆕 أرسل الرقم للبائع @{result.get('seller_username', '—')} عبر واتساب أو الأداة المفضلة.",
        reply_markup=_back(),
    )


# ══════════════ ملفي الشخصي المفصل ══════════════


@router.callback_query(F.data == "engage:reviews")
async def engage_reviews(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("provider_reviews"):
        await callback.answer("هذه الميزة معطلة.", show_alert=True)
        return
    from services.provider_review_service import ProviderReviewService

    reviews = await ProviderReviewService.user_reviews(session, db_user.id, limit=10)
    lines = ["⭐ <b>تقييماتي للمزودين</b>\n"]
    if reviews:
        for r in reviews:
            stars = "⭐" * r.rating
            comment = f" — {r.comment[:40]}..." if r.comment else ""
            lines.append(f"{stars} {r.provider} (طلب #{r.order_id}){comment}")
    else:
        lines.append("لم تُقيّم أي مزود بعد. سيصلك طلب تقييم بعد كل طلب مكتمل.")
    buttons = [
        [InlineKeyboardButton(text="⭐ تقييم جديد", callback_data="engage:review_pick")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")],
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "engage:review_pick")
async def engage_review_pick(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("provider_reviews"):
        await callback.answer("هذه الميزة معطلة.", show_alert=True)
        return
    from sqlalchemy import select
    from database.models import NumberOrder, OrderStatus

    result = await session.execute(
        select(NumberOrder)
        .where(NumberOrder.user_id == db_user.id, NumberOrder.status == OrderStatus.COMPLETED)
        .order_by(NumberOrder.id.desc())
        .limit(5)
    )
    orders = list(result.scalars().all())
    lines = ["⭐ <b>قيّم أحد طلباتك المكتملة</b>\n"]
    buttons = []
    for o in orders:
        lines.append(f"• #{o.id} — {o.service} ({o.country_code})")
        buttons.append(
            [InlineKeyboardButton(text=f"⭐ #{o.id} {o.service}", callback_data=f"engage:review_order:{o.id}", style="primary")]
        )
    buttons.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="engage:reviews")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("engage:review_order:"))
async def engage_review_order(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    if not await _enabled("provider_reviews"):
        await callback.answer("هذه الميزة معطلة.", show_alert=True)
        return
    order_id = int(callback.data.split(":")[3])
    from database.models import NumberOrder, OrderStatus
    from services.provider_review_service import ProviderReviewService

    order = await session.get(NumberOrder, order_id)
    if order is None or order.user_id != db_user.id:
        await callback.answer("طلب غير موجود.", show_alert=True)
        return
    if order.status != OrderStatus.COMPLETED:
        await callback.answer("هذا الطلب لم يكتمل بعد.", show_alert=True)
        return
    if await ProviderReviewService.already_reviewed(session, db_user.id, "number", order_id):
        await callback.answer("قيمت هذا الطلب من قبل.", show_alert=True)
        return
    await state.update_data(review_order_id=order_id, review_provider=order.provider or order.service)
    await state.set_state(ReviewFSM.waiting_rating)
    await callback.message.answer(
        f"⭐ <b>قيّم الطلب #{order_id}</b> — {order.provider or order.service}\n\n"
        "أرسل تقييمك من 1 إلى 5 (5 = ممتاز):",
        reply_markup=_back(),
    )
    await callback.answer()


@router.message(ReviewFSM.waiting_rating)
async def engage_review_rating(message: Message, session, db_user: User, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in {"إلغاء", "cancel", "الغاء"}:
        await state.clear()
        await message.answer("تم الإلغاء.")
        return
    try:
        rating = int(text)
    except (ValueError, TypeError):
        await message.answer("أرسل رقماً بين 1 و 5.")
        return
    if rating < 1 or rating > 5:
        await message.answer("أرسل رقماً بين 1 و 5.")
        return
    await state.update_data(review_rating=rating)
    await state.set_state(ReviewFSM.waiting_comment)
    await message.answer(
        "أرسل تعليقاً اختيارياً على تجربتك، أو أرسل «تخطي» (قد يُرسل بشكل مجهول):"
    )


@router.message(ReviewFSM.waiting_comment)
async def engage_review_comment(message: Message, session, db_user: User, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in {"تخطي", "skip", "لا"}:
        comment = None
    else:
        comment = text[:500]
    data = await state.get_data()
    from services.provider_review_service import ReviewError

    try:
        from services.provider_review_service import ProviderReviewService

        await ProviderReviewService.add_review(
            session,
            user_id=db_user.id,
            provider=data.get("review_provider") or "—",
            order_type="number",
            order_id=data.get("review_order_id") or 0,
            rating=int(data.get("review_rating") or 5),
            comment=comment,
        )
    except ReviewError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(
        "✅ <b>شكراً لك! تم حفظ تقييمك بنجاح.</b>\n"
        "سيساعد تقييمك المستخدمين الآخرين على الاختيار.",
        reply_markup=_back(),
    )


@router.callback_query(F.data == "engage:profile")
async def engage_profile(callback: CallbackQuery, session, db_user: User):
    from services.user_profile_service import UserProfileService

    text = await UserProfileService.render_profile(session, db_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="back_to_main")]])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "engage:report")
async def engage_report(callback: CallbackQuery, session, db_user: User):
    if not await _enabled("monthly_report"):
        await callback.answer("هذه الميزة معطلة.", show_alert=True)
        return
    from services.monthly_report_service import MonthlyReportService

    text = await MonthlyReportService.render(session, db_user.id)
    if text is None:
        text = "📊 <b>التقرير الشهري</b>\n\nلم تنفّذ أي طلبات الشهر الماضي بعد."
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="extras:home")]])
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ══════════════ أدوات مساعدة ══════════════


def name_from_user(user: User) -> str:
    return user.full_name or user.username or f"مستخدم {user.id}"
