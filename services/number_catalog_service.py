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
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_UP
from time import monotonic

from providers.countries import get_active_countries
from services.pricing_service import PricingService

logger = logging.getLogger(__name__)

# مدة كاش لوحة الأسعار في الذاكرة (60 ثانية لتحديث الأسعار باستمرار)
BOARD_TTL_SECONDS = 60
BOARD_CONCURRENCY = 10

# دول نادرة/مرتفعة الطلب يتذبذب مخزونها عادةً، وتستحق الظهور في قناة
# «التوفر المتقطع» عند رجوعها للمخزون بدل الاكتفاء بأرخص الدول الثابتة.
# تُكتب بصيغ متعددة لأن بعض السجلات تستخدم ISO-2 (ae) وبعضها أسماء مزودين
# مثل uae/united_arab_emirates.
DEFAULT_INTERMITTENT_COUNTRY_CODES = frozenset(
    {
        "ae",
        "uae",
        "united_arab_emirates",
        "sa",
        "ksa",
        "saudi_arabia",
        "us",
        "usa",
        "united_states",
        "gb",
        "uk",
        "united_kingdom",
        "qa",
        "qatar",
        "kw",
        "kuwait",
        "bh",
        "bahrain",
        "om",
        "oman",
        "jo",
        "jordan",
        "eg",
        "egypt",
        "de",
        "germany",
        "fr",
        "france",
        "ca",
        "canada",
        "au",
        "australia",
    }
)


def normalize_country_code(code: str | None) -> str:
    """Normalize country identifiers used by DB rows/providers/settings."""
    value = str(code or "").strip().lower().replace("-", "_").replace(" ", "_")
    return re.sub(r"[^a-z0-9_]", "", value)


def is_intermittent_country_code(code: str | None) -> bool:
    """Best-effort static classification for rare/intermittent countries."""
    return normalize_country_code(code) in DEFAULT_INTERMITTENT_COUNTRY_CODES


@dataclass
class BoardEntry:
    """بيانات الدولة مع سعر التكلفة وسعر البيع النهائي."""
    code: str
    name_ar: str
    flag: str
    cost_usd: Decimal
    sell_usd: Decimal
    is_intermittent: bool = False
    # الرقم الداخلي — هو ما يُرسل بأزرار تيليجرام (callback_data ≤ 64B)
    cid: int = 0


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


async def _fetch_cost(
    manager,
    service,
    country,
    use_provider_cache: bool = True,
    only_provider=None,
) -> tuple[object, Decimal] | None:
    """جلب أرخص تكلفة متوفرة لدولة معينة مع التأكد من وجود أرقام."""
    try:
        try:
            prices = await manager.get_cheapest_price(
                service,
                country,
                session=None,
                use_cache=use_provider_cache,
                only_provider=only_provider,
            )
        except TypeError as exc:
            if "only_provider" not in str(exc) and "use_cache" not in str(exc):
                raise
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


async def build_board(session, service, manager=None, use_cache: bool = True, server: object | None = None, only_provider=None) -> list[BoardEntry]:
    """
    بناء لوحة الأسعار لخدمة الأرقام:
    - جلب أسعار التكلفة الحية.
    - تطبيق نسبة ربح الأدمن.
    - الترتيب من الأرخص إلى الأغلى.

    ``use_cache=False`` يجبر الجلب المباشر ويتجاوز كاش اللوحة وكاش أسعار
    المزودين (تستخدمه قناة التوفر الحية حتى ترصد عودة المخزون فوراً).

    ``server``/``only_provider``: عند تخصيص لوحة لسيرفر (مزود) معين، تُجلب
    أسعار ذلك المزود فقط ولا يظهر المزودون الآخرون.
    """
    from providers.manager import provider_manager as default_manager

    manager = manager or default_manager
    if only_provider is None and server is not None:
        from database.models import ProviderName as _PN

        candidate = getattr(server, "provider", None)
        if candidate:
            try:
                only_provider = _PN(candidate)
            except ValueError:
                only_provider = None

    # 1. فحص الكاش المؤقت
    if use_cache:
        cache_key = service.code if only_provider is None else f"{service.code}:{only_provider.value}"
        async with _CACHE_LOCK:
            cached = _BOARD_CACHE.get(cache_key)
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
            fetched = await _fetch_cost(
                manager,
                service,
                country,
                use_provider_cache=use_cache,
                only_provider=only_provider,
            )
            if fetched is not None:
                costs[country.code] = fetched

    await asyncio.gather(*(_worker(c) for c in countries))

    # 4. حساب أسعار البيع بتطبيق هامش ربح الأدمن
    from services.country_localization_service import display_flag, display_name

    entries: list[BoardEntry] = []
    for country in countries:
        fetched = costs.get(country.code)
        if fetched is None:
            continue
        provider, cost = fetched
        server_margin = None
        if server is not None:
            server_margin = getattr(server, "margin_percent", None)
        if server_margin is not None:
            sell = (cost * (Decimal("100") + Decimal(str(server_margin))) / Decimal("100")).quantize(
                Decimal("0.0001"), rounding=ROUND_UP
            )
        else:
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
                name_ar=display_name(country),
                flag=display_flag(country),
                cost_usd=cost,
                sell_usd=sell,
                is_intermittent=is_intermittent_country_code(country.code),
                cid=country.id,
            )
        )

    # 5. الترتيب تصاعدياً من الأرخص إلى الأغلى
    entries.sort(key=lambda e: e.sell_usd)

    # 6. حفظ النتيجة في الكاش
    async with _CACHE_LOCK:
        cache_key = service.code if only_provider is None else f"{service.code}:{only_provider.value}"
        _BOARD_CACHE[cache_key] = (monotonic() + BOARD_TTL_SECONDS, list(entries))

    return entries