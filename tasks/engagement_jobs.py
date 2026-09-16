"""
المهام المجدولة لميزات التفاعل الجديدة:

- التقرير الشهري الشخصي (send_monthly_reports)
- إشعار انتهاء العروض الخاصة (offer_expiry_cycle)
- إنذار انخفاض السعر (price_alert_cycle)
- تجديد التحدي الأسبوعي (weekly_challenge_cycle)

كل مهمة آمنة: تبدأ بفحص تفعيل الميزة من السجل، ولا تكرر أي إشعار
(حُرّاس منع التكرار في الخدمات نفسها).
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from database.engine import async_session_maker
from database.models import User

logger = logging.getLogger(__name__)


async def send_monthly_reports(bot):
    """في اليوم المحدد يرسل تقرير الشهر السابق لكل مستخدم نشط."""
    from services.feature_service import FeatureService

    if not await FeatureService.enabled("monthly_report"):
        return
    send_day = int(await FeatureService.config("monthly_report", "send_day_of_month", 1))
    if datetime.utcnow().day != send_day:
        return

    from services.monthly_report_service import MonthlyReportService
    from services.notification_service import NotificationService

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.is_activated.is_(True)))
        users = list(result.scalars().all())

    notifier = NotificationService(bot)
    sent = skipped = 0
    for user in users:
        async with async_session_maker() as session:
            text = await MonthlyReportService.render(session, user.id)
        if not text:
            skipped += 1
            continue
        try:
            await notifier.notify_user(telegram_id=user.telegram_id, text=text)
            sent += 1
        except Exception:
            logger.exception("فشل إرسال التقرير الشهري إلى %s", user.telegram_id)
    logger.info("التقرير الشهري: أُرسل لـ %s مستخدم، لا نشاط لـ %s", sent, skipped)


async def offer_expiry_cycle(bot):
    """يذكّر مجتمع المستخدمين عروضاً خاصة ستنتهي خلال نافذة ينظمها الأدمن."""
    from services.notification_service import NotificationService
    from services.offer_expiry_service import OfferExpiryService

    async with async_session_maker() as session:
        due = await OfferExpiryService.offers_expiring_soon(session)
    if not due:
        return

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.is_activated.is_(True)))
        users = list(result.scalars().all())

    notifier = NotificationService(bot)
    for item in due:
        offer = item["offer"]
        mins = item["minutes_left"]
        text = (
            "⏰ <b>انتهاء وشيك لعرض خاص!</b>\n\n"
            f"🔥 <b>{offer.name}</b>\n"
            f"💸 السعر: <b>{offer.price_usd:g}$</b>\n"
            f"⏳ يتبقى {mins} دقيقة فقط!\n\n"
            "اطلب الآن قبل انتهاء الفرصة — من قسم «العروض الخاصة» في قائمة المتجر."
        )
        for user in users:
            try:
                await notifier.notify_user(telegram_id=user.telegram_id, text=text)
            except Exception:
                logger.exception("فشل إرسال تنبيه انتهاء العرض إلى %s", user.telegram_id)
        async with async_session_maker() as session:
            await OfferExpiryService.mark_reminded(session, offer.id, close_to_end=mins <= 180)
        logger.info("أُرسل تنبيه انتهاء العرض #%s (%s دقيقة)", offer.id, mins)


async def price_alert_cycle(bot):
    """يفحص تنبيهات الأسعار النشطة مقابل السعر الفعلي للبيع ويُشعر المستخدمين."""
    from services.feature_service import FeatureService

    if not await FeatureService.enabled("price_alerts"):
        return

    from services.notification_service import NotificationService
    from services.price_alert_service import PriceAlertService
    from providers.countries import get_country_by_code, get_number_service_by_code
    from providers.manager import provider_manager
    from services.pricing_service import PricingService

    async with async_session_maker() as session:
        alerts = await PriceAlertService.active_alerts(session)

    notifier = NotificationService(bot)
    for alert in alerts:
        async with async_session_maker() as session:
            service = await get_number_service_by_code(session, alert.service_code)
            country = await get_country_by_code(session, alert.country_code)
            if service is None or country is None:
                continue
            try:
                prices = await provider_manager.get_cheapest_price(service, country, session)
                current = None
                if prices:
                    provider = min(prices, key=prices.get)
                    current = await PricingService.calculate_sell_price(
                        session, alert.service_code, alert.country_code, provider, prices[provider]
                    )
            except Exception:
                logger.exception("فشل فحص سعر التنبيه %s", alert.id)
                continue
            if not current:
                continue
            triggered = await PriceAlertService.check_price(session, alert.id, current)
        if not triggered:
            continue
        try:
            await notifier.notify_user(
                telegram_id=alert.user_id,
                text=(
                    "🔔 <b>انخفض السعر!</b>\n\n"
                    f"📲 الخدمة: <b>{alert.service_code}</b> / <b>{alert.country_code}</b>\n"
                    f"💰 السعر الآن: <b>{current:g}$</b> (هدفك: {alert.target_price_usd:g}$)\n\n"
                    "افتح قسم الأرقام واشترِ باللحظة المناسبة!"
                ),
            )
        except Exception:
            logger.exception("فشل إرسال إشعار تنبيه السعر %s", alert.id)
        else:
            logger.info("تحقق تنبيه السعر %s للمستخدم %s", alert.id, alert.user_id)


async def weekly_challenge_cycle():
    """إذا بدأ أسبوع جديد ولا تحدٍّ نشط ينسخ آخر تحدٍّ من الأسبوع الماضي (تلقائياً)."""
    from services.feature_service import FeatureService

    if not await FeatureService.enabled("weekly_challenges"):
        return

    from services.weekly_challenge_service import WeeklyChallengeService, _iso_week

    async with async_session_maker() as session:
        active = await WeeklyChallengeService.active_challenge(session)
        if active is not None:
            return
        today = datetime.utcnow().date()
        iso = today.isocalendar()
        prev_week = f"{iso[0]}-W{iso[1]-1:02d}" if iso[1] > 1 else f"{iso[0]-1}-W{52:02d}"
        previous = await WeeklyChallengeService.active_challenge(session, week=prev_week)
        if previous is None:
            return

        await WeeklyChallengeService.create_challenge(
            session,
            title=previous.title,
            description=previous.description,
            emoji=previous.emoji,
            metric=previous.metric,
            target_value=previous.target_value,
            reward_usd=previous.reward_usd,
            reward_points=previous.reward_points,
            week_start=_iso_week(),
        )
        logger.info("استُنسخ التحدي الأسبوعي للأسبوع الجديد")