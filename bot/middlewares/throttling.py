"""Global Telegram update throttling, independent from business rate limits."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from config import settings


class GlobalThrottlingMiddleware(BaseMiddleware):
    """Stop update floods before they reach handlers or external APIs."""

    def __init__(self) -> None:
        self._events: dict[tuple[int, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        kind = "callback" if isinstance(event, CallbackQuery) else "message"
        limit = (
            settings.CALLBACK_RATE_LIMIT_PER_MINUTE
            if kind == "callback"
            else settings.GLOBAL_RATE_LIMIT_PER_MINUTE
        )
        if limit <= 0:
            return await handler(event, data)
        now = time.monotonic()
        key = (user.id, kind)
        async with self._lock:
            events = self._events[key]
            while events and now - events[0] >= 60:
                events.popleft()
            if len(events) >= limit:
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        "⏳ طلبات كثيرة جداً، حاول بعد دقيقة.",
                        show_alert=True,
                    )
                elif isinstance(event, Message):
                    await event.answer("⏳ طلبات كثيرة جداً، حاول بعد دقيقة.")
                return
            events.append(now)
        return await handler(event, data)

    def cleanup(self) -> int:
        now = time.monotonic()
        removed = 0
        for key in list(self._events):
            events = self._events[key]
            while events and now - events[0] >= 60:
                events.popleft()
            if not events:
                self._events.pop(key, None)
                removed += 1
        return removed
