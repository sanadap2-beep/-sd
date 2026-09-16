"""
خدمة الكاشباك.
تحسب وتضيف الكاشباك لرصيد المستخدم بعد كل شراء ناجح.
النسبة تُقرأ من إعدادات البوت وقابلة للتغيير من لوحة الأدمن.
"""

import logging
from decimal import Decimal

from sqlalchemy import select

from database.models import TransactionType, CashbackLog, User
from services.balance_service import BalanceService
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


class CashbackService:
    @staticmethod
    async def calculate_cashback(
        order_amount_usd: Decimal,
        multiplier: Decimal | None = None,
    ) -> Decimal:
        """
        يحسب مبلغ الكاشباك بالدولار.
        multiplier اختياري (مضاعف مستوى VIP) — إن كان None يُطبق 1.
        إذا كانت النسبة 0 يرجع 0 بدون استعلام إضافي.
        """
        percent = await SettingsService.get_decimal("cashback_percent", Decimal("0"))
        if percent <= 0:
            return Decimal("0")

        quant = (multiplier or Decimal("1"))
        cashback = order_amount_usd * (percent / Decimal("100")) * quant
        return cashback.quantize(Decimal("0.0001"))

    @staticmethod
    async def apply_cashback(
        session,
        user_id: int,
        order_id: int,
        order_type: str,
        order_amount_usd: Decimal,
    ) -> Decimal:
        """
        يحسب ويضيف الكاشباك لرصيد المستخدم (مع مضاعف مستوى VIP إن كانت
        الميزة مفعلة). يسجل العملية في cashback_logs.
        يرجع مبلغ الكاشباك المضاف (0 إذا لم يكن هناك كاشباك).
        """
        existing_result = await session.execute(
            select(CashbackLog).where(
                CashbackLog.user_id == user_id,
                CashbackLog.order_id == order_id,
                CashbackLog.order_type == order_type,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return existing.cashback_usd

        multiplier = await CashbackService._vip_multiplier(session, user_id)
        cashback_usd = await CashbackService.calculate_cashback(
            order_amount_usd, multiplier=multiplier
        )

        if cashback_usd <= 0:
            return Decimal("0")

        user = await BalanceService.add_balance(
            session=session,
            user_id=user_id,
            amount=cashback_usd,
            tx_type=TransactionType.CASHBACK,
            description=(f"كاشباك {order_type} #{order_id} ({order_amount_usd}$)"),
            related_table=order_type,
            related_id=order_id,
        )
        user.cashback_earned_usd = (user.cashback_earned_usd or Decimal("0")) + cashback_usd

        session.add(
            CashbackLog(
                user_id=user_id,
                order_id=order_id,
                order_type=order_type,
                order_amount_usd=order_amount_usd,
                cashback_usd=cashback_usd,
            )
        )
        await session.commit()

        logger.info(f"كاشباك {cashback_usd}$ للمستخدم {user_id} عن طلب {order_type} #{order_id}")

        return cashback_usd

    @staticmethod
    async def _vip_multiplier(session, user_id: int) -> Decimal:
        """مضاعف مستوى VIP إن فعلها الأدمن، وإلا 1 (بلا أثر)."""
        try:
            from services.feature_service import FeatureService
            from services.vip_service import VipService

            if not await FeatureService.enabled("vip_tiers"):
                return Decimal("1")
            user = await session.get(User, user_id)
            total = getattr(user, "total_spent_usd", None)
            if total is None:
                return Decimal("1")
            return Decimal(str(await VipService.cashback_multiplier(Decimal(str(total)))))
        except Exception:
            return Decimal("1")

    @staticmethod
    async def get_user_total_cashback(session, user_id: int) -> Decimal:
        """يجلب إجمالي الكاشباك الذي حصل عليه المستخدم."""
        from sqlalchemy import select, func

        result = await session.execute(
            select(func.coalesce(func.sum(CashbackLog.cashback_usd), 0)).where(
                CashbackLog.user_id == user_id
            )
        )
        return Decimal(str(result.scalar_one()))
