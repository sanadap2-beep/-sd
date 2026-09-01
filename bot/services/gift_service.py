"""خدمة بطاقات الهدايا وشحن الرصيد الآمن."""

from __future__ import annotations

import secrets
import string
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from database.models import (
    GiftCode,
    GiftRedemption,
    Transaction,
    TransactionType,
    User,
)
from services.balance_service import BalanceService
from services.input_validation_service import InputValidationError, InputValidationService


class GiftCodeError(Exception):
    """خطأ قابل للعرض في بطاقات الهدايا."""


class GiftService:
    @staticmethod
    def _generate_code() -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "GIFT-" + "".join(secrets.choice(alphabet) for _ in range(12))

    @staticmethod
    async def create(
        session,
        amount_usd: Decimal,
        max_uses: int,
        created_by: int,
        expires_at: datetime | None = None,
    ) -> GiftCode:
        try:
            amount_usd = InputValidationService.positive_money(amount_usd)
        except InputValidationError as exc:
            raise GiftCodeError(str(exc)) from exc
        if max_uses < 1:
            raise GiftCodeError("عدد الاستخدامات يجب أن يكون أكبر من صفر.")
        for _ in range(5):
            code = GiftService._generate_code()
            existing = await session.execute(select(GiftCode.id).where(GiftCode.code == code))
            if existing.scalar_one_or_none() is None:
                gift = GiftCode(
                    code=code,
                    amount_usd=amount_usd,
                    max_uses=max_uses,
                    created_by=created_by,
                    expires_at=expires_at,
                    is_active=True,
                )
                session.add(gift)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    continue
                await session.refresh(gift)
                return gift
        raise GiftCodeError("تعذر إنشاء رمز فريد، حاول مجدداً.")

    @staticmethod
    async def redeem(
        session,
        user_id: int,
        raw_code: str,
    ) -> Decimal:
        code = raw_code.strip().upper()
        if len(code) < 6 or len(code) > 32:
            raise GiftCodeError("رمز البطاقة غير صالح.")

        async with BalanceService._get_lock(user_id):
            result = await session.execute(select(GiftCode).where(GiftCode.code == code))
            gift = result.scalar_one_or_none()
            if gift is None or not gift.is_active:
                raise GiftCodeError("البطاقة غير موجودة أو غير مفعلة.")
            if gift.expires_at and datetime.utcnow() >= gift.expires_at:
                raise GiftCodeError("انتهت صلاحية بطاقة الهدايا.")
            already = await session.execute(
                select(GiftRedemption).where(
                    GiftRedemption.gift_code_id == gift.id,
                    GiftRedemption.user_id == user_id,
                )
            )
            if already.scalar_one_or_none() is not None:
                raise GiftCodeError("استخدمت هذه البطاقة مسبقاً.")

            updated = await session.execute(
                update(GiftCode)
                .where(
                    GiftCode.id == gift.id,
                    GiftCode.is_active.is_(True),
                    GiftCode.used_count < GiftCode.max_uses,
                )
                .values(used_count=GiftCode.used_count + 1)
            )
            if updated.rowcount != 1:
                raise GiftCodeError("تم استنفاد استخدامات هذه البطاقة.")

            user = await session.get(User, user_id)
            if user is None:
                raise GiftCodeError("المستخدم غير موجود.")
            user.balance += gift.amount_usd
            redemption = GiftRedemption(
                gift_code_id=gift.id,
                user_id=user_id,
                amount_usd=gift.amount_usd,
            )
            session.add(redemption)
            await session.flush()
            session.add(
                Transaction(
                    user_id=user_id,
                    type=TransactionType.GIFT_REDEEM,
                    amount=gift.amount_usd,
                    balance_after=user.balance,
                    related_table="gift_redemptions",
                    related_id=redemption.id,
                    payment_reference=f"gift:{redemption.id}",
                    description=f"استبدال بطاقة هدية {gift.code}",
                )
            )
            await session.commit()
            return gift.amount_usd
