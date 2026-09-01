"""
التجربة المجانية الأولى + الإحالة متعددة المستويات.

1) التجربة المجانية (Zero-Risk Trial)
   أول رقم للمستخدم الجديد مجاناً. صفر احتكاك في أول تجربة = نمو أسرع
   من أي حملة إعلانية. الخطر هو إساءة الاستخدام بحسابات متعددة، لذا
   تُربط بعدة حواجز: حساب واحد لكل telegram_id، عمر حساب أدنى، سقف
   قيمة، وربطها بـ abuse_guard الموجود.

2) الإحالة متعددة المستويات
   الإحالة اليوم مستوى واحد بنسبة ثابتة 5%. الشجرة هنا تكافئ عدة
   مستويات بنسب يتناقص عمقها، فيصير لدى المستخدم حافز يبني شبكة لا
   يدعو فرداً واحداً.

كلاهما قابل للإيقاف والضبط من مركز الإضافات.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    TransactionType,
    User,
)
from services.abuse_guard_service import AbuseGuardService
from services.balance_service import BalanceService
from services.feature_service import FeatureService
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


class TrialError(Exception):
    pass


class FreeTrialService:
    USED_KEY = "free_trial_used_users"

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("free_trial")

    @staticmethod
    async def max_value() -> Decimal:
        return Decimal(
            str(await FeatureService.config_decimal("free_trial", "max_value_usd", 0.5))
        )

    @staticmethod
    async def is_eligible(session, user_id: int) -> tuple[bool, str]:
        """هل يستحق هذا المستخدم التجربة المجانية؟"""
        if not await FreeTrialService.enabled():
            return False, "التجربة المجانية موقوفة حالياً."

        user = await session.get(User, user_id)
        if user is None:
            return False, "المستخدم غير موجود."

        # سبق أن استخدمها؟
        used = await SettingsService.get(FreeTrialService.USED_KEY, "")
        if f"|{user.telegram_id}|" in f"|{used}|":
            return False, "استخدمت التجربة المجانية من قبل."

        # عمر الحساب
        min_minutes = await FeatureService.config_int("free_trial", "require_account_age_minutes", 0)
        if min_minutes > 0 and user.joined_at is not None:
            age_minutes = (datetime.utcnow() - user.joined_at).total_seconds() / 60
            if age_minutes < min_minutes:
                return False, f"تحتاج حساباً بعمر {min_minutes} دقيقة على الأقل."

        # لديه طلبات سابقة؟ التجربة للأجديد فقط
        if (user.total_orders or 0) > 0:
            return False, "التجربة المجانية للمستخدمين الجدد فقط."

        # سلوك مريب؟
        if await AbuseGuardService.is_blocked(session, user_id):
            return False, "تم تقييد حسابك. تواصل مع الدعم."

        return True, ""

    @staticmethod
    async def grant(session, user_id: int) -> Decimal:
        """يمنح رصيد التجربة ويسجل الاستخدام."""
        ok, reason = await FreeTrialService.is_eligible(session, user_id)
        if not ok:
            raise TrialError(reason)

        user = await session.get(User, user_id)
        amount = await FreeTrialService.max_value()
        if amount <= 0:
            raise TrialError("قيمة التجربة غير مضبوطة.")

        await BalanceService.add_balance(
            session,
            user_id,
            amount,
            TransactionType.ADMIN_ADD,
            description="رصيد التجربة المجانية الأولى",
            payment_reference=f"free_trial:{user.telegram_id}",
        )

        used = await SettingsService.get(FreeTrialService.USED_KEY, "")
        await SettingsService.set(
            session, FreeTrialService.USED_KEY, f"{used}|{user.telegram_id}|"
        )
        await FeatureService.track("free_trial", "granted", user_id=user_id)
        return amount


class AffiliateService:
    """
    إحالة متعددة المستويات.

    المستويات ونسبها تُضبط من اللوحة، فيمكن للأدمن أن يجعلها مستوى
    واحداً (السلوك القديم) أو شجرة أعمق.
    """

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("affiliate_tiers")

    @staticmethod
    async def levels() -> int:
        return max(1, await FeatureService.config_int("affiliate_tiers", "levels", 3))

    @staticmethod
    async def percents() -> list[Decimal]:
        raw = await FeatureService.config_json(
            "affiliate_tiers", "level_percents_json", [5, 2, 1]
        )
        out: list[Decimal] = []
        for item in raw or []:
            try:
                value = Decimal(str(item))
            except (TypeError, ValueError, ArithmeticError):
                continue
            if value > 0:
                out.append(value)
        return out or [Decimal("5")]

    @staticmethod
    async def upline(session, user_id: int) -> list[tuple[int, int]]:
        """
        سلسلة المحيلين فوق المستخدم: [(user_id, level), ...]
        المستوى 1 = من دعاه مباشرة.
        """
        chain: list[tuple[int, int]] = []
        current = await session.get(User, user_id)
        depth = 0
        seen: set[int] = {user_id}
        while current is not None and current.referrer_id is not None:
            if current.referrer_id in seen:
                break  # حلقة مغلقة في بيانات الإحالة
            seen.add(current.referrer_id)
            depth += 1
            if depth > await AffiliateService.levels():
                break
            chain.append((current.referrer_id, depth))
            current = await session.get(User, current.referrer_id)
        return chain

    @staticmethod
    async def reward_purchase(
        session, buyer_id: int, amount_usd: Decimal, order_table: str, order_id: int
    ) -> list[dict]:
        """
        يكافئ سلسلة المحيلين عند شراء أحدهم.
        يرجع من دُفع له وكم.
        """
        if not await AffiliateService.enabled() or amount_usd <= 0:
            return []

        percents = await AffiliateService.percents()
        paid: list[dict] = []
        for referrer_id, level in await AffiliateService.upline(session, buyer_id):
            if level > len(percents):
                break
            percent = percents[level - 1]
            bonus = (amount_usd * percent / Decimal("100")).quantize(Decimal("0.0001"))
            if bonus <= 0:
                continue
            awarded = await BalanceService.add_balance(
                session,
                referrer_id,
                bonus,
                TransactionType.REFERRAL_BONUS,
                description=f"عمولة إحالة مستوى {level} من طلب #{order_id}",
                related_table=order_table,
                related_id=order_id,
                payment_reference=f"affiliate:L{level}:{order_table}:{order_id}:{referrer_id}",
            )
            paid.append({"referrer_id": referrer_id, "level": level, "bonus_usd": bonus})

        if paid:
            await FeatureService.track(
                "affiliate_tiers", "rewarded", user_id=buyer_id, value=str(len(paid))
            )
        return paid

    @staticmethod
    async def leaderboard(session, limit: int = 10) -> list[dict]:
        """أفضل المحيلين — يُعرض في لوحة الصدارة العامة."""
        result = await session.execute(
            select(User.referrer_id, func.count(User.id), func.coalesce(func.sum(User.total_spent_usd), 0))
            .where(User.referrer_id.is_not(None))
            .group_by(User.referrer_id)
            .order_by(func.count(User.id).desc())
            .limit(limit)
        )
        rows = []
        for referrer_id, referrals, volume in result.all():
            referrer = await session.get(User, referrer_id)
            rows.append(
                {
                    "user_id": referrer_id,
                    "username": referrer.username if referrer else None,
                    "referrals": int(referrals or 0),
                    "volume_usd": Decimal(str(volume or 0)),
                }
            )
        return rows
