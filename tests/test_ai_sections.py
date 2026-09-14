"""Tests for the main AI sections (programming/chat, per-message billing)."""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal

import pytest
from sqlalchemy import select

import services.ai_section_service as ai_service
from database.engine import async_session_maker
from database.models import (
    AiMessage,
    AiSection,
    AiSession,
    Transaction,
    TransactionType,
    User,
)
from services.ai_provider_client import AiCompletion, AiProviderError
from services.ai_section_service import (
    AiConversationService,
    AiSectionService,
    extract_code_artifacts,
    files_to_zip,
)
from services.balance_service import InsufficientBalanceError


async def _make_user(session, balance: str = "10.00", telegram_id: int = 111) -> User:
    user = User(telegram_id=telegram_id, balance=Decimal(balance))
    session.add(user)
    await session.flush()
    return user


async def _make_section(
    session,
    cost: str = "0.01",
    multiplier: float = 3.0,
    kind: str = "chat",
    enabled: bool = True,
    key: str = "ai_chat",
) -> AiSection:
    return await AiSectionService.create(
        session,
        key=key,
        name_ar="دردشة",
        kind=kind,
        model="test/model",
        cost_per_message_usd=Decimal(cost),
        profit_multiplier=multiplier,
        enabled=enabled,
        description_ar="قسم تجريبي",
    )


# ══════════════ التسعير ══════════════


async def test_sell_price_is_cost_plus_profit():
    async with async_session_maker() as session:
        section = await _make_section(session, cost="0.01", multiplier=3.0)
    assert AiSectionService.sell_price(section) == Decimal("0.0400")

    async with async_session_maker() as session:
        section2 = await _make_section(
            session, cost="0.05", multiplier=2.0, key="ai_coding"
        )
    assert AiSectionService.sell_price(section2) == Decimal("0.1500")
    assert AiSectionService.profit_per_message(section2) == Decimal("0.1000")


async def test_create_duplicate_key_rejected():
    async with async_session_maker() as session:
        await _make_section(session, key="ai_x")
        with pytest.raises(Exception):
            await _make_section(session, key="ai_x")


# ══════════════ المعالجة والخصم ══════════════


async def _fake_ok(*args, **kwargs):
    return AiCompletion(text="رد تجريبي", usage={"total_tokens": 10})


async def _fake_fail(*args, **kwargs):
    raise AiProviderError("فشل المزود")


async def test_process_message_success_deducts_and_saves(
    monkeypatch
):
    monkeypatch.setattr(ai_service, "chat_completion", _fake_ok)
    async with async_session_maker() as session:
        user = await _make_user(session, balance="10.00")
        section = await _make_section(session)
        result = await AiConversationService.process_message(
            session, user, section, "مرحبا"
        )
        assert result["ok"] is True
        assert result["text"] == "رد تجريبي"
        await session.refresh(user)
        # خصم 0.04$ (0.01 + 3× ربح)
        assert user.balance == Decimal("9.9600")

        ai_session = await AiConversationService.get_session(session, user, section)
        assert ai_session is not None
        assert ai_session.title == "مرحبا"
        assert ai_session.message_count == 2
        messages = await AiConversationService.latest_messages(session, ai_session)
        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[1].cost_usd == Decimal("0.0100")

        txs = (
            (
                await session.execute(
                    select(Transaction).where(
                        Transaction.user_id == user.id,
                        Transaction.type == TransactionType.AI_USAGE,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(txs) == 1
        assert txs[0].amount == -Decimal("0.0400")


async def test_process_message_refunds_on_provider_failure(
    monkeypatch
):
    monkeypatch.setattr(ai_service, "chat_completion", _fake_fail)
    async with async_session_maker() as session:
        user = await _make_user(session, balance="10.00")
        section = await _make_section(session)
        result = await AiConversationService.process_message(
            session, user, section, "مرحبا"
        )
        assert result["ok"] is False
        await session.refresh(user)
        # رُجع كامل: لا خصم صافي.
        assert user.balance == Decimal("10.0000")
        ai_session = await AiConversationService.get_session(session, user, section)
        messages = await AiConversationService.latest_messages(session, ai_session)
        assert [m.role for m in messages] == ["user"]  # لا رد مساعد


async def test_refund_is_idempotent(monkeypatch):
    """إعادة المحاولة بعد فشل لا تخصم مرتين ولا ترجع مرتين."""
    monkeypatch.setattr(ai_service, "chat_completion", _fake_fail)
    async with async_session_maker() as session:
        user = await _make_user(session, balance="10.00")
        section = await _make_section(session)
        await AiConversationService.process_message(session, user, section, "أول")
        await AiConversationService.process_message(session, user, section, "ثاني")
        await session.refresh(user)
        assert user.balance == Decimal("10.0000")
        # رسالتا مستخدم فقط.
        ai_session = await AiConversationService.get_session(session, user, section)
        messages = await AiConversationService.latest_messages(session, ai_session)
        assert len(messages) == 2


async def test_insufficient_balance_raises_and_keeps_balance(
    monkeypatch
):
    monkeypatch.setattr(ai_service, "chat_completion", _fake_ok)
    async with async_session_maker() as session:
        user = await _make_user(session, balance="0.0100")
        section = await _make_section(session)  # السعر 0.04
        with pytest.raises(InsufficientBalanceError):
            await AiConversationService.process_message(session, user, section, "hi")
        await session.refresh(user)
        assert user.balance == Decimal("0.0100")


async def test_disabled_section_rejected(monkeypatch):
    monkeypatch.setattr(ai_service, "chat_completion", _fake_ok)
    async with async_session_maker() as session:
        user = await _make_user(session)
        section = await _make_section(session, enabled=False)
        with pytest.raises(ai_service.AiSectionError):
            await AiConversationService.process_message(session, user, section, "hi")


# ══════════════ استخراج الملفات ══════════════


def test_extract_named_files():
    text = (
        "هذه الأداة:\n"
        "```python\n"
        "file: main.py\n"
        "print('hello')\n"
        "```\n"
        "```python\n"
        "file: utils.py\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "```\n"
        "شغّل: python main.py"
    )
    explanation, files = extract_code_artifacts(text)
    assert files[0][0] == "main.py"
    assert files[1][0] == "utils.py"
    assert "file:" not in files[0][1]
    assert "print('hello')" in files[0][1]
    assert "python main.py" in explanation
    assert "```" not in explanation


def test_extract_header_name():
    text = "```javascript index.js\nconsole.log(1);\n```\n"
    explanation, files = extract_code_artifacts(text)
    assert files == [("index.js", "console.log(1);\n")]
    assert explanation == ""


def test_extract_unnamed_single_block():
    text = "تفضل الكود:\n```python\nprint(1)\n```\n"
    explanation, files = extract_code_artifacts(text)
    assert len(files) == 1
    assert files[0][0] == "main.py"
    assert files[0][1] == "print(1)\n"
    assert "تفضل الكود" in explanation


def test_extract_unnamed_multiple_blocks_guess_extensions():
    text = (
        "```html\n<div>hi</div>\n```\n"
        "```python\nx = 1\n```\n"
    )
    _explanation, files = extract_code_artifacts(text)
    assert files[0][0] == "main.html"
    assert files[1][0] == "file_2.py"


def test_extract_no_code():
    explanation, files = extract_code_artifacts("نص عادي بدون كود")
    assert files == []
    assert explanation == "نص عادي بدون كود"


def test_files_to_zip():
    files = [("a.py", b"print(1)"), ("b.py", b"x=2")]
    data = files_to_zip(files)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["a.py", "b.py"]
        assert zf.read("a.py") == b"print(1)"


# ══════════════ الإحصاءات ══════════════


async def test_stats_aggregate(monkeypatch):
    monkeypatch.setattr(ai_service, "chat_completion", _fake_ok)
    async with async_session_maker() as session:
        user = await _make_user(session, balance="100.00")
        section = await _make_section(session)
        await AiConversationService.process_message(session, user, section, "أ")
        await AiConversationService.process_message(session, user, section, "ب")
        stats = await AiConversationService.stats(session)
    assert stats["total_messages"] == 2
    assert stats["total_cost"] == Decimal("0.0200")
    assert stats["total_revenue"] == Decimal("0.0800")
    assert stats["total_profit"] == Decimal("0.0600")
    assert stats["sections"][0]["name_ar"] == "دردشة"
