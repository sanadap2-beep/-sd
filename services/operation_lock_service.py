"""Short-lived locks for idempotent user operations."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from time import monotonic


class OperationBusyError(Exception):
    pass


class OperationLockService:
    _locks: dict[str, asyncio.Lock] = {}
    _guard = asyncio.Lock()

    @classmethod
    async def _get(cls, key: str) -> asyncio.Lock:
        async with cls._guard:
            return cls._locks.setdefault(key, asyncio.Lock())

    @classmethod
    @asynccontextmanager
    async def acquire(cls, key: str, timeout: float = 3):
        lock = await cls._get(key)
        started = monotonic()
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
        except TimeoutError as exc:
            raise OperationBusyError("العملية قيد التنفيذ، انتظر لحظة.") from exc
        try:
            yield
        finally:
            if lock.locked():
                lock.release()
            if monotonic() - started > 10:
                async with cls._guard:
                    if not lock.locked():
                        cls._locks.pop(key, None)

    @classmethod
    async def cleanup(cls) -> int:
        async with cls._guard:
            keys = [key for key, lock in cls._locks.items() if not lock.locked()]
            for key in keys:
                cls._locks.pop(key, None)
            return len(keys)
