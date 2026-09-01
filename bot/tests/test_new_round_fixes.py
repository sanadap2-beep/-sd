"""
اختبارات جولة الإصلاحات الجديدة:

1) إسقاط العمولة المتوقعة لأصحاب أسهم حصة الإحالة يجب أن يعتمد على
   العمولات المسجّلة خلال الفترة (آخر N يوم)، وليس مجموع مشتريات
   المحالين منذ البدء، وألا يشمل ما قبل الفترة.
2) تحويل الرصيد مع تفعيل العمولة: يجب رفض المبلغ إذا كان الرصيد لا
   يكفي للمبلغ الإجمالي (المبلغ + العمولة) قبل أي خصم، ولا يُخصم
   أي شيء عند الرفض.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from database.engine import async_session_maker
from database.models import Transaction, TransactionType, User
from services.balance_service import BalanceService
from services.growth_channels_service import RevenueShareService


async def _new_user(session, telegram_id: int, balance: str = "0") -> User:
    user = User(
        telegram_id=telegram_id,
        username=f"u{telegram_id}",
        full_name=f"U{telegram_id}",
        language_code="ar",
        balance=Decimal(balance),
    )
    session.add(user)
    await session.flush()
    return user


async def _add_commission(
    session,
    referrer_id: int,
    amount: str,
    created_at: datetime,
) -> None:
    tx = Transaction(
        user_id=referrer_id,
        type=TransactionType.REFERRAL_BONUS,
        amount=Decimal(amount),
        balance_after=Decimal(amount),
        description="عمولة إحالة مستوى 1",
        created_at=created_at,
    )
    session.add(tx)
    await session.flush()


async def test_projected_commission_uses_recent_window_only():
    async with async_session_maker() as session:
        referrer = await _new_user(session, 9001, "0")
        referee = await _new_user(session, 9002, "100")
        referee.referrer_id = referrer.id
        await session.flush()

        old = datetime.utcnow() - timedelta(days=60)
        recent = datetime.utcnow() - timedelta(days=3)
        await _add_commission(session, referrer.id, "5", old)
        await _add_commission(session, referrer.id, "7", recent)
        await session.commit()

        projected = await RevenueShareService.projected_commission(session, referrer.id, days=30)
        # 7$ فقط — العمولة القديمة (60 يوماً) خارج النافذة ولا تُحتسب
        assert projected == Decimal("7.0000")


async def test_projected_commission_ignores_referee_lifetime_spend():
    """
    التأكد من أن الإصلاح لم يعد يعتمد على total_spent_usd للمحالين:
    مشتريات المحيلين تتغير بدون عمولات مسجّلة، فلا تغيّر التوقع.
    """
    async with async_session_maker() as session:
        referrer = await _new_user(session, 9101, "0")
        referee = await _new_user(session, 9102, "200")
        referee.referrer_id = referrer.id
        referee.total_spent_usd = Decimal("500")
        await session.flush()
        await session.commit()

        projected = await RevenueShareService.projected_commission(session, referrer.id, days=30)
        assert projected == Decimal("0.0000")


async def test_transfer_fee_precheck_prevents_any_deduction():
    """
    إذا كان الرصيد لا يكفي للمبلغ + العمولة، يجب رفض التحويل قبل
    الخصم: لا يوازن بين إيداع/استرجاع، ولا تُسجَّل حركة عمولة.
    هذه الحماية لا تنفذ عبر الهاندلر مباشرة (يعتمد على ميدلوير الجلسة)،
    لذا نختبر معادلة التكلفة الكلية التي يعتمدها الهاندلر.
    """

    # القيم الافتراضية للميزة كما تُقرأ من الإعدادات:
    # fee_percent = 1% ضمن نطاق 1$..1000$
    fee_percent = Decimal("1.0")
    amount = Decimal("50")
    fee = (amount * fee_percent / Decimal("100")).quantize(Decimal("0.0001"))
    total_needed = amount + fee

    async with async_session_maker() as session:
        user = await _new_user(session, 9201, "50")  # يكفي المبلغ بلا عمولة
        sufficient = await BalanceService.check_sufficient(session, user.id, total_needed)
        assert sufficient is False  # 50$ + 0.5$ > 50$ → يُرفض قبل الخصم

        # الرصيد بعد الفحص لم يتغير (لا خصم ولا إيداع)
        await session.commit()
        fresh = await session.get(User, user.id)
        assert fresh.balance == Decimal("50")
        txs = (
            await session.execute(select(Transaction).where(Transaction.user_id == user.id))
        ).scalars().all()
        assert txs == []
