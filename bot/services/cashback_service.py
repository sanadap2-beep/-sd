"""
خدمة الكاشباك.
تحسب وتضيف الكاشباك لرصيد المستخدم بعد كل شراء ناجح.
النسبة تُقرأ من إعدادات البوت وقابلة للتغيير من لوحة الأدمن.
"""

import logging
from decimal import Decimal

from sqlalchemy import select

from database.models import TransactionType, CashbackLog
from services.balance_service import BalanceService
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


class CashbackService:
    @staticmethod
    async def calculate_cashback(
        order_amount_usd: Decimal,
    ) -> Decimal:
        """
        يحسب مبلغ الكاشباك بالدولار.
        إذا كانت النسبة 0 يرجع 0 بدون استعلام إضافي.
        """
        percent = await SettingsService.get_decimal("cashback_percent", Decimal("0"))
        if percent <= 0:
            return Decimal("0")

        cashback = order_amount_usd * (percent / Decimal("100"))
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
        يحسب ويضيف الكاشباك لرصيد المستخدم.
        يسجل العملية في cashback_logs.
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

        cashback_usd = await CashbackService.calculate_cashback(order_amount_usd)

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
    async def get_user_total_cashback(session, user_id: int) -> Decimal:
        """يجلب إجمالي الكاشباك الذي حصل عليه المستخدم."""
        from sqlalchemy import select, func

        result = await session.execute(
            select(func.coalesce(func.sum(CashbackLog.cashback_usd), 0)).where(
                CashbackLog.user_id == user_id
            )
        )
        return Decimal(str(result.scalar_one()))
