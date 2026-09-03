"""Tests for the agent program (codes, discount, weekly check)."""

from __future__ import annotations

from decimal import Decimal

from database.engine import async_session_maker
from database.models import Transaction, TransactionType, User
from services.agent_service import AgentError, AgentService
from services.feature_service import FeatureService


async def _make_user(session, telegram_id: int, admin: bool = False) -> User:
    user = User(
        telegram_id=telegram_id,
        full_name=f"User {telegram_id}",
        username=f"user{telegram_id}",
        is_admin=admin,
        balance=Decimal("100"),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


class FakeBot:
    def __init__(self):
        self.sent = []
        self._id = 500

    async def send_message(self, chat_id=None, text=None, **kwargs):
        self._id += 1
        msg = type("M", (), {"message_id": self._id})()
        self.sent.append({"chat_id": chat_id, "text": text})
        return msg


async def test_create_and_redeem_code():
    await FeatureService.reload()
    async with async_session_maker() as session:
        admin = await _make_user(session, 9001, admin=True)
        user = await _make_user(session, 9002)
        code = await AgentService.create_code(session, admin.id)
        assert code.code.startswith("AGENT-")
        assert code.status == "unused"
        assert code.percent == Decimal("10")

        profile = await AgentService.redeem_code(session, user.id, code.code.lower())
        assert profile.status == "active"
        assert profile.percent == Decimal("10")
        assert await AgentService.is_active_agent(session, user.id)

        # الكود استُعمل: لا يمكن استعماله مرة ثانية
        try:
            await AgentService.redeem_code(session, admin.id, code.code)
            assert False, "كان يفترض فشل الاستعمال الثاني"
        except AgentError:
            pass


async def test_redeem_requires_valid_code():
    await FeatureService.reload()
    async with async_session_maker() as session:
        user = await _make_user(session, 9003)
        for bad in ["", "X", "AGENT-NOPE-NOPE"]:
            try:
                await AgentService.redeem_code(session, user.id, bad)
                assert False, f"كان يفترض رفض الكود {bad!r}"
            except AgentError:
                pass


async def test_discount_applied_and_adjusted():
    await FeatureService.reload()
    async with async_session_maker() as session:
        admin = await _make_user(session, 9010, admin=True)
        user = await _make_user(session, 9011)
        code = await AgentService.create_code(session, admin.id, Decimal("10"))
        await AgentService.redeem_code(session, user.id, code.code)

        # غير وكيل → بدون خصم
        other = await _make_user(session, 9012)
        assert await AgentService.apply_discount(session, other.id, Decimal("10")) == Decimal("10")

        # وكيل 10% → 10$ تصبح 9$
        assert await AgentService.apply_discount(session, user.id, Decimal("10")) == Decimal("9.0000")

        # رفع النسبة +5% → 15%
        profile = await AgentService.adjust_percent(session, user.id, Decimal("5"))
        assert profile.percent == Decimal("15")
        assert await AgentService.apply_discount(session, user.id, Decimal("100")) == Decimal("85.0000")

        # خفض النسبة -3% → 12%
        profile = await AgentService.adjust_percent(session, user.id, Decimal("-3"))
        assert profile.percent == Decimal("12")


async def test_percent_clamped_to_bounds():
    await FeatureService.reload()
    async with async_session_maker() as session:
        admin = await _make_user(session, 9020, admin=True)
        user = await _make_user(session, 9021)
        code = await AgentService.create_code(session, admin.id, Decimal("10"))
        await AgentService.redeem_code(session, user.id, code.code)

        # الحد الأدنى 1%
        profile = await AgentService.adjust_percent(session, user.id, Decimal("-50"))
        assert profile.percent == Decimal("1")
        # الحد الأقصى 50%
        profile = await AgentService.adjust_percent(session, user.id, Decimal("+500"))
        assert profile.percent == Decimal("50")


async def test_revoke_and_block_adjust():
    await FeatureService.reload()
    async with async_session_maker() as session:
        admin = await _make_user(session, 9030, admin=True)
        user = await _make_user(session, 9031)
        code = await AgentService.create_code(session, admin.id)
        await AgentService.redeem_code(session, user.id, code.code)

        profile = await AgentService.revoke(session, user.id, "سحب يدوي", admin_id=admin.id)
        assert profile.status == "revoked"
        assert profile.revoke_reason == "سحب يدوي"
        assert not await AgentService.is_active_agent(session, user.id)
        # لا خصم بعد السحب
        assert await AgentService.apply_discount(session, user.id, Decimal("10")) == Decimal("10")

        # تعديل النسبة على وكيل مسلوب → خطأ
        try:
            await AgentService.adjust_percent(session, user.id, Decimal("1"))
            assert False
        except AgentError:
            pass

        # إعادة التفعيل بكود جديد بعد السحب
        code2 = await AgentService.create_code(session, admin.id, Decimal("12"))
        profile2 = await AgentService.redeem_code(session, user.id, code2.code)
        assert profile2.status == "active"
        assert profile2.percent == Decimal("12")


async def test_weekly_check_revokes_low_deposits():
    await FeatureService.reload()
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "agent_program", True)
        admin = await _make_user(session, 9040, admin=True)
        poor = await _make_user(session, 9041)
        rich = await _make_user(session, 9042)

        for user in (poor, rich):
            code = await AgentService.create_code(session, admin.id)
            await AgentService.redeem_code(session, user.id, code.code)

        # غني: إيداع 25$ داخل الأسبوع
        session.add(
            Transaction(
                user_id=rich.id,
                type=TransactionType.DEPOSIT,
                amount=Decimal("25"),
                balance_after=Decimal("25"),
            )
        )
        await session.commit()

        bot = FakeBot()
        revoked = await AgentService.check_weekly_deposits(session, bot)
        assert [p.user_id for p in revoked] == [poor.id]

        # تم إشعار الإدارة عن السحب
        assert len(bot.sent) == 1
        assert "سُحبت وكالة" in bot.sent[0]["text"]

        # المسحوب فقد خصمه
        assert not await AgentService.is_active_agent(session, poor.id)
        assert await AgentService.is_active_agent(session, rich.id)

        # نفس الأسبوع: لا فحص مجدداً (لا إشعارات جديدة)
        revoked2 = await AgentService.check_weekly_deposits(session, bot)
        assert revoked2 == []
        assert len(bot.sent) == 1


async def test_weekly_check_disabled_feature_noop():
    await FeatureService.reload()
    async with async_session_maker() as session:
        # الميزة معطلة افتراضياً
        admin = await _make_user(session, 9050, admin=True)
        user = await _make_user(session, 9051)
        code = await AgentService.create_code(session, admin.id)
        await AgentService.redeem_code(session, user.id, code.code)

        bot = FakeBot()
        revoked = await AgentService.check_weekly_deposits(session, bot)
        assert revoked == []
        assert len(bot.sent) == 0


def test_main_menu_agent_button():
    from keyboards.main_menu import build_main_menu

    kb_on = build_main_menu(
        number_services=[], categories=[], show_agent=True, agent_percent="10"
    )
    data_on = [b.callback_data for row in kb_on.inline_keyboard for b in row]
    assert "agent:home" in data_on

    kb_off = build_main_menu(number_services=[], categories=[], show_agent=False)
    data_off = [b.callback_data for row in kb_off.inline_keyboard for b in row]
    assert "agent:home" not in data_off
