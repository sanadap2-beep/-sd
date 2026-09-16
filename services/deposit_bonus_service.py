"""
نظام مكافآت الشحن: نسبة إضافية على كل إيداع يمر عبره.

يقرأ قواعد المكافأة من جدول deposit_bonus_rules (يُدار من لوحة الأدمن)،
ويصرف المكافأة تلقائياً فور اعتماد أي إيداع، مع سجل منع التكرار.

كل مبلغ يُضاف عبر BalanceService بمعاملة نوع COUPON_BONUS حتى يظهر
في دفتر الأستاذ ولا يُضاف مرتين.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from sqlalchemy import select

from database.models import DepositBonusGrant, DepositBonusRule, TransactionType
from services.balance_service import BalanceService
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class DepositBonusService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("deposit_bonuses")

    @staticmethod
    async def get_rules(session) -> list[DepositBonusRule]:
        result = await session.execute(
            select(DepositBonusRule)
            .where(DepositBonusRule.is_active.is_(True))
            .order_by(DepositBonusRule.min_deposit_usd)
        )
        return list(result.scalars().all())

    @staticmethod
    async def all_rules(session) -> list[DepositBonusRule]:
        result = await session.execute(select(DepositBonusRule).order_by(DepositBonusRule.min_deposit_usd))
        return list(result.scalars().all())

    @staticmethod
    async def add_rule(session, min_deposit: Decimal, percent: Decimal, max_bonus: Decimal) -> DepositBonusRule:
        rule = DepositBonusRule(
            min_deposit_usd=min_deposit,
            bonus_percent=percent,
            max_bonus_usd=max_bonus,
            is_active=True,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
        return rule

    @staticmethod
    async def update_rule(
        session,
        rule_id: int,
        min_deposit: Decimal,
        percent: Decimal,
        max_bonus: Decimal,
    ) -> DepositBonusRule | None:
        rule = await session.get(DepositBonusRule, rule_id)
        if rule is None:
            return None
        rule.min_deposit_usd = min_deposit
        rule.bonus_percent = percent
        rule.max_bonus_usd = max_bonus
        await session.commit()
        await session.refresh(rule)
        return rule

    @staticmethod
    async def toggle_rule(session, rule_id: int) -> DepositBonusRule | None:
        rule = await session.get(DepositBonusRule, rule_id)
        if rule is None:
            return None
        rule.is_active = not rule.is_active
        await session.commit()
        await session.refresh(rule)
        return rule

    @staticmethod
    async def delete_rule(session, rule_id: int) -> bool:
        rule = await session.get(DepositBonusRule, rule_id)
        if rule is None:
            return False
        await session.delete(rule)
        await session.commit()
        return True

    @staticmethod
    async def _granted_before(session, deposit_source: str, deposit_id: int) -> bool:
        if deposit_id is None:
            return False
        result = await session.execute(
            select(DepositBonusGrant).where(
                DepositBonusGrant.deposit_source == deposit_source,
                DepositBonusGrant.deposit_id == deposit_id,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def apply_for_deposit(
        session,
        user_id: int,
        deposit_amount_usd: Decimal,
        deposit_source: str = "deposit",
        deposit_id: int | None = None,
    ) -> Decimal:
        """
        يصرف مكافأة الشحن المناسبة لإيداع معين — إذا كانت الميزة مفعلة
        ولم تمنح المكافأة لهذا الإيداع سابقاً. يُرجع مبلغ المكافأة المضافة.
        """
        if not await FeatureService.enabled("deposit_bonuses"):
            return Decimal("0")
        if deposit_amount_usd <= 0:
            return Decimal("0")
        if await DepositBonusService._granted_before(session, deposit_source, deposit_id):
            return Decimal("0")

        rules = await DepositBonusService.get_rules(session)
        if not rules:
            return Decimal("0")

        best: DepositBonusRule | None = None
        for rule in rules:
            if deposit_amount_usd >= rule.min_deposit_usd:
                if best is None or rule.min_deposit_usd > best.min_deposit_usd:
                    best = rule

        if best is None:
            return Decimal("0")

        bonus = (deposit_amount_usd * best.bonus_percent / Decimal("100")).quantize(Decimal("0.0001"))
        if best.max_bonus_usd and best.max_bonus_usd > 0:
            bonus = min(bonus, best.max_bonus_usd)
        if bonus <= 0:
            return Decimal("0")

        try:
            await BalanceService.add_balance(
                session,
                user_id,
                bonus,
                TransactionType.COUPON_BONUS,
                description=f"مكافأة شحن {deposit_amount_usd}$ بنسبة {best.bonus_percent}%",
                related_table="deposit_bonus_rules",
                related_id=best.id,
            )
        except Exception:
            logger.exception("فشل صرف مكافأة الشحن للمستخدم %s", user_id)
            return Decimal("0")

        session.add(
            DepositBonusGrant(
                user_id=user_id,
                deposit_id=deposit_id,
                deposit_source=deposit_source,
                deposit_amount_usd=deposit_amount_usd,
                bonus_usd=bonus,
                rule_id=best.id,
            )
        )
        await session.commit()
        logger.info("مكافأة شحن %s$ للمستخدم %s (إيداع %s)", bonus, user_id, deposit_amount_usd)

        if await FeatureService.config("deposit_bonuses", "notify_on_grant", True):
            from services.notification_service import NotificationService

            # nota: الإشعار يُرسل من المتصل عبر bot — لكن نحتفظ بسجل هنا
            pass
        return bonus

    @staticmethod
    async def grants_report(session, days: int = 30) -> dict:
        """تقرير الأدمن: كم صُرف من المكافآت في المدة."""
        from datetime import datetime, timedelta

        since = datetime.utcnow() - timedelta(days=days)
        result = await session.execute(
            select(DepositBonusGrant).where(DepositBonusGrant.created_at >= since)
        )
        grants = list(result.scalars().all())
        total = sum((g.bonus_usd for g in grants), Decimal("0"))
        users = len({g.user_id for g in grants})
        return {"count": len(grants), "total_usd": total, "users": users}

    @staticmethod
    async def seed_default_rule(session) -> None:
        """يقوم بزرع قاعدة افتراضية إذا لم توجد أي قاعدة (آمن عند أول تفعيل)."""
        result = await session.execute(select(DepositBonusRule))
        if result.scalars().first() is not None:
            return
        session.add(
            DepositBonusRule(
                min_deposit_usd=Decimal("5"),
                bonus_percent=Decimal("2"),
                max_bonus_usd=Decimal("2"),
                is_active=True,
            )
        )
        await session.commit()