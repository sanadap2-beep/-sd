"""
برنامج الوكلاء: كود من الأدمن → وكيل بخصم على كل المنتجات والخدمات.

- الكود يُصدر من «💼 إدارة الوكلاء» ويُستعمل مرة واحدة.
- الوكيل ينال نسبة الخصم الحالية (افتراضياً 10%) على كل المشتريات:
  المنتجات، الأرقام، العروض الخاصة، الباقات.
- الفحص الأسبوعي: إذا بقي إيداع الوكيل (شام كاش/نجوم/USDT...) خلال
  آخر 7 أيام أقل من الحد (افتراضياً 20$) تُسحب وكالته وتُشعر الإدارة.
- نسبة الخصم قابلة للرفع/الخفض من اللوحة (بين min/max).
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from database.models import (
    AgentCode,
    AgentProfile,
    Transaction,
    TransactionType,
    UnifiedOrder,
    NumberOrder,
)
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)

FEATURE_KEY = "agent_program"

# أنواع الإيداعات التي تُحتسب ضمن «إيداع الوكيل الأسبوعي»
DEPOSIT_TYPES = (TransactionType.DEPOSIT, TransactionType.STARS_DEPOSIT)


class AgentError(Exception):
    """خطأ قابل للعرض للمستخدم (كود خاطئ، وكيل سابقاً...)."""


def _week_key(dt: datetime) -> str:
    """مفتاح الأسبوع (YYYY-WW) لمنع فحص متكرر لأسبوع واحد."""
    iso = dt.isocalendar()
    return f"{iso.year}-{iso.week:02d}"


class AgentService:
    # ─────────── الحالة ───────────

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY)

    @staticmethod
    async def profile(session, user_id: int) -> AgentProfile | None:
        result = await session.execute(
            select(AgentProfile)
            .options(selectinload(AgentProfile.user))
            .where(AgentProfile.user_id == user_id)
        )
        return result.scalars().unique().first()

    @staticmethod
    async def is_active_agent(session, user_id: int) -> bool:
        profile = await AgentService.profile(session, user_id)
        return profile is not None and profile.status == "active"

    # ─────────── الخصم ───────────

    @staticmethod
    async def active_percent(session, user_id: int) -> Decimal:
        """نسبة خصم الوكيل الحالية (0 إن لم يكن وكلاً)."""
        profile = await AgentService.profile(session, user_id)
        if profile is None or profile.status != "active":
            return Decimal("0")
        return Decimal(str(profile.percent or 0))

    @classmethod
    async def apply_discount(cls, session, user_id: int, price: Decimal) -> Decimal:
        """السعر بعد خصم الوكيل (لو كان وكلاً)، مقرب لأقرب 0.0001$."""
        percent = await cls.active_percent(session, user_id)
        if percent <= 0 or price <= 0:
            return price
        discounted = price * (Decimal("100") - percent) / Decimal("100")
        return discounted.quantize(Decimal("0.0001"))

    # ─────────── الإصدار والاستعمال ───────────

    @staticmethod
    async def create_code(session, admin_id: int, percent: Decimal | None = None) -> AgentCode:
        if percent is None:
            percent = Decimal(
                str(await FeatureService.config(FEATURE_KEY, "default_percent", 10))
            )
        minimum, maximum = await AgentService._percent_bounds()
        percent = max(minimum, min(maximum, Decimal(str(percent))))
        code = AgentCode(
            code=AgentService._generate_code(),
            created_by=admin_id,
            percent=percent,
            status="unused",
        )
        session.add(code)
        await session.commit()
        await session.refresh(code)
        return code

    @staticmethod
    def _generate_code() -> str:
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        return "AGENT-" + "-".join(
            "".join(secrets.choice(alphabet) for _ in range(4))
            for _ in range(2)
        )

    @classmethod
    async def redeem_code(cls, session, user_id: int, code_text: str, admin_id: int | None = None) -> AgentProfile:
        code_text = (code_text or "").strip().upper()
        if len(code_text) < 6:
            raise AgentError("⚠️ الكود غير صالح.")

        result = await session.execute(select(AgentCode).where(AgentCode.code == code_text))
        code = result.scalars().first()
        if code is None:
            raise AgentError("❌ هذا الكود غير موجود.")
        if code.status == "used":
            raise AgentError("❌ هذا الكود استُعمل مسبقاً.")
        if code.status == "voided":
            raise AgentError("❌ هذا الكود مُلغي من الإدارة.")

        existing = await cls.profile(session, user_id)
        if existing is not None and existing.status == "active":
            raise AgentError("⚠️ أنت وكيل بالفعل ولا تحتاج كوداً جديداً.")

        # وكيل سابق سُبقت وكالته → يعيد تفعيله بنفس نسبة الكود.
        if existing is not None:
            existing.status = "active"
            existing.percent = code.percent
            existing.code_id = code.id
            existing.granted_by = code.created_by
            existing.activated_at = datetime.utcnow()
            existing.revoked_at = None
            existing.revoke_reason = None
            profile = existing
        else:
            profile = AgentProfile(
                user_id=user_id,
                code_id=code.id,
                granted_by=code.created_by,
                percent=code.percent,
                status="active",
            )
            session.add(profile)

        code.status = "used"
        code.user_id = user_id
        code.redeemed_at = datetime.utcnow()
        await session.commit()
        await session.refresh(profile)
        return profile

    # ─────────── إدارة الوكلاء ───────────

    @staticmethod
    async def _percent_bounds() -> tuple[Decimal, Decimal]:
        try:
            minimum = Decimal(str(await FeatureService.config(FEATURE_KEY, "min_percent", 1)))
            maximum = Decimal(str(await FeatureService.config(FEATURE_KEY, "max_percent", 50)))
        except (InvalidOperation, ValueError, TypeError):
            minimum, maximum = Decimal("1"), Decimal("50")
        return minimum, maximum

    @classmethod
    async def adjust_percent(cls, session, user_id: int, delta: Decimal) -> AgentProfile:
        profile = await cls.profile(session, user_id)
        if profile is None:
            raise AgentError("❌ المستخدم ليس وكلاً.")
        if profile.status != "active":
            raise AgentError("⚠️ وكالته سُبقت — لا يمكن تعديل النسبة.")
        minimum, maximum = await cls._percent_bounds()
        new_value = (Decimal(str(profile.percent)) + delta).quantize(Decimal("0.01"))
        new_value = max(minimum, min(maximum, new_value))
        profile.percent = new_value
        await session.commit()
        await session.refresh(profile)
        return profile

    @staticmethod
    async def revoke(session, user_id: int, reason: str, admin_id: int | None = None) -> AgentProfile:
        profile = await AgentService.profile(session, user_id)
        if profile is None:
            raise AgentError("❌ المستخدم ليس وكلاً.")
        if profile.status != "active":
            raise AgentError("⚠️ وكالته سُبقت مسبقاً.")
        profile.status = "revoked"
        profile.revoked_at = datetime.utcnow()
        profile.revoke_reason = reason[:255]
        await session.commit()
        await session.refresh(profile)
        return profile

    @staticmethod
    async def active_agents(session) -> list[AgentProfile]:
        result = await session.execute(
            select(AgentProfile)
            .options(selectinload(AgentProfile.user))
            .where(AgentProfile.status == "active")
            .order_by(AgentProfile.activated_at.desc())
        )
        return list(result.scalars().unique().all())

    @staticmethod
    async def revoked_agents(session) -> list[AgentProfile]:
        result = await session.execute(
            select(AgentProfile)
            .options(selectinload(AgentProfile.user))
            .where(AgentProfile.status == "revoked")
            .order_by(AgentProfile.revoked_at.desc())
            .limit(50)
        )
        return list(result.scalars().unique().all())

    @staticmethod
    async def unused_codes(session) -> list[AgentCode]:
        result = await session.execute(
            select(AgentCode)
            .where(AgentCode.status == "unused")
            .order_by(AgentCode.created_at.desc())
            .limit(20)
        )
        return list(result.scalars().all())

    @staticmethod
    async def void_code(session, code_id: int) -> bool:
        code = await session.get(AgentCode, code_id)
        if code is None or code.status != "unused":
            return False
        code.status = "voided"
        await session.commit()
        return True

    # ─────────── الإحصاءات ───────────

    @staticmethod
    async def weekly_stats(session, user_id: int) -> dict:
        """إيداعات/مشتريات/طلبات آخر 7 أيام + الإجماليات الكلية."""
        since = datetime.utcnow() - timedelta(days=7)

        deposits = (
            await session.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                    Transaction.user_id == user_id,
                    Transaction.type.in_(DEPOSIT_TYPES),
                    Transaction.created_at >= since,
                )
            )
        ).scalar_one()

        purchases = (
            await session.execute(
                select(func.coalesce(func.sum(UnifiedOrder.price_usd), 0)).where(
                    UnifiedOrder.user_id == user_id,
                    UnifiedOrder.created_at >= since,
                    UnifiedOrder.status.notin_(
                        ["refunded", "failed", "cancelled"]
                    ),
                )
            )
        ).scalar_one()

        unified_orders = (
            await session.execute(
                select(func.count(UnifiedOrder.id)).where(
                    UnifiedOrder.user_id == user_id,
                    UnifiedOrder.created_at >= since,
                )
            )
        ).scalar_one()

        number_orders = (
            await session.execute(
                select(func.count(NumberOrder.id)).where(
                    NumberOrder.user_id == user_id,
                    NumberOrder.purchased_at >= since,
                )
            )
        ).scalar_one()

        total_spent = (
            await session.execute(
                select(func.coalesce(func.sum(UnifiedOrder.price_usd), 0)).where(
                    UnifiedOrder.user_id == user_id,
                    UnifiedOrder.status.notin_(["refunded", "failed", "cancelled"]),
                )
            )
        ).scalar_one()

        return {
            "week_deposits_usd": Decimal(str(deposits)),
            "week_purchases_usd": Decimal(str(purchases)),
            "week_orders": int(unified_orders) + int(number_orders),
            "total_spent_usd": Decimal(str(total_spent)),
        }

    # ─────────── الفحص الأسبوعي ───────────

    @classmethod
    async def check_weekly_deposits(cls, session, bot=None) -> list[AgentProfile]:
        """يسحب وكالات الوكلاء الذين أقل إيداعهم الأسبوعي من الحد.

        يُشعر الإدارة عن كل سحب. يعيد قائمة الوكلاء المسحوبين.
        """
        from services.notification_service import NotificationService

        if not await cls.enabled():
            return []
        try:
            threshold = Decimal(
                str(await FeatureService.config(FEATURE_KEY, "min_weekly_deposit_usd", 20))
            )
        except (InvalidOperation, ValueError, TypeError):
            threshold = Decimal("20")

        week = _week_key(datetime.utcnow())
        agents = await cls.active_agents(session)
        revoked: list[AgentProfile] = []
        touched = False

        for profile in agents:
            if profile.last_checked_week == week:
                continue
            touched = True
            profile.last_checked_week = week
            stats = await cls.weekly_stats(session, profile.user_id)
            if stats["week_deposits_usd"] >= threshold:
                continue

            profile.status = "revoked"
            profile.revoked_at = datetime.utcnow()
            profile.revoke_reason = (
                f"إيداع أسبوعي {stats['week_deposits_usd']}$ أقل من الحد {threshold}$"
            )
            revoked.append(profile)
            logger.info(
                " سُحب وكيل #%s (إيداع أسبوعي %s$ < %s$)",
                profile.user_id,
                stats["week_deposits_usd"],
                threshold,
            )
            if bot is not None:
                user = profile.user
                name = (user.full_name or user.telegram_id) if user else profile.user_id
                notifier = NotificationService(bot)
                await notifier.notify_admin(
                    "🚨 <b>سُحبت وكالة تلقائياً</b>\n\n"
                    f"👤 المستخدم: <b>{name}</b> (<code>{user.telegram_id if user else '—'}</code>)\n"
                    f"💵 إيداع آخر 7 أيام: <b>{stats['week_deposits_usd']}$</b>\n"
                    f"📏 الحد المطلوب: <b>{threshold}$</b>\n\n"
                    "لإعادة التفعيل: أنشئ كوداً جديداً وأرسله له من «إدارة الوكلاء»."
                )
        if touched:
            await session.commit()
        return revoked
