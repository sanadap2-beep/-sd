"""
الملف الرئيسي لتشغيل البوت.
"""

import asyncio
import logging

from aiogram import Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from services.html_guard import HtmlGuardedBot
from database.engine import async_session_maker
from database.seed import init_db

from middlewares.db_session import DbSessionMiddleware
from middlewares.user_middleware import UserMiddleware
from middlewares.subscription_middleware import SubscriptionMiddleware
from middlewares.state_reset_middleware import StateResetMiddleware
from middlewares.throttling import GlobalThrottlingMiddleware
from middlewares.error_middleware import ErrorReportingMiddleware, install_asyncio_exception_handler

from handlers import (
    start,
    support,
    account,
    agent,
    cart,
    referral,
    referral_guard,
    deposit,
    transfer,
    withdrawal,
    notifications,
    bot_info,
    sponsored_ads,
    special_offers,
    numbers,
    loyalty,
    promotions,
    product_requests,
    gift,
    assistant,
    status,
    reviews,
    webapp,
    language,
    currency,
    challenges,
    marketplace as user_marketplace,
    tasks as user_tasks,
    points as user_points,
    extras as user_extras,
    store as user_store,
    inline_search,
    fallback,
)
from handlers.deposit_methods import router as deposit_methods_router
from handlers.games import router as games_router
from handlers.admin import (
    panel as admin_panel,
    broadcast as admin_broadcast,
    channels as admin_channels,
    countries as admin_countries,
    deposits as admin_deposits,
    gift_codes as admin_gift_codes,
    users as admin_users,
    pricing as admin_pricing,
    providers as admin_providers,
    stats as admin_stats,
    settings as admin_settings,
    support as admin_support,
    categories as admin_categories,
    products as admin_products,
    smm_products as admin_smm_products,
    api_providers as admin_api_providers,
    audit as admin_audit,
    health as admin_health,
    inventory as admin_inventory,
    loyalty as admin_loyalty,
    promotions as admin_promotions,
    coupons as admin_coupons,
    multi_admin as admin_multi_admin,
    number_services as admin_number_services,
    store_servers as admin_store_servers,
    number_orders as admin_number_orders,
    features as admin_features,
    tasks_center as admin_tasks_center,
    marketplace as admin_marketplace,
    points as admin_points,
    operations as admin_ops,
    bonuses as admin_bonuses,
    orders as admin_orders,
    product_requests as admin_product_requests,
    stars as admin_stars,
    bot_guide as admin_bot_guide,
    main_buttons as admin_main_buttons,
    store_control as admin_store_control,
    extras_control as admin_extras_control,
    agents as admin_agents,
    margins as admin_margins,
    withdrawals as admin_withdrawals,
    notifications as admin_notifications,
    sponsored_ads as admin_sponsored_ads,
    special_offers as admin_special_offers,
    pulled_services as admin_pulled_services,
    ledger as admin_ledger,
    partner_catalog as admin_partner_catalog,
    live_feed as admin_live_feed,
)

from tasks.order_monitor import (
    check_pending_orders,
    update_provider_status,
    cleanup_balance_locks,
)
from tasks.unified_order_monitor import check_unified_orders
from tasks.invoice_monitor import check_pending_invoices
from tasks.watch_job import check_product_watches
from tasks.backup_job import daily_backup
from tasks.sponsored_ads_job import process_sponsored_ads
from tasks.special_offers_job import process_special_offers
from services.feature_service import FeatureService
from services.smm_sections_service import SmmSectionsService
from services.subscriptions_sync_service import SubscriptionsSyncService
from services.marketplace_service import MarketplaceService
from services.refill_service import RefillService
from services.drip_feed_service import DripFeedService
from services.ai_layer_service import AutonomousPurchaseService
from services.marketplace_ext_service import EscrowService
from services.subscription_lifecycle_service import SubscriptionLifecycleService
from services.operations_service import CatalogAutopilotService, FxFeedService
from services.platform_service import ProviderBiddingService
from services.bot_command_service import SentinelService
from services.task_service import TaskService
from services.observability import init_observability
from services.plisio_service import plisio_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
init_observability()

bot = HtmlGuardedBot(
    token=settings.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
storage = RedisStorage.from_url(settings.REDIS_URL) if settings.REDIS_URL else MemoryStorage()
dp = Dispatcher(storage=storage)


def register_middlewares():
    db_mw = DbSessionMiddleware()
    user_mw = UserMiddleware()
    state_reset_mw = StateResetMiddleware()
    throttle_mw = GlobalThrottlingMiddleware()
    sub_mw = SubscriptionMiddleware(bot)
    error_mw = ErrorReportingMiddleware()

    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(error_mw)
        observer.outer_middleware(db_mw)
        observer.outer_middleware(user_mw)
        observer.outer_middleware(state_reset_mw)
        observer.outer_middleware(throttle_mw)
        observer.outer_middleware(sub_mw)


def register_routers():
    # ── هاندلرز المستخدم ──
    dp.include_router(start.router)
    dp.include_router(support.router)
    dp.include_router(account.router)
    dp.include_router(cart.router)
    dp.include_router(referral.router)
    dp.include_router(referral_guard.router)
    dp.include_router(deposit.router)
    dp.include_router(deposit_methods_router)
    dp.include_router(transfer.router)
    dp.include_router(withdrawal.router)
    dp.include_router(notifications.router)
    dp.include_router(bot_info.router)
    dp.include_router(sponsored_ads.router)
    dp.include_router(special_offers.router)
    dp.include_router(numbers.router)
    dp.include_router(agent.router)
    dp.include_router(loyalty.router)
    dp.include_router(promotions.router)
    dp.include_router(product_requests.router)
    dp.include_router(gift.router)
    dp.include_router(assistant.router)
    dp.include_router(status.router)
    dp.include_router(reviews.router)
    dp.include_router(webapp.router)
    dp.include_router(language.router)
    dp.include_router(currency.router)
    dp.include_router(challenges.router)
    dp.include_router(user_marketplace.router)
    dp.include_router(user_tasks.router)
    dp.include_router(user_points.router)
    dp.include_router(user_extras.router)
    dp.include_router(user_store.router)
    dp.include_router(inline_search.router)
    dp.include_router(games_router)

    # ── هاندلرز الأدمن ──
    dp.include_router(admin_panel.router)
    dp.include_router(admin_broadcast.router)
    dp.include_router(admin_channels.router)
    dp.include_router(admin_countries.router)
    dp.include_router(admin_deposits.router)
    dp.include_router(admin_gift_codes.router)
    dp.include_router(admin_users.router)
    dp.include_router(admin_pricing.router)
    dp.include_router(admin_providers.router)
    dp.include_router(admin_stats.router)
    dp.include_router(admin_ledger.router)
    dp.include_router(admin_settings.router)
    dp.include_router(admin_support.router)
    dp.include_router(admin_categories.router)
    dp.include_router(admin_products.router)
    dp.include_router(admin_smm_products.router)
    dp.include_router(admin_api_providers.router)
    dp.include_router(admin_pulled_services.router)
    dp.include_router(admin_partner_catalog.router)
    dp.include_router(admin_audit.router)
    dp.include_router(admin_health.router)
    dp.include_router(admin_inventory.router)
    dp.include_router(admin_loyalty.router)
    dp.include_router(admin_promotions.router)
    dp.include_router(admin_coupons.router)
    dp.include_router(admin_multi_admin.router)
    dp.include_router(admin_number_services.router)
    dp.include_router(admin_store_servers.router)
    dp.include_router(admin_number_orders.router)
    dp.include_router(admin_features.router)
    dp.include_router(admin_tasks_center.router)
    dp.include_router(admin_marketplace.router)
    dp.include_router(admin_points.router)
    dp.include_router(admin_ops.router)
    dp.include_router(admin_bonuses.router)
    dp.include_router(admin_orders.router)
    dp.include_router(admin_product_requests.router)
    dp.include_router(admin_stars.router)
    dp.include_router(admin_bot_guide.router)
    dp.include_router(admin_main_buttons.router)
    dp.include_router(admin_store_control.router)
    dp.include_router(admin_extras_control.router)
    dp.include_router(admin_agents.router)
    dp.include_router(admin_margins.router)
    dp.include_router(admin_withdrawals.router)
    dp.include_router(admin_notifications.router)
    dp.include_router(admin_sponsored_ads.router)
    dp.include_router(admin_special_offers.router)
    dp.include_router(admin_live_feed.router)

    # آخر Router دائماً: يلتقط أي زر غير مربوط بدل أن يسكت البوت.
    dp.include_router(fallback.router)


async def refill_guarantee_cycle(bot):
    """ضمان التعويض الآلي: يعوّض هبوط المتابعين خلال فترة الضمان."""
    from database.engine import async_session_maker

    if not await RefillService.enabled():
        return
    async with async_session_maker() as session:
        await RefillService.run_cycle(session, bot=bot)


async def marketplace_maintenance_cycle():
    """يُفرج عن العمليات المستحقة ويُنتهي الإعلانات منتهية الصلاحية."""
    from database.engine import async_session_maker

    async with async_session_maker() as session:
        if await FeatureService.enabled("peer_marketplace"):
            released = await MarketplaceService.auto_release_due(session)
            expired = await MarketplaceService.expire_listings(session)
            if released or expired:
                logger.info("صيانة السوق: أُفرج %s، انتهى %s", len(released), expired)


async def drip_feed_cycle():
    """ينفّذ دفعات التدريج التي حلّ موعدها."""
    from database.engine import async_session_maker

    if not await FeatureService.enabled("drip_feed"):
        return
    async with async_session_maker() as session:
        await DripFeedService.run_due(session)


async def autonomous_purchase_cycle(bot):
    """ينفّز دورات وكيل الشراء المستقل، مع فحص السعر قبل الشراء."""
    from database.engine import async_session_maker

    if not await FeatureService.enabled("autonomous_purchase_agent"):
        return
    async with async_session_maker() as session:
        await AutonomousPurchaseService.run_due(session, bot=bot)


async def escrow_expiry_cycle():
    """يفرج عن الحجوزات التي علقت بلا حسم حتى لا تبقى الأموال مجمّدة."""
    from database.engine import async_session_maker

    if not await FeatureService.enabled("escrow_engine"):
        return
    async with async_session_maker() as session:
        released = await EscrowService.expire_stale(session, 72)
        if released:
            logger.info("أُفرج عن %s حجز ضمان منتهي المهلة.", released)


async def subscription_cycle(bot):
    """ينبّه الاشتراكات المنتهية قريباً ويجدد لمن فعّل التجديد."""
    from database.engine import async_session_maker

    if not await FeatureService.enabled("subscription_lifecycle"):
        return
    async with async_session_maker() as session:
        await SubscriptionLifecycleService.run_cycle(session, bot=bot)


async def catalog_autopilot_cycle():
    """يعطّل المنتجات المعطوبة ويبلّغ عن فرص المراجحة."""
    from database.engine import async_session_maker

    if not await FeatureService.enabled("catalog_autopilot"):
        return
    async with async_session_maker() as session:
        disabled = await CatalogAutopilotService.disable_deleted_services(session)
        if disabled:
            logger.info("عطّل الكتالوج الذاتي %s منتجاً.", len(disabled))


async def fx_refresh_cycle():
    """يحدّث أسعار الصرف من مصدر خارجي بدل الإدخال اليدوي."""
    from database.engine import async_session_maker

    if not await FeatureService.enabled("live_fx_feed"):
        return
    async with async_session_maker() as session:
        await FxFeedService.refresh(session)


async def sentinel_cycle(bot):
    """يراقب معدل الأخطاء ويدخل الوضع الآمن قبل أن تتفاقم الخسارة."""
    from database.engine import async_session_maker

    if not await SentinelService.enabled():
        return
    async with async_session_maker() as session:
        result = await SentinelService.check(session)
        action = result.get("action")

        if action == "engaged":
            disabled = result.get("disabled", [])
            logger.warning("🛡 الحارس أدخل البوت الوضع الآمن: %s", disabled)
            from services.notification_service import NotificationService

            bullets = "".join(f"• {key}\n" for key in disabled)
            await NotificationService(bot).notify_admin(
                "🛡 <b>الحارس الذاتي فعّل الوضع الآمن</b>\n\n"
                f"معدل الأخطاء: {result.get('error_rate')}\n"
                f"عُطّلت {len(disabled)} ميزة خطرة:\n"
                f"{bullets}\n"
                "ستُعاد تلقائياً حين يستقر المعدل."
            )
        elif action == "disengaged":
            restored = result.get("restored", [])
            logger.info("🛡 الحارس أخرج البوت من الوضع الآمن.")
            from services.notification_service import NotificationService

            await NotificationService(bot).notify_admin(
                "✅ <b>استقر الوضع — خرج البوت من الوضع الآمن</b>\n\n"
                f"أُعيدت {len(restored)} ميزة."
            )


async def bid_cleanup_cycle():
    """ينظّف عروض المزايدة المنتهية."""
    from database.engine import async_session_maker

    if not await FeatureService.enabled("provider_bidding"):
        return
    async with async_session_maker() as session:
        await ProviderBiddingService.cleanup_expired(session)


async def agent_weekly_cycle(bot):
    """فحص أسبوعي لبرنامج الوكلاء: سحب من أقل إيداعاته الأسبوعية من الحد."""
    from database.engine import async_session_maker
    from services.agent_service import AgentService

    if not await FeatureService.enabled("agent_program"):
        return
    try:
        async with async_session_maker() as session:
            await AgentService.check_weekly_deposits(session, bot)
    except Exception:
        logger.exception("فشل فحص الوكلاء الأسبوعي")


async def availability_board_cycle(bot):
    """التوفر المتقطع: يحدّث لوحة الدول الجاهزة في القناة الحية كل دورة.

    كل دورة تُقرأ أحدث بيانات المزود فيرى المستخدم ما هو متوفر الآن فعلاً
    (دول تتوفر فجأة تظهر فوراً وتختفي الدول التي نفدت). التحديث يتم
    بتعديل نفس الرسالة، لكن اللوحة تُعاد نشرها كرسالة جديدة كل عدد محدد
    من الدورات (repost_every_cycles) — لأن تعديل رسالة في Telegram لا
    يُصدر إشعاراً ويبقى مدفوناً تحت أي منشور أحدث — فتظهر بأعلى القناة
    ويصل المشتركين إشعار فعلي.
    """
    if not await FeatureService.enabled("numbers_availability_board"):
        return
    from services.availability_board_service import AvailabilityBoardService

    try:
        await AvailabilityBoardService.post_board(bot)
    except Exception:
        logger.exception("فشل نشر لوحة التوفر المتقطع")


async def prune_feature_events():
    """يحذف أحداث القياس القديمة حسب المدة التي حددها الأدمن."""
    days = await FeatureService.config_int("feature_usage_analytics", "retention_days", 90)
    removed = await FeatureService.prune_events(days)
    if removed:
        logger.info("حُذفت %s من أحداث القياس القديمة.", removed)


async def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    order_poll_seconds = max(
        3,
        await FeatureService.config_int(
            "instant_delivery", "poll_interval_seconds", 15
        ),
    )
    scheduler.add_job(
        check_pending_orders,
        "interval",
        seconds=order_poll_seconds,
        args=[bot],
    )

    scheduler.add_job(
        check_unified_orders,
        "interval",
        minutes=2,
        args=[bot],
    )

    scheduler.add_job(
        check_pending_invoices,
        "interval",
        seconds=max(5, settings.PLISIO_POLLING_INTERVAL_SECONDS),
        args=[bot],
    )

    scheduler.add_job(
        update_provider_status,
        "interval",
        minutes=10,
        args=[bot],
    )

    scheduler.add_job(
        check_product_watches,
        "interval",
        minutes=10,
        args=[bot],
    )

    scheduler.add_job(
        cleanup_balance_locks,
        "interval",
        minutes=5,
    )

    scheduler.add_job(
        daily_backup,
        "cron",
        hour=3,
        minute=0,
        args=[bot],
    )

    scheduler.add_job(
        refill_guarantee_cycle,
        "interval",
        hours=6,
        args=[bot],
    )

    scheduler.add_job(
        marketplace_maintenance_cycle,
        "interval",
        minutes=30,
    )

    scheduler.add_job(
        prune_feature_events,
        "cron",
        hour=4,
        minute=30,
    )

    scheduler.add_job(
        drip_feed_cycle,
        "interval",
        minutes=5,
    )

    scheduler.add_job(
        autonomous_purchase_cycle,
        "interval",
        minutes=15,
        args=[bot],
    )

    scheduler.add_job(
        escrow_expiry_cycle,
        "interval",
        hours=1,
    )

    scheduler.add_job(
        subscription_cycle,
        "cron",
        hour=8,
        minute=0,
        args=[bot],
    )

    scheduler.add_job(
        catalog_autopilot_cycle,
        "interval",
        hours=6,
    )

    scheduler.add_job(
        fx_refresh_cycle,
        "interval",
        minutes=60,
    )

    scheduler.add_job(
        bid_cleanup_cycle,
        "interval",
        minutes=30,
    )

    # التوفر المتقطع: كل دورة (افتراضياً دقيقة) تُحذف اللوحة وتُنشأ بأحدث
    # الدول الجاهزة فوراً من المزود.
    scheduler.add_job(
        availability_board_cycle,
        "interval",
        seconds=max(
            30,
            await FeatureService.config_int(
                "numbers_availability_board", "refresh_seconds", 60
            ),
        ),
        args=[bot],
    )

    # برنامج الوكلاء: فحص دوري (افتراضياً كل 6 ساعات) لسحب وكالات
    # الوكلاء الذين أقل إيداعهم الأسبوعي من الحد.
    scheduler.add_job(
        agent_weekly_cycle,
        "interval",
        hours=max(
            1,
            await FeatureService.config_int("agent_program", "check_interval_hours", 6),
        ),
        args=[bot],
    )

    scheduler.add_job(
        sentinel_cycle,
        "interval",
        minutes=5,
        args=[bot],
    )

    scheduler.add_job(
        process_sponsored_ads,
        "interval",
        minutes=5,
        args=[bot],
    )

    scheduler.add_job(
        process_special_offers,
        "interval",
        minutes=5,
        args=[bot],
    )

    scheduler.start()
    return scheduler


async def main():
    logger.info("⏳ جاري تهيئة قاعدة البيانات...")
    await init_db()
    await FeatureService.sync_registry()
    await FeatureService.reload()
    # ترميم أسماء الدول الأجنبية (من المزود) إلى العربية + العلم الصحيح.
    # لا يلمس أكواد الربط مع المزودين إطلاقاً.
    try:
        from services.country_localization_service import heal_countries

        async with async_session_maker() as session:
            healed = await heal_countries(session)
            if healed:
                logger.info("🌍 عُرّبت %s دولة أجنبية.", healed)
    except Exception:
        logger.exception("فشل ترميم أسماء الدول")
    async with async_session_maker() as session:
        await TaskService.seed_defaults(session)

    # ── بناء أقسام الرشق الداخلية تلقائياً ──
    # ينشئ لكل تطبيق أقسامه (متابعون/لايكات/مشاهدات...) من الخدمات المسحوبة
    # وينشر أرخص 5 خدمات بكل قسم. Idempotent: لا يكرر ولا يمس المنتجات اليدوية.
    try:
        async with async_session_maker() as session:
            if await SmmSectionsService.auto_build_enabled():
                report = await SmmSectionsService.build(session)
                logger.info("🚀 البناء التلقائي لأقسام الرشق: %s", report)
    except Exception:
        logger.exception("فشل البناء التلقائي لأقسام الرشق عند الإقلاع")

    # ── مزامنة الاشتراكات الرقمية (ggsoma) تلقائياً ──
    # يسحب كتالوج المزود وينشر منتجاته في قسم الاشتراكات بسعر التكلفة +
    # هامش الربح المحدد. Idempotent: لا يكرر ولا يمس المنتجات اليدوية.
    try:
        async with async_session_maker() as session:
            if await SubscriptionsSyncService.enabled() and await SubscriptionsSyncService.auto_on_startup():
                reports = await SubscriptionsSyncService.sync_all(session)
                for report in reports:
                    logger.info("🛍 مزامنة الاشتراكات: %s", report)
    except Exception:
        logger.exception("فشل مزامنة الاشتراكات الرقمية عند الإقلاع")

    logger.info(f"🔑 آيديات الأدمن: {settings.admin_ids_list}")
    logger.info("✅ قاعدة البيانات جاهزة.")

    register_middlewares()
    register_routers()
    install_asyncio_exception_handler(bot)
    scheduler = await start_scheduler()

    logger.info("🚀 البوت يعمل الآن...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await plisio_client.close()
        await bot.session.close()
        if hasattr(storage, "close"):
            await storage.close()
        logger.info("🛑 البوت توقف.")


if __name__ == "__main__":
    asyncio.run(main())
