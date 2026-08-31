"""Dedicated SMS receiver abstraction with polling/webhook-ready interface."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from providers.base import OrderStatusResult
from providers.manager import provider_manager


class SMSReceiverError(Exception):
    pass


class SMSReceiverService:
    @staticmethod
    async def check(provider, external_order_id: str) -> OrderStatusResult:
        """Fetch one status; the only place order monitoring talks to providers."""
        try:
            return await provider_manager.check_status(provider, external_order_id)
        except Exception as exc:
            raise SMSReceiverError(str(exc)) from exc

    @staticmethod
    async def wait_for_code(
        provider,
        external_order_id: str,
        timeout_seconds: int = 300,
        interval_seconds: int = 15,
        on_update: Callable[[OrderStatusResult], Awaitable[None]] | None = None,
    ) -> OrderStatusResult:
        """Wait for a code while allowing a future webhook adapter to replace polling."""
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_result = OrderStatusResult("pending", None, None, {})
        while asyncio.get_running_loop().time() < deadline:
            last_result = await SMSReceiverService.check(provider, external_order_id)
            if on_update:
                await on_update(last_result)
            if last_result.sms_code or last_result.status in {"cancelled", "expired", "failed"}:
                return last_result
            await asyncio.sleep(max(1, interval_seconds))
        return last_result
