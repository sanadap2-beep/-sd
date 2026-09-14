"""اختبارات أقسام الذكاء الاصطناعي: التسعير، دورة الرسالة، الجلسات، وذرة البذر."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from database.engine import async_session_maker
from database.models import (
    AISectionMode,
    AIPricingMode,
    Transaction,
    TransactionType,
    User,
    WhatsAppLink,
    WhatsAppSubscription,
    WALinkStatus,
)
from services.ai_sections_service import (
    AISectionError,
    AISectionService,
    AISessionService,
    guess_extension,
    run_turn,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.nanogpt_service import NanoGPTError, NanoGPTService, _extract_cost
from services.settings_service import SettingsService
from services.whatsapp_bridge_service import (
    WABridgeError,
    WALinkService,
    WASubscriptionService,
    normalize_phone,
)


async def _make_user(telegram_id: int = 777001, balance: str = "10") -> User:
    async with async_session_maker() as session:
        user = User(telegram_id=telegram_id, full_name="مستخدم AI", is_activated=True)
        session.add(user)
        await session.commit()
        if Decimal(balance) > 0:
            await BalanceService.add_balance(
                session, user.id, Decimal(balance), TransactionType.ADMIN_ADD, "اختبار"
            )
        await session.refresh(user)
        return user


async def _get_section(key_title: str):
    async with async_session_maker() as session:
        sections = await AISectionService.list_all(session)
        for section in sections:
            if section.title == key_title:
                return section.id
    raise AssertionError(f"القسم {key_title} غير موجود")


# ══════════════ البذر ══════════════


async def test_default_sections_seeded():
    async with async_session_maker() as session:
        sections = await AISectionService.list_all(session)
        titles = {s.title for s in sections}
        assert "برمجة بدون قيود" in titles
        assert "تحدث بدون قيود" in titles
        code_section = next(s for s in sections if s.mode == AISectionMode.CODE)
        assert code_section.model  # موديل مضبوط


# ══════════════ التسعير ══════════════


async def test_compute_charge_usage_mode():
    async with async_session_maker() as session:
        sections = await AISectionService.list_all(session)
        section = next(s for s in sections if s.pricing_mode == AIPricingMode.USAGE)
        # تكلفة مزود 0.01 × مضاعف 3 = 0.03
        assert AISectionService.compute_charge(section, Decimal("0.01")) == Decimal("0.0300")
        # بدون تكلفة فعلية → التقديرية × 3
        est = section.est_cost_per_message * section.profit_multiplier
        assert AISectionService.compute_charge(section, None) == est.quantize(Decimal("0.0001"))


def test_compute_charge_fixed_mode():
    class FakeSection:
        pricing_mode = AIPricingMode.FIXED
        fixed_price = Decimal("0.02")
        profit_multiplier = Decimal("3")
        est_cost_per_message = Decimal("0.5")

    # السعر الثابت لا يتأثر بالمضاعف ولا بتكلفة المزود
    assert AISectionService.compute_charge(FakeSection(), Decimal("0.9")) == Decimal("0.0200")


def test_guess_extension():
    assert guess_extension("شرح:\n```python\nprint(1)\n```") == "py"
    assert guess_extension("```javascript\nconst x=1;\n```") == "js"
    assert guess_extension("بدون كتلة كود") == "txt"


def test_extract_cost_variants():
    assert _extract_cost({"usage": {"cost": 0.0012}}) == Decimal("0.0012")
    assert _extract_cost({"cost": "0.5"}) == Decimal("0.5")
    assert _extract_cost({"usage": {"nanoCost": 2}}) == Decimal("2")
    assert _extract_cost({"usage": {}}) is None


# ══════════════ دورة الرسالة ══════════════


class _FakeResult:
    def __init__(self, text: str, cost: Decimal | None):
        self.text = text
        self.provider_cost = cost
        self.input_tokens = 10
        self.output_tokens = 20
        self.model = "z-ai/glm-4.6"


async def test_run_turn_charges_actual_and_refunds_diff(monkeypatch):
    user = await _make_user(balance="10")
    section_id = await _get_section("تحدث بدون قيود")

    async with async_session_maker() as session:
        section = await AISectionService.get(session, section_id)

        async def fake_complete(model, messages, **kwargs):
            # المحادثة تصل مع السياق: system + رسالة المستخدم
            assert messages[-1]["role"] == "user"
            return _FakeResult("أهلاً بك!", Decimal("0.001"))

        monkeypatch.setattr(NanoGPTService, "complete", fake_complete)

        result = await run_turn(session, user, section, "مرحبا")
        # التقديري: 0.001 × 3 = 0.003 → الفعلي 0.003 → لا فرق
        assert result["paid"] == Decimal("0.0030")

        balance_after = await BalanceService.get_balance(session, user.id)
        assert balance_after == Decimal("9.9970")  # 10 - 0.003

        ai_session = result["ai_session"]
        assert ai_session.messages_count == 2
        assert ai_session.charged_total == Decimal("0.0030")
        saved = await AISessionService.get_messages(session, ai_session)
        assert len(saved) == 2
        roles = {m.role.value for m in saved}
        assert roles == {"user", "assistant"}
        # التأكد من تسجيل الاسترجاع/الخصم في الدفاتر
        txs = (
            (
                await session.execute(
                    Transaction.__table__.select().where(Transaction.user_id == user.id)
                )
            )
        ).fetchall()
        types = {row.type for row in txs}
        assert TransactionType.AI_USAGE.value in types or "AI_USAGE" in types


async def test_run_turn_adjusts_up_when_provider_more_expensive(monkeypatch):
    user = await _make_user(balance="10", telegram_id=777002)
    section_id = await _get_section("تحدث بدون قيود")

    async with async_session_maker() as session:
        section = await AISectionService.get(session, section_id)

        async def fake_complete(model, messages, **kwargs):
            return _FakeResult("رد", Decimal("0.02"))  # الفعلي 0.06 > التقديري 0.009

        monkeypatch.setattr(NanoGPTService, "complete", fake_complete)
        result = await run_turn(session, user, section, "سؤال")
        assert result["paid"] == Decimal("0.0600")
        balance_after = await BalanceService.get_balance(session, user.id)
        assert balance_after == Decimal("9.9400")


async def test_run_turn_refunds_on_provider_failure(monkeypatch):
    user = await _make_user(balance="10", telegram_id=777003)
    section_id = await _get_section("تحدث بدون قيود")

    async with async_session_maker() as session:
        section = await AISectionService.get(session, section_id)

        async def failing_complete(model, messages, **kwargs):
            raise NanoGPTError("انفجر المزود")

        monkeypatch.setattr(NanoGPTService, "complete", failing_complete)
        with pytest.raises(NanoGPTError):
            await run_turn(session, user, section, "رسالة")

        # الرصيد رجع كما كان — الحجز أُرجع كاملاً
        balance_after = await BalanceService.get_balance(session, user.id)
        assert balance_after == Decimal("10.0000")


async def test_run_turn_rejects_insufficient_balance(monkeypatch):
    user = await _make_user(balance="0", telegram_id=777004)
    section_id = await _get_section("تحدث بدون قيود")

    async with async_session_maker() as session:
        section = await AISectionService.get(session, section_id)

        async def should_not_be_called(model, messages, **kwargs):
            raise AssertionError("لا يجب استدعاء المزود بدون رصيد")

        monkeypatch.setattr(NanoGPTService, "complete", should_not_be_called)
        with pytest.raises(InsufficientBalanceError):
            await run_turn(session, user, section, "رسالة")


async def test_chat_session_context_is_sent(monkeypatch):
    """الجلسة التانية في نفس المحادثة ترسل السياق السابق للموديل."""
    user = await _make_user(balance="10", telegram_id=777005)
    section_id = await _get_section("تحدث بدون قيود")

    captured: list[list[dict]] = []

    async with async_session_maker() as session:
        section = await AISectionService.get(session, section_id)

        async def fake_complete(model, messages, **kwargs):
            captured.append(list(messages))
            return _FakeResult("تمام", Decimal("0.001"))

        monkeypatch.setattr(NanoGPTService, "complete", fake_complete)
        first = await run_turn(session, user, section, "اسمي أحمد")
        ai_session = first["ai_session"]
        await run_turn(session, user, section, "ما اسمي؟", ai_session=ai_session)

    second_call = captured[1]
    contents = [m["content"] for m in second_call]
    assert any("اسمي أحمد" in c for c in contents)  # الرسالة الأولى موجودة بالسياق
    assert second_call[-1]["content"] == "ما اسمي؟"


# ══════════════ الجلسات ══════════════


async def test_sessions_scoped_to_user():
    user_a = await _make_user(telegram_id=777006)
    user_b = await _make_user(telegram_id=777007)
    section_id = await _get_section("تحدث بدون قيود")

    async with async_session_maker() as session:
        section = await AISectionService.get(session, section_id)
        s_a = await AISessionService.new_session(session, user_a.id, section)
        s_b = await AISessionService.new_session(session, user_b.id, section)

        assert (await AISessionService.get_user_session(session, s_a.id, user_a.id)) is not None
        # مستخدم ثانٍ لا يرى جلسة الأول
        assert (await AISessionService.get_user_session(session, s_a.id, user_b.id)) is None
        assert s_b.id != s_a.id


# ══════════════ واتساب: الاشتراك والربط ══════════════


def test_normalize_phone():
    assert normalize_phone("+963955123456") == "+963955123456"
    assert normalize_phone("963955123456") == "+963955123456"
    with pytest.raises(WABridgeError):
        normalize_phone("123")
    with pytest.raises(WABridgeError):
        normalize_phone("abc")


async def test_wa_subscription_charges_and_stacks():
    user = await _make_user(telegram_id=777008, balance="5")

    async with async_session_maker() as session:
        await SettingsService.set(session, "wa_daily_price", "1.00")
        session.expire_all()

        sub, amount = await WASubscriptionService.subscribe(session, user, days=1)
        assert amount == Decimal("1.00")
        balance_after_first = await BalanceService.get_balance(session, user.id)
        assert balance_after_first == Decimal("4.0000")

        until_first = sub.paid_until
        sub2, _ = await WASubscriptionService.subscribe(session, user, days=1)
        # التمديد يُكدّس فوق المتبقي
        delta = sub2.paid_until - until_first
        assert abs(delta - timedelta(days=1)) < timedelta(minutes=1)
        balance_after_second = await BalanceService.get_balance(session, user.id)
        assert balance_after_second == Decimal("3.0000")

        active = await WASubscriptionService.active_until(session, user.id)
        assert active is not None
        txs = (
            (
                await session.execute(
                    Transaction.__table__.select().where(Transaction.user_id == user.id)
                )
            )
        ).fetchall()
        wa_txs = [t for t in txs if t.type in (TransactionType.WA_SUBSCRIPTION.value, "WA_SUBSCRIPTION")]
        assert len(wa_txs) == 2


async def test_wa_subscription_insufficient_balance():
    user = await _make_user(telegram_id=777009, balance="0")

    async with async_session_maker() as session:
        await SettingsService.set(session, "wa_daily_price", "1.00")
        session.expire_all()
        with pytest.raises(InsufficientBalanceError):
            await WASubscriptionService.subscribe(session, user, days=1)


async def test_wa_link_parse_buttons():
    user = await _make_user(telegram_id=777010)

    async with async_session_maker() as session:
        link = WhatsAppLink(
            user_id=user.id,
            phone="+963955123456",
            status=WALinkStatus.LINKED,
            bridge_session_id="sess_x",
        )
        link.last_menu_json = (
            '{"buttons": [{"text": "شراء", "action": "buy"}, {"text": "بدون عمل"}]}'
        )
        session.add(link)
        await session.commit()
        await session.refresh(link)

        buttons = WALinkService.parse_buttons(link)
        # الزر بلا action يُستبعد
        assert len(buttons) == 1
        assert buttons[0]["action"] == "buy"
