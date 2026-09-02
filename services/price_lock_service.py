"""Short-lived immutable price quotes between catalog display and checkout."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic


@dataclass(frozen=True)
class PriceQuote:
    token: str
    service_code: str
    country_code: str
    provider: str
    cost_usd: Decimal
    sell_price_usd: Decimal
    expires_at: float


class PriceLockService:
    _quotes: dict[str, PriceQuote] = {}
    _lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        service_code: str,
        country_code: str,
        provider: str,
        cost_usd: Decimal,
        sell_price_usd: Decimal,
        ttl: int = 45,
    ) -> PriceQuote:
        token = secrets.token_urlsafe(12)
        quote = PriceQuote(
            token=token,
            service_code=service_code,
            country_code=country_code,
            provider=provider,
            cost_usd=cost_usd,
            sell_price_usd=sell_price_usd,
            expires_at=monotonic() + max(5, ttl),
        )
        async with cls._lock:
            cls._quotes[token] = quote
        return quote

    @classmethod
    async def get(
        cls,
        token: str | None,
        service_code: str,
        country_code: str,
    ) -> PriceQuote | None:
        if not token:
            return None
        async with cls._lock:
            quote = cls._quotes.get(token)
            if quote is None or quote.expires_at <= monotonic():
                cls._quotes.pop(token, None)
                return None
            if quote.service_code != service_code or quote.country_code != country_code:
                return None
            return quote

    @classmethod
    async def consume(cls, token: str | None) -> None:
        if token:
            async with cls._lock:
                cls._quotes.pop(token, None)

    @classmethod
    async def cleanup(cls) -> int:
        now = monotonic()
        async with cls._lock:
            expired = [key for key, quote in cls._quotes.items() if quote.expires_at <= now]
            for key in expired:
                cls._quotes.pop(key, None)
            return len(expired)
