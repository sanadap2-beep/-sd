"""اختبارات معالج حماية الإحالة (send_human_check + rg:ans).

يغطي أن /start لا يسقط بسبب دالة send_human_check ناقصة (كانت مرجعاً
بلا تنفيذ) وأن الإجابة الصحيحة تُفعّل الحساب وتُكمل التحقق.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from database.engine import async_session_maker
from database.models import User
from handlers.referral_guard import human_check_answer, send_human_check
from states.states import ReferralGuardStates


class DummyMessage:
    def __init__(self):
        self.answers = []
        self.edits = []

    async def answer(self, text=None, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return SimpleNamespace(chat=SimpleNamespace(id=1), message_id=1)

    async def edit_text(self, text=None, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return None


class DummyCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = DummyMessage()
        self.answers = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert))


def _make_ctx() -> tuple[MemoryStorage, FSMContext]:
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=6707747395, user_id=6707747395)
    return storage, FSMContext(storage=storage, key=key)


@pytest.mark.asyncio
async def test_send_human_check_and_success_flow():
    async with async_session_maker() as session:
        user = User(
            telegram_id=6707747395,
            username="joiner",
            full_name="Joiner",
            language_code="ar",
            is_activated=False,
            referral_check_pending=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        storage, ctx = _make_ctx()
        msg = DummyMessage()
        await send_human_check(msg, ctx, user)

        # أُرسلت رسالة بالاختبار مع زر التحقق.
        assert len(msg.answers) == 1
        assert msg.answers[0][1] is not None

        data = await ctx.get_data()
        correct = data["human_check_answer"]

        cb = DummyCallback(f"rg:ans:{correct}")
        await human_check_answer(cb, ctx, session, user, bot=None)

        await session.refresh(user)
        assert user.referral_check_pending is False
        assert user.is_activated is True


@pytest.mark.asyncio
async def test_wrong_answer_records_failure():
    async with async_session_maker() as session:
        user = User(
            telegram_id=6707747396,
            username="wrong",
            full_name="Wrong",
            language_code="ar",
            is_activated=False,
            referral_check_pending=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        storage, ctx = _make_ctx()
        msg = DummyMessage()
        await send_human_check(msg, ctx, user)

        data = await ctx.get_data()
        correct = int(data["human_check_answer"])
        wrong = (correct + 1) % 10

        cb = DummyCallback(f"rg:ans:{wrong}")
        await human_check_answer(cb, ctx, session, user, bot=None)

        await session.refresh(user)
        assert user.referral_check_pending is True
        assert user.referral_check_fails == 1
