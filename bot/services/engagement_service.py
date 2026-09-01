"""
منشئ الحملات + مُحسِّن المزيج + البطولات + لوحة الصدارة + البصمة السلوكية.

خمس ميزات ترفع متوسط السلة والاحتفاظ:

1) منشئ الحملات: بدل شراء خدمة خدمة، يخطط البوت حملة كاملة بميزانية
   وجدول زمني. ينتقل البيع من «خدمة» إلى «نتيجة».

2) مُحسِّن المزيج: يعطي المستخدم رابطاً وميزانية، فيبني البوت أرخص
   أو أفضل مزيج من كل خدمات كل المزودين. كل الأسعار مسحوبة أصلاً في
   provider_services لكنها كانت تُستخدم للعرض فقط.

3) البطولات: اشتراك بالرصيد وجوائز تُدفع رصيداً، فتُبنى مجتمعات حول
   البوت بدل المرور عليه.

4) لوحة الصدارة: تنافس اجتماعي معلن بدل نقاط باردة.

5) البصمة السلوكية: كشف الحسابات المؤتمتة وعصابات إعادة البيع من نمط
   التوقيت والسرعة، لا من عنوان IP وحده.

كلها قابلة للإيقاف والضبط من مركز الإضافات.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN

from sqlalchemy import desc, func, select

from database.models import (
    Product,
    ProductStatus,
    ProviderService,
    ProviderServiceStatus,
    TransactionType,
    UnifiedOrder,
    User,
)
from services.balance_service import BalanceService
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class CampaignError(Exception):
    pass


class CampaignService:
    """يخطط حملة من عدة خدمات ضمن ميزانية واحدة."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("campaign_builder")

    @staticmethod
    async def max_services() -> int:
        return max(1, await FeatureService.config_int(
            "campaign_builder", "max_services_per_campaign", 10
        ))

    @staticmethod
    async def plan(
        session,
        product_ids: list[int],
        budget_usd: Decimal,
        target: str,
        spread_days: int = 1,
    ) -> dict:
        """
        يبني خطة حملة: كيف تُوزَّع الميزانية على الخدمات وعلى الزمن.
        لا ينفذ شيئاً — يعرض الخطة ليوافق المستخدم أولاً.
        """
        if not await CampaignService.enabled():
            raise CampaignError("منشئ الحملات موقوف حالياً.")
        if budget_usd <= 0:
            raise CampaignError("الميزانية يجب أن تكون موجبة.")
        if not product_ids:
            raise CampaignError("اختر خدمة واحدة على الأقل.")
        if len(product_ids) > await CampaignService.max_services():
            raise CampaignError(
                f"الحد الأقصى {await CampaignService.max_services()} خدمات في الحملة."
            )
        if not target or not target.strip():
            raise CampaignError("أدخل الرابط أو المعرف المستهدف.")

        items = []
        remaining = budget_usd
        per_service = (budget_usd / Decimal(len(product_ids))).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )

        for product_id in product_ids:
            product = await session.get(Product, product_id)
            if product is None or product.status != ProductStatus.ACTIVE:
                continue
            unit = Decimal(str(product.price_usd or 0))
            if unit <= 0:
                continue
            quantity = int(per_service / unit)
            if quantity < int(product.min_quantity or 1):
                continue
            quantity = min(quantity, int(product.max_quantity or quantity))
            cost = (unit * Decimal(quantity)).quantize(Decimal("0.0001"))
            if cost > remaining:
                continue
            remaining -= cost
            items.append(
                {
                    "product_id": product.id,
                    "name": product.name_ar,
                    "unit_price_usd": unit,
                    "quantity": quantity,
                    "cost_usd": cost,
                }
            )

        if not items:
            raise CampaignError("الميزانية لا تكفي لأي من الخدمات المختارة.")

        # توزيع زمني
        days = max(1, spread_days)
        for index, item in enumerate(items):
            item["scheduled_day"] = index % days

        return {
            "target": target.strip(),
            "budget_usd": budget_usd,
            "planned_usd": (budget_usd - remaining).quantize(Decimal("0.0001")),
            "unallocated_usd": remaining.quantize(Decimal("0.0001")),
            "spread_days": days,
            "items": items,
        }


class SmartMixService:
    """يبني أرخص/أفضل مزيج لميزانية من كل خدمات كل المزودين."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("smart_mix")

    @staticmethod
    async def options(session, keyword: str, budget_usd: Decimal) -> list[dict]:
        """ثلاثة خيارات: الأرخص، المتوازن، الأعلى جودة."""
        if not await SmartMixService.enabled():
            return []
        if budget_usd <= 0:
            return []

        query = select(ProviderService).where(
            ProviderService.status == ProviderServiceStatus.ACTIVE,
            ProviderService.rate_usd > 0,
        )
        if keyword:
            query = query.where(ProviderService.name.ilike(f"%{keyword}%"))
        result = await session.execute(query.limit(300))
        services = list(result.scalars().all())
        if not services:
            return []

        cheapest = min(services, key=lambda s: s.rate_usd)
        dearest = max(services, key=lambda s: s.rate_usd)
        mid_rate = (cheapest.rate_usd + dearest.rate_usd) / 2
        balanced = min(services, key=lambda s: abs(s.rate_usd - mid_rate))

        def _option(service: ProviderService, label: str) -> dict:
            rate = service.rate_usd
            # الأسعار لكل 1000 عادةً، فنحسب ما تغطيه الميزانية
            units = int((budget_usd / rate) * 1000) if rate > 0 else 0
            return {
                "label": label,
                "service_id": service.id,
                "name": service.name,
                "rate_usd_per_1000": rate,
                "affordable_units": units,
                "max_quantity": service.max_quantity,
            }

        return [
            _option(cheapest, "💰 الأرخص"),
            _option(balanced, "⚖️ المتوازن"),
            _option(dearest, "💎 الأعلى جودة"),
        ]


class TournamentService:
    """بطولات باشتراك من الرصيد وجوائز تُدفع رصيداً."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("tournaments")

    @staticmethod
    async def entry_fee() -> Decimal:
        return Decimal(str(await FeatureService.config_decimal(
            "tournaments", "entry_fee_usd", 1.0
        )))

    @staticmethod
    async def prize_percent() -> int:
        return max(0, min(100, await FeatureService.config_int(
            "tournaments", "prize_pool_percent", 80
        )))

    @staticmethod
    async def join(session, user_id: int) -> dict:
        """يشترك المستخدم ويدفع رسم الدخول."""
        if not await TournamentService.enabled():
            raise CampaignError("البطولات موقوفة حالياً.")
        fee = await TournamentService.entry_fee()
        if fee <= 0:
            raise CampaignError("رسم الدخول غير مضبوط.")
        await BalanceService.deduct_balance(
            session, user_id, fee, TransactionType.PURCHASE,
            description="اشتراك في بطولة", is_purchase=True,
        )
        await FeatureService.track("tournaments", "joined", user_id=user_id)
        return {"entry_fee_usd": fee}

    @staticmethod
    async def settle(session, winners: list[int], pool_usd: Decimal) -> list[dict]:
        """يوزع الجوائز. نسبة المنصة تبقى من الرسم."""
        if not winners or pool_usd <= 0:
            return []
        percent = Decimal(await TournamentService.prize_percent())
        total_prize = (pool_usd * percent / Decimal("100")).quantize(Decimal("0.0001"))
        if total_prize <= 0:
            return []

        # توزيع تنازلي بأوزان 1/المركز، مطبّعة على مجموع الجائزة.
        # (تقسيم «نصف الباقي» كان يعطي تعادلاً عند فائزين: 4 و4 من 8.)
        weights = [Decimal(1) / Decimal(place) for place in range(1, len(winners) + 1)]
        weight_sum = sum(weights)

        paid = []
        allocated = Decimal("0")
        for index, user_id in enumerate(winners):
            if index == len(winners) - 1:
                # الأخير يأخذ الباقي حتى لا يضيع فرق التقريب
                share = (total_prize - allocated).quantize(Decimal("0.0001"))
            else:
                share = (total_prize * weights[index] / weight_sum).quantize(
                    Decimal("0.0001")
                )
            if share <= 0:
                break
            await BalanceService.add_balance(
                session, user_id, share, TransactionType.ADMIN_ADD,
                description=f"جائزة بطولة - المركز {index + 1}",
                payment_reference=f"tournament:{user_id}:{index}:{int(pool_usd * 100)}",
            )
            paid.append({"user_id": user_id, "place": index + 1, "prize_usd": share})
            allocated += share

        await FeatureService.track("tournaments", "settled", value=str(len(paid)))
        return paid


class LeaderboardService:
    """لوحة صدارة عامة — تنافس اجتماعي بدل نقاط باردة."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("public_leaderboard")

    @staticmethod
    async def top_spenders(session, limit: int = 10) -> list[dict]:
        result = await session.execute(
            select(User)
            .where(User.is_banned.is_(False), User.total_spent_usd > 0)
            .order_by(desc(User.total_spent_usd))
            .limit(limit)
        )
        return [
            {
                "rank": index + 1,
                "username": user.username or f"user{user.telegram_id}",
                "spent_usd": user.total_spent_usd,
                "orders": user.total_orders or 0,
            }
            for index, user in enumerate(result.scalars().all())
        ]

    @staticmethod
    async def top_points(session, limit: int = 10) -> list[dict]:
        result = await session.execute(
            select(User)
            .where(User.is_banned.is_(False), User.loyalty_points > 0)
            .order_by(desc(User.loyalty_points))
            .limit(limit)
        )
        return [
            {
                "rank": index + 1,
                "username": user.username or f"user{user.telegram_id}",
                "points": user.loyalty_points,
            }
            for index, user in enumerate(result.scalars().all())
        ]


class BehavioralFingerprintService:
    """
    كشف الحسابات المؤتمتة من نمط السلوك لا من العنوان وحده.

    الفكرة: البوت البشري يتصفح ثم يشتري بفواصل غير منتظمة. السكريبت
    ينفذ طلبات متتالية بفواصل شبه ثابتة وبلا تصفح. هذا الفرق قابل
    للقياس.
    """

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("behavioral_fingerprint")

    @staticmethod
    async def score(session, user_id: int) -> dict:
        """
        درجة ريبة من 0 إلى 100. الأعلى = أشبه بآلة.
        """
        if not await BehavioralFingerprintService.enabled():
            return {"score": 0, "flags": []}

        flags: list[str] = []
        score = 0

        user = await session.get(User, user_id)
        if user is None:
            return {"score": 0, "flags": ["unknown_user"]}

        # 1) كثافة الطلبات في آخر ساعة
        since = datetime.utcnow() - timedelta(hours=1)
        recent = (
            await session.execute(
                select(func.count(UnifiedOrder.id)).where(
                    UnifiedOrder.user_id == user_id,
                    UnifiedOrder.created_at >= since,
                )
            )
        ).scalar_one()
        if int(recent) >= 20:
            score += 35
            flags.append(f"orders_last_hour:{recent}")

        # 2) انتظام الفواصل بين الطلبات (الآلة منتظمة)
        result = await session.execute(
            select(UnifiedOrder.created_at)
            .where(UnifiedOrder.user_id == user_id)
            .order_by(desc(UnifiedOrder.created_at))
            .limit(10)
        )
        times = [row[0] for row in result.all() if row[0]]
        if len(times) >= 4:
            gaps = [
                (times[i] - times[i + 1]).total_seconds()
                for i in range(len(times) - 1)
            ]
            nonzero = [g for g in gaps if g > 0]
            if nonzero:
                avg = sum(nonzero) / len(nonzero)
                if avg > 0:
                    variance = sum(abs(g - avg) for g in nonzero) / len(nonzero)
                    # انتظام شديد = فارق صغير جداً مقارنة بالمتوسط
                    if variance / avg < 0.1:
                        score += 30
                        flags.append("regular_intervals")

        # 3) حساب جديد بلا تصفح يشتري مباشرة
        if user.joined_at is not None:
            age_minutes = (datetime.utcnow() - user.joined_at).total_seconds() / 60
            if age_minutes < 5 and (user.total_orders or 0) > 0:
                score += 25
                flags.append("instant_purchase_on_new_account")

        # 4) سلوك مسيء مسجل سابقاً
        from services.abuse_guard_service import AbuseGuardService

        abuse = await AbuseGuardService.score(session, user_id)
        if abuse > 0:
            score += min(30, abuse)
            flags.append(f"abuse_score:{abuse}")

        score = min(100, score)
        return {"score": score, "flags": flags}

    @staticmethod
    async def verdict(session, user_id: int) -> tuple[str, str]:
        """allow / review / block مع سبب مقروء."""
        data = await BehavioralFingerprintService.score(session, user_id)
        block_at = await FeatureService.config_int(
            "behavioral_fingerprint", "block_threshold", 80
        )
        review_at = await FeatureService.config_int(
            "behavioral_fingerprint", "review_threshold", 50
        )
        if data["score"] >= block_at:
            return "block", f"درجة ريبة {data['score']}: {', '.join(data['flags'])}"
        if data["score"] >= review_at:
            return "review", f"درجة ريبة {data['score']}: {', '.join(data['flags'])}"
        return "allow", ""
