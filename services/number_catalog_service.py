"""
لوحة أسعار دول خدمة الأرقام:
- تجلب أسعار التكلفة من المزودين بالتوازي.
- تطبق نسبة ربح الأدمن المحددة من لوحة التحكم.
- ترتب الدول تصاعدياً من الأرخص إلى الأغلى 🟢.
- تستبعد تلقائياً أي دولة لا يتوفر بها مخزون أو أرقام حالياً.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_UP
from time import monotonic

from providers.countries import get_active_countries
from services.pricing_service import PricingService

logger = logging.getLogger(__name__)

# مدة كاش لوحة الأسعار في الذاكرة (60 ثانية لتحديث الأسعار باستمرار)
BOARD_TTL_SECONDS = 60
BOARD_CONCURRENCY = 10


@dataclass
class BoardEntry:
    """بيانات الدولة مع سعر التكلفة وسعر البيع النهائي."""
    code: str
    name_ar: str
    flag: str
    cost_usd: Decimal
    sell_usd: Decimal


_BOARD_CACHE: dict[str, tuple[float, list[BoardEntry]]] = {}
_CACHE_LOCK = asyncio.Lock()


def format_price(price: Decimal) -> str:
    """تنسيق السعر للعرض على الأزرار."""
    p = Decimal(str(price))
    if p < Decimal("0.01"):
        return f"{p:.3f}"
    return f"{p:.2f}"


def invalidate_board(service_code: str | None = None) -> None:
    """تفريغ كاش الأسعار لإعادة الجلب الفوري."""
    if service_code is None:
        _BOARD_CACHE.clear()
    else:
        _BOARD_CACHE.pop(service_code, None)


async def _fetch_cost(manager, service, country) -> tuple[object, Decimal] | None:
    """جلب أرخص تكلفة متوفرة لدولة معينة مع التأكد من وجود أرقام."""
    try:
        prices = await manager.get_cheapest_price(service, country, session=None)
    except Exception:
        return None

    if not prices:
        return None

    provider = min(prices, key=prices.get)
    cost = prices[provider]
    if cost is None or cost <= 0:
        return None

    return provider, cost


async def build_board(session, service, manager=None) -> list[BoardEntry]:
    """
    بناء لوحة الأسعار لخدمة الأرقام:
    - جلب أسعار التكلفة الحية.
    - تطبيق نسبة ربح الأدمن.
    - الترتيب من الأرخص إلى الأغلى.
    """
    from providers.manager import provider_manager as default_manager

    manager = manager or default_manager

    # 1. فحص الكاش المؤقت
    async with _CACHE_LOCK:
        cached = _BOARD_CACHE.get(service.code)
        if cached and cached[0] > monotonic():
            return list(cached[1])

    # 2. جلب الدول المفعلة
    countries = await get_active_countries(session)
    if not countries:
        return []

    # 3. جلب التكاليف الحية بالتوازي
    semaphore = asyncio.Semaphore(BOARD_CONCURRENCY)
    costs: dict[str, tuple[object, Decimal]] = {}

    async def _worker(country):
        async with semaphore:
            fetched = await _fetch_cost(manager, service, country)
            if fetched is not None:
                costs[country.code] = fetched

    await asyncio.gather(*(_worker(c) for c in countries))

    # 4. حساب أسعار البيع بتطبيق هامش ربح الأدمن
    entries: list[BoardEntry] = []
    for country in countries:
        fetched = costs.get(country.code)
        if fetched is None:
            continue
        provider, cost = fetched
        try:
            sell = await PricingService.calculate_sell_price(
                session,
                service.code,
                country.code,
                provider,
                cost,
            )
        except Exception:
            # هامش افتراضي 50% في حال عدم تعيين نسبة خاصة
            sell = (cost * Decimal("1.50")).quantize(Decimal("0.0001"), rounding=ROUND_UP)

        entries.append(
            BoardEntry(
                code=country.code,
                name_ar=country.name_ar,
                flag=country.flag or "🌍",
                cost_usd=cost,
                sell_usd=sell,
            )
        )

    # 5. الترتيب تصاعدياً من الأرخص إلى الأغلى
    entries.sort(key=lambda e: e.sell_usd)

    # 6. حفظ النتيجة في الكاش
    async with _CACHE_LOCK:
        _BOARD_CACHE[service.code] = (monotonic() + BOARD_TTL_SECONDS, list(entries))

    return entries