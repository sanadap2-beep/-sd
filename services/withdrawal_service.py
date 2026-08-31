"""User balance withdrawals reviewed by admins."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import desc, select

from database.models import TransactionType, User, WithdrawalRequest
from services.balance_service import BalanceService, InsufficientBalanceError
from services.settings_service import SettingsService


class WithdrawalError(Exception):
    pass


class WithdrawalService:
    @staticmethod
    async def shamcash_syp_enabled() -> bool:
        return await SettingsService.get_bool("withdraw_shamcash_syp_enabled", False)

    @staticmethod
    async def min_amount_usd() -> Decimal:
        return await SettingsService.get_decimal("withdraw_min_usd", Decimal("1"))

    @staticmethod
    async def create(
        session,
        user_id: int,
        method: str,
        amount_usd: Decimal,
        payout_address: str,
        currency: str = "USD",
        network: str | None = None,
    ) -> WithdrawalRequest:
        if amount_usd <= 0:
            raise WithdrawalError("المبلغ يجب أن يكون موجباً.")
        minimum = await WithdrawalService.min_amount_usd()
        if amount_usd < minimum:
            raise WithdrawalError(f"الحد الأدنى للسحب {minimum}$.")
        if method == "shamcash" and currency == "SYP" and not await WithdrawalService.shamcash_syp_enabled():
            raise WithdrawalError("السحب بالليرة عبر شام كاش غير متاح حالياً، اختر الدولار.")
        user = await session.get(User, user_id)
        if user is None or user.balance < amount_usd:
            raise WithdrawalError("رصيدك غير كافٍ لهذا السحب.")
        rate = await SettingsService.get_decimal("usd_to_syp_rate", Decimal("130"))
        payout_amount = (amount_usd * rate).quantize(Decimal("1")) if currency == "SYP" else amount_usd

        request = WithdrawalRequest(
            user_id=user_id,
            method=method,
            currency=currency,
            network=network,
            amount_usd=amount_usd,
            payout_amount=payout_amount,
            payout_address=payout_address[:255],
            status="pending",
        )
        session.add(request)
        await session.flush()
        try:
            await BalanceService.deduct_balance(
                session,
                user_id,
                amount_usd,
                TransactionType.ADMIN_DEDUCT,
                description=f"حجز طلب سحب #{request.id}",
                related_table="withdrawal_requests",
                related_id=request.id,
            )
        except InsufficientBalanceError as exc:
            raise WithdrawalError("رصيدك غير كافٍ لهذا السحب.") from exc
        await session.refresh(request)
        return request

    @staticmethod
    async def pending(session, limit: int = 20) -> list[WithdrawalRequest]:
        result = await session.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.status == "pending").order_by(desc(WithdrawalRequest.created_at)).limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def complete(session, request_id: int, admin_id: int) -> WithdrawalRequest | None:
        request = await session.get(WithdrawalRequest, request_id)
        if request is None or request.status != "pending":
            return None
        request.status = "paid"
        request.admin_id = admin_id
        request.processed_at = datetime.utcnow()
        await session.commit()
        await session.refresh(request)
        return request

    @staticmethod
    async def reject(session, request_id: int, admin_id: int, note: str = "رفض إداري") -> WithdrawalRequest | None:
        request = await session.get(WithdrawalRequest, request_id)
        if request is None or request.status != "pending":
            return None
        await BalanceService.add_balance(
            session,
            request.user_id,
            request.amount_usd,
            TransactionType.REFUND,
            description=f"استرجاع طلب سحب #{request.id}",
            related_table="withdrawal_requests",
            related_id=request.id,
            payment_reference=f"withdraw_refund:{request.id}",
        )
        request.status = "rejected"
        request.admin_id = admin_id
        request.admin_note = note[:255]
        request.processed_at = datetime.utcnow()
        await session.commit()
        await session.refresh(request)
        return request
