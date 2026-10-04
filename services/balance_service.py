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
    ⚠️ القفل يعمل على مستوى العملية الواحدة فقط.
    لو تم توسيع الاستضافة لأكثر من instance يلزم قفل موزع عبر Redis.
    """

    _locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

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
        tenant_id: int = 0,
    ) -> User:
        if not amount.is_finite() or amount <= 0:
            raise ValueError("المبلغ يجب أن يكون رقماً موجباً ومنتهياً")

        async with cls._get_lock(user_id):
            user = await session.get(User, user_id)
            if user is None:
                raise ValueError(f"المستخدم {user_id} غير موجود")

            # عمليات الدفع قد تُرسل أكثر من مرة من Telegram أو من مراقب
            # الفواتير. لا نضيف الرصيد مجدداً إذا تمت معالجة نفس المرجع.
            if payment_reference:
                existing_result = await session.execute(
                    select(Transaction).where(Transaction.payment_reference == payment_reference)
                )
                existing = existing_result.scalar_one_or_none()
                if existing is not None:
                    if existing.user_id != user_id or existing.amount != amount:
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
                    tenant_id=tenant_id,
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
                    existing_result = await session.execute(
                        select(Transaction).where(
                            Transaction.payment_reference == payment_reference
                        )
                    )
                    existing = existing_result.scalar_one_or_none()
                    if existing is not None:
                        if existing.user_id != user_id or existing.amount != amount:
                            raise ValueError("مرجع دفعة مستخدم مسبقاً ببيانات مختلفة")
                        user = await session.get(User, user_id)
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
        tenant_id: int = 0,
    ) -> User:
        if not amount.is_finite() or amount <= 0:
            raise ValueError("المبلغ يجب أن يكون رقماً موجباً ومنتهياً")

        async with cls._get_lock(user_id):
            from sqlalchemy import update as _update

            # تحديث ذري: ينجح فقط إذا الرصيد كافٍ — يغلق سباق العمليات
            # (bot + api حاويتان على Postgres مشترك) حيث القفل داخل العملية لا يكفي.
            values: dict = {User.balance: User.balance - amount}
            if is_purchase:
                values[User.total_spent_usd] = User.total_spent_usd + amount
                values[User.total_orders] = User.total_orders + 1
            result = await session.execute(
                _update(User)
                .where(User.id == user_id, User.balance >= amount)
                .values(values)
                .execution_options(synchronize_session=False)
            )
            if (result.rowcount or 0) == 0:
                # ميز بين غير موجود وغير كافٍ لرسالة أدق
                user = await session.get(User, user_id)
                if user is None:
                    raise ValueError(f"المستخدم {user_id} غير موجود")
                raise InsufficientBalanceError(
                    f"رصيد غير كافٍ: المتاح {user.balance}$، المطلوب {amount}$"
                )

            # أبطل كاش نسخة المستخدم فقط حتى لا يفلش ORM نسخة قديمة فوق التحديث الذري
            # (expire_all كان يُبطل كائنات المتصل مثل Product/Order في checkout ويكسر الـ flush)
            user = await session.get(User, user_id)
            try:
                await session.refresh(user, attribute_names=["balance", "total_spent_usd", "total_orders"])
            except Exception:
                pass
            session.add(
                Transaction(
                    user_id=user_id,
                    type=tx_type,
                    amount=-amount,
                    balance_after=user.balance,
                    description=description,
                    related_table=related_table,
                    related_id=related_id,
                    tenant_id=tenant_id,
                )
            )

            await session.commit()
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
        return await cls.transfer_with_fee(
            session, from_user_id, to_user_id, amount, fee_amount=Decimal("0"),
            fee_description=None,
        )

    @classmethod
    async def transfer_with_fee(
        cls,
        session,
        from_user_id: int,
        to_user_id: int,
        amount: Decimal,
        fee_amount: Decimal = Decimal("0"),
        fee_description: str | None = None,
    ) -> tuple[User, User, Transfer]:
        """تحويل ذري واحد: العمولة + الصافي في commit واحد بلا حالة وسطية.

        total = amount + fee يُخصم من المرسل، amount يصل المستلم، fee إيراد منصة
        (صف دفتر ثالث). أي عطل قبل الـ commit لا يخصم شيئاً إطلاقاً.
        """
        if not amount.is_finite() or amount <= 0:
            raise ValueError("المبلغ يجب أن يكون رقماً موجباً ومنتهياً")
        if fee_amount is None:
            fee_amount = Decimal("0")
        if fee_amount < 0 or not fee_amount.is_finite():
            raise ValueError("العمولة غير صالحة")
        if from_user_id == to_user_id:
            raise ValueError("لا يمكنك التحويل لنفسك")

        from sqlalchemy import update as _update

        total = amount + fee_amount
        first_id, second_id = sorted((from_user_id, to_user_id))
        first_lock = cls._get_lock(first_id)
        second_lock = cls._get_lock(second_id)

        async with first_lock:
            async with second_lock:
                result = await session.execute(
                    _update(User)
                    .where(User.id == from_user_id, User.balance >= total)
                    .values(balance=User.balance - total)
                    .execution_options(synchronize_session=False)
                )
                if (result.rowcount or 0) == 0:
                    source = await session.get(User, from_user_id)
                    if source is None:
                        raise ValueError("المستخدم غير موجود")
                    raise InsufficientBalanceError(
                        f"رصيد غير كافٍ: المتاح {source.balance}$، المطلوب {total}$"
                    )
                await session.execute(
                    _update(User)
                    .where(User.id == to_user_id)
                    .values(balance=User.balance + amount)
                    .execution_options(synchronize_session=False)
                )
                source = await session.get(User, from_user_id)
                recipient = await session.get(User, to_user_id)
                try:
                    if source is not None:
                        await session.refresh(source, attribute_names=["balance"])
                except Exception:
                    pass
                try:
                    if recipient is not None:
                        await session.refresh(recipient, attribute_names=["balance"])
                except Exception:
                    pass
                if source is None or recipient is None:
                    raise ValueError("المستخدم غير موجود")

                transfer = Transfer(
                    from_user_id=from_user_id,
                    to_user_id=to_user_id,
                    amount=amount,
                )
                session.add(transfer)
                await session.flush()

                rows = [
                    Transaction(
                        user_id=from_user_id,
                        type=TransactionType.TRANSFER_OUT,
                        amount=-(amount),
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
                if fee_amount > 0:
                    rows.append(
                        Transaction(
                            user_id=from_user_id,
                            type=TransactionType.PURCHASE,
                            amount=-fee_amount,
                            balance_after=source.balance,
                            related_table="transfers",
                            related_id=transfer.id,
                            description=fee_description or "عمولة تحويل",
                        )
                    )
                session.add_all(rows)
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
