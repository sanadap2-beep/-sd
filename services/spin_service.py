"""
خدمة عجلة الحظ اليومية.

- لفة واحدة مجانية يومياً لكل مستخدم (قيد فريد على day_key).
- الجوائز يُديرها الأدمن من لوحة الأدمن (اسم، نوع، قيمة، وزن، تفعيل).
- النتيجة تُصرف فوراً عبر BalanceService / LoyaltyService ولا تتكرر.
"""

from __future__ import annotations

import logging
import random
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from database.models import SpinHistory, SpinPrize, TransactionType
from services.balance_service import BalanceService
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class SpinError(Exception):
    """خطأ واضح في عجلة الحظ."""


class SpinService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("daily_spin")

    @staticmethod
    async def get_active_prizes(session) -> list[SpinPrize]:
        result = await session.execute(
            select(SpinPrize)
            .where(SpinPrize.is_active.is_(True))
            .order_by(SpinPrize.sort_order.asc(), SpinPrize.id.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def all_prizes(session) -> list[SpinPrize]:
        result = await session.execute(select(SpinPrize).order_by(SpinPrize.sort_order.asc(), SpinPrize.id.asc()))
        return list(result.scalars().all())

    @staticmethod
    async def add_prize(session, name_ar: str, prize_type: str, value: Decimal, weight: int) -> SpinPrize:
        prize = SpinPrize(
            name_ar=name_ar[:128],
            prize_type=prize_type,
            value=value,
            weight=max(1, int(weight)),
            is_active=True,
        )
        session.add(prize)
        await session.commit()
        await session.refresh(prize)
        return prize

    @staticmethod
    async def update_prize(
        session, prize_id: int, name_ar: str, value: Decimal, weight: int
    ) -> SpinPrize | None:
        prize = await session.get(SpinPrize, prize_id)
        if prize is None:
            return None
        prize.name_ar = name_ar[:128]
        prize.value = value
        prize.weight = max(1, int(weight))
        await session.commit()
        await session.refresh(prize)
        return prize

    @staticmethod
    async def toggle_prize(session, prize_id: int) -> SpinPrize | None:
        prize = await session.get(SpinPrize, prize_id)
        if prize is None:
            return None
        prize.is_active = not prize.is_active
        await session.commit()
        await session.refresh(prize)
        return prize

    @staticmethod
    async def delete_prize(session, prize_id: int) -> bool:
        prize = await session.get(SpinPrize, prize_id)
        if prize is None:
            return False
        await session.delete(prize)
        await session.commit()
        return True

    @staticmethod
    async def can_spin_today(session, user_id: int) -> tuple[int | None, bool, SpinHistory | None]:
        """متى يمكن اللف: (روكتنا اليوم، هل لف سابقاً، سجل آخر لفة)."""
        today = date.today().isoformat()
        result = await session.execute(
            select(SpinHistory).where(SpinHistory.user_id == user_id).order_by(SpinHistory.id.desc())
        )
        last = result.scalars().first()
        if last is None:
            return None, False, None
        return last.day_key, last.day_key == today, last

    @staticmethod
    def _pick_prize(prizes: list[SpinPrize]) -> SpinPrize:
        """اختيار عشوائي مرجح حسب weight."""
        if not prizes:
            raise SpinError("لا توجد جوائز مفعلة حالياً.")
        total = sum(p.weight for p in prizes)
        roll = random.randint(1, total)
        running = 0
        for prize in prizes:
            running += prize.weight
            if roll <= running:
                return prize
        return prizes[-1]

    @staticmethod
    async def spin(session, user_id: int) -> dict:
        """قم باللف. يُرجع نتيجة مجانية (لا يلف إذا لف مسبقاً اليوم)."""
        if not await SpinService.enabled():
            raise SpinError("الميزة معطلة حالياً.")

        prizes = await SpinService.get_active_prizes(session)
        if not prizes:
            raise SpinError("لا توجد جوائز مفعلة حالياً.")

        today = date.today().isoformat()
        result = await session.execute(
            select(SpinHistory).where(SpinHistory.user_id == user_id, SpinHistory.day_key == today)
        )
        existing = result.scalars().first()
        if existing is not None:
            raise SpinError("لقد حصلت على لفتك المجانية اليوم. عُد غداً!")

        prize = SpinService._pick_prize(prizes)

        if prize.prize_type == "balance":
            amount = prize.value
            if amount > 0:
                await BalanceService.add_balance(
                    session,
                    user_id,
                    amount,
                    TransactionType.COUPON_BONUS,
                    description=f"عجلة الحظ: {prize.name_ar}",
                    related_table="spin_history",
                    related_id=0,
                    payment_reference=f"spin:{user_id}:{today}",
                )
            outcome = f"{prize.name_ar} ({amount}$)"
        elif prize.prize_type == "points":
            points = int(prize.value)
            from services.loyalty_service import LoyaltyService
            from services.settings_service import SettingsService

            if points > 0:
                converted = await SettingsService.get_int("spin_points_per_usd", 10)
                await LoyaltyService.award_points(
                    session,
                    user_id,
                    points,
                    event_key=f"spin:{user_id}:{today}",
                    event_type="spin",
                    description=f"عجلة الحظ: {prize.name_ar}",
                )
                outcome = f"{prize.name_ar} ({points} نقطة)"
            else:
                outcome = prize.name_ar
        else:
            outcome = prize.name_ar

        session.add(
            SpinHistory(
                user_id=user_id,
                prize_id=prize.id,
                prize_type=prize.prize_type,
                prize_label=prize.name_ar,
                value=prize.value,
                day_key=today,
            )
        )
        await session.commit()

        logger.info("لفة عجلة للمستخدم %s: %s", user_id, outcome)
        return {
            "prize": prize,
            "label": prize.name_ar,
            "outcome": outcome,
            "prize_type": prize.prize_type,
            "value": prize.value,
        }

    @staticmethod
    async def seed_default_prizes(session) -> None:
        """جوائز افتراضية عند أول تشغيل (تبقى تحت سيطرة الأدمن)."""
        result = await session.execute(select(SpinPrize))
        if result.scalars().first() is not None:
            return
        defaults = [
            ("1$ هدية", "balance", Decimal("1"), 8),
            ("0.5$ هدية", "balance", Decimal("0.5"), 20),
            ("25 نقطة ولاء", "points", Decimal("25"), 15),
            ("10 نقاط ولاء", "points", Decimal("10"), 25),
            ("حظ أوفر! محاولة غداً", "nothing", Decimal("0"), 32),
        ]
        for i, (name, ptype, value, weight) in enumerate(defaults):
            session.add(
                SpinPrize(
                    name_ar=name, prize_type=ptype, value=value, weight=weight, sort_order=i, is_active=True
                )
            )
        await session.commit()