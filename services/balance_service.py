"""
الخدمة المركزية الوحيدة المسموح فيها بتعديل رصيد أي مستخدم.
تستخدم قفل (asyncio.Lock) لكل مستخدم لمنع أي race condition.
العملة الداخلية: دولار أمريكي (USD) بالكامل.
"""

import asyncio
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select, desc
from sqlalchemy.exc import IntegrityError

from database.models import User, Transaction, TransactionType, Transfer


class InsufficientBalanceError(Exception):
    pass


class BalanceService:
    """
    خدمة الرصيد المركزية.

    القفل داخل العملية يخفف التصادم داخل worker واحد، أما قفل صف المستخدم
    عبر قاعدة البيانات فيحمي العمليات عند تشغيل أكثر من worker على PostgreSQL.
    تبقى العمليات الخارجية بحاجة إلى idempotency key وحالة طلب محفوظة.
    """

    _locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    @staticmethod
    async def _get_user_for_update(session, user_id: int) -> User | None:
        """Load a user while locking its row on databases that support it."""
        result = await session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_transaction_by_reference(session, payment_reference: str | None):
        """Return the ledger entry for an idempotency reference, if present."""
        if not payment_reference:
            return None
        result = await session.execute(
            select(Transaction).where(Transaction.payment_reference == payment_reference)
        )
        return result.scalar_one_or_none()

    @classmethod
    def _get_lock(cls, user_id: int) -> asyncio.Lock:
        return cls._locks[user_id]

    @classmethod
    def cleanup_idle_locks(cls) -> int:
        idle_user_ids = [uid for uid, lock in cls._locks.items() if not lock.locked()]
        for uid in idle_user_ids:
            cls._locks.pop(uid, None)
        return len(idle_user_ids)

    @classmethod
    async def get_balance(cls, session, user_id: int) -> Decimal:
        """يجلب رصيد المستخدم الحالي بالدولار."""
        user = await session.get(User, user_id)
        if user is None:
            return Decimal("0")
        return user.balance

    @classmethod
    async def check_sufficient(cls, session, user_id: int, amount: Decimal) -> bool:
        """
        يتحقق من كفاية الرصيد بدون خصم.
        يُستخدم لعرض رسالة الرصيد غير الكافي قبل بدء عملية الشراء.
        """
        balance = await cls.get_balance(session, user_id)
        return balance >= amount

    @classmethod
    async def add_balance(
        cls,
        session,
        user_id: int,
        amount: Decimal,
        tx_type: TransactionType,
        description: str | None = None,
        related_table: str | None = None,
        related_id: int | None = None,
        payment_reference: str | None = None,
    ) -> User:
        if not amount.is_finite() or amount <= 0:
            raise ValueError("المبلغ يجب أن يكون رقماً موجباً ومنتهياً")

        async with cls._get_lock(user_id):
            user = await cls._get_user_for_update(session, user_id)
            if user is None:
                raise ValueError(f"المستخدم {user_id} غير موجود")

            # عمليات الدفع قد تُرسل أكثر من مرة من Telegram أو من مراقب
            # الفواتير. لا نضيف الرصيد مجدداً إذا تمت معالجة نفس المرجع.
            if payment_reference:
                existing = await cls.get_transaction_by_reference(
                    session, payment_reference
                )
                if existing is not None:
                    if (
                        existing.user_id != user_id
                        or existing.type != tx_type
                        or existing.amount != amount
                    ):
                        raise ValueError("مرجع دفعة مستخدم مسبقاً ببيانات مختلفة")
                    await session.refresh(user)
                    return user

            # كذلك نمنع تكرار الحركات المرتبطة بسجل داخلي، مثل قبول نفس
            # طلب الإيداع أو استرجاع نفس الطلب مرتين بعد إعادة المحاولة.
            if related_table and related_id is not None:
                existing_result = await session.execute(
                    select(Transaction).where(
                        Transaction.user_id == user_id,
                        Transaction.type == tx_type,
                        Transaction.related_table == related_table,
                        Transaction.related_id == related_id,
                    )
                )
                existing = existing_result.scalar_one_or_none()
                if existing is not None:
                    if existing.amount != amount:
                        raise ValueError("الحركة المرتبطة موجودة بمبلغ مختلف")
                    await session.refresh(user)
                    return user

            user.balance = user.balance + amount

            session.add(
                Transaction(
                    user_id=user_id,
                    type=tx_type,
                    amount=amount,
                    balance_after=user.balance,
                    description=description,
                    related_table=related_table,
                    related_id=related_id,
                    payment_reference=payment_reference,
                )
            )

            try:
                await session.commit()
            except IntegrityError:
                # A second bot instance may have inserted the same external
                # payment reference between our check and commit. Recover
                # idempotently instead of reporting a false payment failure.
                await session.rollback()
                if payment_reference:
                    existing = await cls.get_transaction_by_reference(
                        session, payment_reference
                    )
                    if existing is not None:
                        if (
                            existing.user_id != user_id
                            or existing.type != tx_type
                            or existing.amount != amount
                        ):
                            raise ValueError("مرجع دفعة مستخدم مسبقاً ببيانات مختلفة")
                        user = await cls._get_user_for_update(session, user_id)
                        return user
                raise

            await session.refresh(user)
            return user

    @classmethod
    async def deduct_balance(
        cls,
        session,
        user_id: int,
        amount: Decimal,
        tx_type: TransactionType,
        description: str | None = None,
        related_table: str | None = None,
        related_id: int | None = None,
        is_purchase: bool = False,
        payment_reference: str | None = None,
    ) -> User:
        if not amount.is_finite() or amount <= 0:
            raise ValueError("المبلغ يجب أن يكون رقماً موجباً ومنتهياً")

        async with cls._get_lock(user_id):
            user = await cls._get_user_for_update(session, user_id)
            if user is None:
                raise ValueError(f"المستخدم {user_id} غير موجود")

            # A retry with the same business reference must not debit twice.
            # The unique database index is the final guard across workers;
            # this lookup handles the common sequential retry cheaply.
            if payment_reference:
                existing = await cls.get_transaction_by_reference(
                    session, payment_reference
                )
                if existing is not None:
                    if existing.user_id != user_id or existing.amount != -amount:
                        raise ValueError("مرجع دفعة مستخدم مسبقاً ببيانات مختلفة")
                    await session.refresh(user)
                    return user

            if user.balance < amount:
                raise InsufficientBalanceError(
                    f"رصيد غير كافٍ: المتاح {user.balance}$، المطلوب {amount}$"
                )

            user.balance = user.balance - amount

            if is_purchase:
                user.total_spent_usd = user.total_spent_usd + amount
                user.total_orders = user.total_orders + 1

            session.add(
                Transaction(
                    user_id=user_id,
                    type=tx_type,
                    amount=-amount,
                    balance_after=user.balance,
                    description=description,
                    related_table=related_table,
                    related_id=related_id,
                    payment_reference=payment_reference,
                )
            )

            try:
                await session.commit()
            except IntegrityError:
                # A second worker may have committed the same reference after
                # our lookup. Roll back the attempted debit and treat the
                # already committed ledger row as the successful operation.
                await session.rollback()
                if payment_reference:
                    existing = await cls.get_transaction_by_reference(
                        session, payment_reference
                    )
                    if existing is not None:
                        if existing.user_id != user_id or existing.amount != -amount:
                            raise ValueError("مرجع دفعة مستخدم مسبقاً ببيانات مختلفة")
                        user = await cls._get_user_for_update(session, user_id)
                        return user
                raise

            await session.refresh(user)
            return user

    @classmethod
    async def transfer(
        cls,
        session,
        from_user_id: int,
        to_user_id: int,
        amount: Decimal,
    ) -> tuple[User, User, Transfer]:
        """Transfer balance atomically and write both ledger entries.

        The previous implementation committed the debit and credit in two
        separate calls. A crash between those calls could destroy a user's
        balance. Locks are acquired in a stable order to avoid deadlocks.
        """
        if not amount.is_finite() or amount <= 0:
            raise ValueError("المبلغ يجب أن يكون رقماً موجباً ومنتهياً")
        if from_user_id == to_user_id:
            raise ValueError("لا يمكنك التحويل لنفسك")

        first_id, second_id = sorted((from_user_id, to_user_id))
        first_lock = cls._get_lock(first_id)
        second_lock = cls._get_lock(second_id)

        async with first_lock:
            async with second_lock:
                locked_users = {
                    user_id: await cls._get_user_for_update(session, user_id)
                    for user_id in (first_id, second_id)
                }
                source = locked_users[from_user_id]
                recipient = locked_users[to_user_id]
                if source is None or recipient is None:
                    raise ValueError("المستخدم غير موجود")
                if source.balance < amount:
                    raise InsufficientBalanceError(
                        f"رصيد غير كافٍ: المتاح {source.balance}$، المطلوب {amount}$"
                    )

                source.balance -= amount
                recipient.balance += amount

                transfer = Transfer(
                    from_user_id=from_user_id,
                    to_user_id=to_user_id,
                    amount=amount,
                )
                session.add(transfer)
                await session.flush()

                session.add_all(
                    [
                        Transaction(
                            user_id=from_user_id,
                            type=TransactionType.TRANSFER_OUT,
                            amount=-amount,
                            balance_after=source.balance,
                            related_table="transfers",
                            related_id=transfer.id,
                            description=f"تحويل إلى {recipient.telegram_id}",
                        ),
                        Transaction(
                            user_id=to_user_id,
                            type=TransactionType.TRANSFER_IN,
                            amount=amount,
                            balance_after=recipient.balance,
                            related_table="transfers",
                            related_id=transfer.id,
                            description=f"تحويل من {source.telegram_id}",
                        ),
                    ]
                )
                await session.commit()
                await session.refresh(source)
                await session.refresh(recipient)
                return source, recipient, transfer

    @classmethod
    async def get_transactions(
        cls,
        session,
        user_id: int,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Transaction]:
        """يجلب سجل معاملات المستخدم مرتبة من الأحدث للأقدم."""
        result = await session.execute(
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(desc(Transaction.created_at))
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())
