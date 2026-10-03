"""سلسلة الولاء: خصم متصاعد لمن يكثر الطلب في الشهر.

الزبون الذي يطلب ٣ مرات في الشهر يستحق شيئاً يربطه بالمتجر،
والذي يطلب ١٠ يستحق أكثر. كل مرة يعبر فيها عتبة نمنحه كوبون خصم
شخصياً (ونقاط ولاء) — مرة واحدة لكل عتبة في كل شهر.

العتبات افتراضياً: ٣ طلبات ← ٥٪ · ٦ ← ١٠٪ · ١٠ ← ١٥٪.
كلها قابلة للضبط من لوحة الأدمن.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from database.models import Coupon, LoyaltyEvent, UnifiedOrder, UnifiedOrderStatus, User
from services.coupon_service import CouponService
from services.feature_service import FeatureService
from services.html_guard import esc
from services.loyalty_service import LoyaltyService

logger = logging.getLogger(__name__)

FEATURE_KEY = "loyalty_chain"

DEFAULT_TIERS = ((3, 5), (6, 10), (10, 15))


class LoyaltyChainService:
    """منح كوبونات الخصم حسب عدد طلبات الشهر."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY, default=True)

    @staticmethod
    async def tiers() -> list[tuple[int, int]]:
        """العتبات: (عدد الطلبات، نسبة الخصم) مرتبة تصاعدياً."""
        raw = await FeatureService.config(FEATURE_KEY, "tiers", "3:5,6:10,10:15")
        tiers: list[tuple[int, int]] = []
        try:
            for part in str(raw or "").split(","):
                if ":" not in part:
                    continue
                orders, percent = part.split(":", 1)
                orders_i, percent_i = int(orders.strip()), int(percent.strip())
                if orders_i > 0 and 0 < percent_i <= 90:
                    tiers.append((orders_i, percent_i))
        except (TypeError, ValueError):
            logger.debug("عتبات سلسلة الولاء غير صالحة، نستخدم الافتراضية")
        tiers = sorted(set(tiers)) or list(DEFAULT_TIERS)
        return tiers

    @staticmethod
    def month_key(now: datetime | None = None) -> str:
        return (now or datetime.utcnow()).strftime("%Y-%m")

    @staticmethod
    async def monthly_orders(session, user_id: int, since: datetime) -> int:
        total = await session.scalar(
            select(func.count(UnifiedOrder.id)).where(
                UnifiedOrder.user_id == user_id,
                UnifiedOrder.status == UnifiedOrderStatus.COMPLETED,
                UnifiedOrder.created_at >= since,
            )
        )
        return int(total or 0)

    @staticmethod
    def _month_start(now: datetime | None = None) -> datetime:
        now = now or datetime.utcnow()
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    async def _unique_code(session, user_id: int, percent: int) -> str:
        import secrets

        for _attempt in range(5):
            code = f"LOY{int(user_id) % 10000:04d}{percent}{secrets.token_hex(2).upper()}"
            if await CouponService.get_coupon_by_code(session, code) is None:
                return code
        return f"LOY{int(user_id) % 10000:04d}{percent}{secrets.token_hex(3).upper()}"

    @classmethod
    async def progress(cls, session, user_id: int) -> dict | None:
        """تقدّم المستخدم نحو العتبة القادمة هذا الشهر (للعرض في شاشة الولاء)."""
        if not await cls.enabled():
            return None
        tiers = await cls.tiers()
        now = datetime.utcnow()
        month_start = cls._month_start(now)
        month = cls.month_key(now)
        orders = await cls.monthly_orders(session, user_id, month_start)

        keys = [f"loyalty_chain:{user_id}:{month}:{threshold}" for threshold, _ in tiers]
        found = set(
            (
                await session.execute(
                    select(LoyaltyEvent.event_key).where(LoyaltyEvent.event_key.in_(keys))
                )
            )
            .scalars()
            .all()
        )
        earned = [(t, p) for t, p in tiers if f"loyalty_chain:{user_id}:{month}:{t}" in found]
        next_tier = next(((t, p) for t, p in tiers if orders < t), None)

        coupons = (
            (
                await session.execute(
                    select(Coupon)
                    .where(
                        Coupon.created_by == user_id,
                        Coupon.code.like("LOY%"),
                        Coupon.is_active.is_(True),
                    )
                    .order_by(Coupon.id.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        active = [c for c in coupons if c.expires_at is None or c.expires_at > now]
        return {
            "orders": orders,
            "month": month,
            "tiers": tiers,
            "earned": earned,
            "current_percent": earned[-1][1] if earned else 0,
            "next_tier": next_tier,
            "remaining": (next_tier[0] - orders) if next_tier else 0,
            "coupons": active,
        }

    @staticmethod
    def render_progress(data: dict | None, language: str | None = None) -> str:
        """سلسلة الولاء كما يراها الزبون: أين هو، وباقي كم."""
        if not data:
            return ""
        ar = not str(language or "").startswith("en")
        orders = int(data.get("orders") or 0)
        tiers = data.get("tiers") or []
        earned = data.get("earned") or []
        percent = int(data.get("current_percent") or 0)

        lines = [
            "",
            "🔥 <b>سلسلة الولاء</b> (هذا الشهر)"
            if ar
            else "🔥 <b>Loyalty streak</b> (this month)",
        ]
        if ar:
            lines.append(f"🎯 طلباتك المكتملة: <b>{orders}</b>")
        else:
            lines.append(f"🎯 Completed orders: <b>{orders}</b>")

        # شريط تقدّم بالعتبات
        if tiers:
            top = tiers[-1][0]
            filled = min(len(earned), len(tiers))
            goal = f"0 ← {top}"
            lines.append("▰" * filled + "▱" * (len(tiers) - filled) + f"  ({goal})")

        next_tier = data.get("next_tier")
        if next_tier:
            threshold, next_percent = next_tier
            remaining = int(data.get("remaining") or 0)
            if ar:
                lines.append(
                    f"⏳ باقي <b>{remaining}</b> "
                    f"{'طلب' if remaining == 1 else 'طلبات'} وتفتح لك "
                    f"<b>{next_percent}٪</b> خصم."
                )
            else:
                lines.append(
                    f"⏳ <b>{remaining}</b> more order(s) unlock "
                    f"<b>{next_percent}%</b> off."
                )
        else:
            if ar:
                lines.append("👑 وصلت أعلى عتبة هذا الشهر — أحسنت!")
            else:
                lines.append("👑 You hit the top tier this month — amazing!")

        if percent:
            if ar:
                lines.append(f"🎖 أعلى خصم حققته هذا الشهر: <b>{percent}٪</b>")
            else:
                lines.append(f"🎖 Best discount unlocked: <b>{percent}%</b>")

        coupons = data.get("coupons") or []
        if coupons:
            coupon = coupons[0]
            value = coupon.discount_value
            suffix = "٪" if str(coupon.discount_type) == "percent" else "$"
            until = (
                coupon.expires_at.strftime("%d/%m")
                if coupon.expires_at is not None
                else "—"
            )
            if ar:
                lines.append(
                    f"🎁 كوبونك الجاهز: <code>{esc(coupon.code)}</code> "
                    f"({value}{suffix}) حتى {until}"
                )
            else:
                lines.append(
                    f"🎁 Your coupon: <code>{esc(coupon.code)}</code> "
                    f"({value}{suffix}) until {until}"
                )
        elif not orders and ar:
            lines.append("✨ أول ٣ طلبات في الشهر تفتح لك خصم ٥٪.")
        elif not orders:
            lines.append("✨ Your first 3 orders this month unlock 5% off.")
        return "\n".join(lines)

    @staticmethod
    def compose_message(language: str, code: str, percent: int, orders: int, days: int) -> str:
        ar = not str(language or "").startswith("en")
        if ar:
            return (
                "🔥 <b>سلسلة ولاء</b>\n\n"
                f"وصلت إلى <b>{orders}</b> طلبات هذا الشهر — شكراً لثقتك!\n"
                "هديتك: خصم خاص على طلبك القادم.\n\n"
                f"🏷 الكود: <code>{esc(code)}</code>\n"
                f"💸 الخصم: <b>{percent}%</b>\n"
                f"⏳ صالح {days} يوماً.\n\n"
                "كل ما زادت طلباتك هذا الشهر زاد الخصم 👑"
            )
        return (
            "🔥 <b>Loyalty streak</b>\n\n"
            f"You hit <b>{orders}</b> orders this month — thank you!\n"
            "Your gift: a discount on your next order.\n\n"
            f"🏷 Code: <code>{esc(code)}</code>\n"
            f"💸 Discount: <b>{percent}%</b>\n"
            f"⏳ Valid for {days} days.\n\n"
            "The more you order this month, the bigger the discount 👑"
        )

    @staticmethod
    def markup(language: str) -> InlineKeyboardMarkup:
        ar = not str(language or "").startswith("en")
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛍 متابعة التسوق" if ar else "🛍 Keep shopping",
                        callback_data="store:home",
                        style="success",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🎁 هداياي" if ar else "🎁 My rewards",
                        callback_data="menu:loyalty",
                    )
                ],
            ]
        )

    @classmethod
    async def grant(cls, bot, limit: int = 200) -> int:
        """يفحص طلبات الشهر ويمنح كوبونات العتبات الجديدة."""
        if not await cls.enabled():
            return 0
        from database.engine import async_session_maker
        from services.notification_service import NotificationService

        tiers = await cls.tiers()
        min_orders = tiers[0][0]
        now = datetime.utcnow()
        month_start = cls._month_start(now)
        month = cls.month_key(now)
        try:
            days = int(await FeatureService.config_int(FEATURE_KEY, "coupon_valid_days", 14))
        except (TypeError, ValueError):
            days = 14
        days = max(1, min(days, 90))
        try:
            points = int(await FeatureService.config_int(FEATURE_KEY, "bonus_points", 25))
        except (TypeError, ValueError):
            points = 25

        async with async_session_maker() as session:
            rows = (
                await session.execute(
                    select(UnifiedOrder.user_id, func.count(UnifiedOrder.id))
                    .where(
                        UnifiedOrder.status == UnifiedOrderStatus.COMPLETED,
                        UnifiedOrder.created_at >= month_start,
                    )
                    .group_by(UnifiedOrder.user_id)
                    .having(func.count(UnifiedOrder.id) >= min_orders)
                    .limit(limit)
                )
            ).all()
            candidates = [(int(uid), int(count)) for uid, count in rows]

        granted = 0
        notifier = NotificationService(bot)
        for user_id, orders in candidates:
            for threshold, percent in tiers:
                if orders < threshold:
                    break
                event_key = f"loyalty_chain:{user_id}:{month}:{threshold}"
                async with async_session_maker() as session:
                    user = await session.get(User, user_id)
                    if user is None or user.is_banned:
                        continue
                    awarded = await LoyaltyService.award_points(
                        session,
                        user_id=user_id,
                        points=points,
                        event_key=event_key,
                        event_type="loyalty_chain",
                        description=f"سلسلة ولاء: {threshold} طلبات في {month}",
                    )
                    if not awarded:
                        continue  # مُنح سابقاً لهذه العتبة هذا الشهر
                    code = await cls._unique_code(session, user_id, percent)
                    try:
                        coupon = await CouponService.create_coupon(
                            session,
                            code=code,
                            discount_type="percent",
                            discount_value=Decimal(percent),
                            max_uses=1,
                            created_by=user_id,
                            min_order_usd=Decimal("0"),
                            expires_at=now + timedelta(days=days),
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("تعذّر إنشاء كوبون سلسلة الولاء للمستخدم %s", user_id)
                        continue
                    code = coupon.code
                try:
                    await notifier.notify_user(
                        user.telegram_id,
                        cls.compose_message(
                            user.language_code or "ar", code, percent, orders, days
                        ),
                        reply_markup=cls.markup(user.language_code or "ar"),
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("تعذّر إشعار سلسلة الولاء للمستخدم %s", user_id)
                granted += 1
        return granted
