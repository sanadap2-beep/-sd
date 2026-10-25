"""Regression tests for the product-review callback handlers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.types import InlineKeyboardMarkup

from handlers.reviews import review_skip_comment


class DummyMessage:
    def __init__(self) -> None:
        self.edits: list[tuple[str | None, object]] = []

    async def edit_text(self, text: str | None = None, reply_markup=None, **kwargs) -> None:
        self.edits.append((text, reply_markup))


class DummyCallback:
    def __init__(self) -> None:
        self.message = DummyMessage()
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs) -> None:
        self.answers.append((text, show_alert))


class DummyState:
    def __init__(self) -> None:
        self.cleared = False

    async def get_data(self) -> dict[str, int]:
        return {"review_order_id": 42, "review_rating": 5}

    async def clear(self) -> None:
        self.cleared = True


@pytest.mark.asyncio
async def test_skip_comment_renders_navigation_keyboard(monkeypatch) -> None:
    created = {}

    async def fake_create(session, user_id, order_id, rating, comment):
        created.update(
            user_id=user_id,
            order_id=order_id,
            rating=rating,
            comment=comment,
        )
        return SimpleNamespace(rating=rating)

    monkeypatch.setattr("handlers.reviews.ReviewService.create", fake_create)
    callback = DummyCallback()
    state = DummyState()

    await review_skip_comment(
        callback,
        state,
        session=object(),
        db_user=SimpleNamespace(id=99),
    )

    assert created == {"user_id": 99, "order_id": 42, "rating": 5, "comment": None}
    assert state.cleared is True
    assert callback.answers == [("✅ تم حفظ تقييمك.", True)]
    assert callback.message.edits[0][0] == "✅ تم حفظ تقييمك: ⭐⭐⭐⭐⭐"

    keyboard = callback.message.edits[0][1]
    assert isinstance(keyboard, InlineKeyboardMarkup)
    assert [button.callback_data for row in keyboard.inline_keyboard for button in row] == [
        "my_uni_orders:0",
        "back_to_main",
    ]
