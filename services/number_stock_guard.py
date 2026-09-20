"""حارس المخزون الوهمي لمزودي الأرقام.

المشكلة:
- بعض المزودين (grizzly تحديداً) يعرضون سعراً وcount>0 عبر getPrices
  لكن الشراء الفعلي يرجع NO_NUMBERS — مخزون وهمي.
- النتيجة: الدولة تظهر في اللوحة بسعر مغرٍ، والمستخدم يشتري فيفشل
  ويسترجع رصيده — تجربة مستفزة.

الحل الاحترافي:
1) عند كل فشل شراء بسبب نفاد المخزون (NO_NUMBERS وما شابه)، نحظر
   (provider, service, country) مؤقتاً — تُستبعد من get_cheapest_price
   وبالتالي من build_board ولوحات السيرفرات.
2) الحظر مؤقت (TTL) فينتهي تلقائياً: إذا عاد المخزون للمزود ترجع
   الدولة للوحة شغالة تلقائياً بدون تدخل أدمن — كما طلب المستخدم.
3) الفشل المتكرر يمدد الحظر تدريجياً، والنجاح يمسحه فوراً.
4) يعمل على كل المزودين (grizzly/herosms/fivesim/...) بنفس الآلية.

التخزين في الذاكرة (خفيف وسريع) + إبطال كاش الأسعار واللوحة عند
الحظر حتى تختفي الدولة فوراً من العرض التالي.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# حظر أولي 10 دقائق، يتضاعف مع التكرار حتى سقف ساعة.
BASE_BLOCK_SECONDS = 10 * 60
MAX_BLOCK_SECONDS = 60 * 60

_lock = asyncio.Lock()
# (provider_value, service_code, country_code) -> (expires_at, failures)
_BLOCKS: dict[tuple[str, str, str], tuple[float, int]] = {}


def _key(provider: str, service_code: str, country_code: str) -> tuple[str, str, str]:
    return (
        str(provider or "").strip().lower(),
        str(service_code or "").strip().lower(),
        str(country_code or "").strip().lower(),
    )


def _is_empty_stock_error(message: str) -> bool:
    text = str(message or "")
    markers = (
        "NO_NUMBERS",
        "لا توجد أرقام",
        "مخزون وهمي",
        "OUT_OF_STOCK",
        "NO_STOCK",
        "not available",
        "no numbers",
    )
    lowered = text.lower()
    return any(m.lower() in lowered for m in markers)


async def report_empty_stock(
    provider,
    service_code: str,
    country_code: str,
    reason: str = "",
) -> float:
    """يحظر دولة/خدمة/مزود مؤقتاً بسبب مخزون وهمي. يرجع مدة الحظر بالثواني."""
    provider_value = getattr(provider, "value", provider)
    key = _key(str(provider_value), service_code, country_code)
    async with _lock:
        _, failures = _BLOCKS.get(key, (0.0, 0))
        failures += 1
        ttl = min(BASE_BLOCK_SECONDS * (2 ** (failures - 1)), MAX_BLOCK_SECONDS)
        _BLOCKS[key] = (time.monotonic() + ttl, failures)
    logger.warning(
        "حظر مخزون وهمي %s/%s/%s لمدة %ss (تكرار=%s) — %s",
        provider_value, service_code, country_code, int(ttl), failures, str(reason)[:150],
    )
    # إبطال الكاش حتى تختفي الدولة من اللوحة فوراً
    try:
        from services.price_cache_service import PriceCacheService

        await PriceCacheService.invalidate(f"number-price:{service_code}:{country_code}")
    except Exception:
        pass
    try:
        from services.number_catalog_service import invalidate_board

        invalidate_board(service_code)
        invalidate_board(f"{service_code}:{provider_value}")
    except Exception:
        pass
    return ttl


async def record_success(provider, service_code: str, country_code: str) -> None:
    """نجاح شراء فعلي → امسح الحظر فوراً (المخزون حقيقي)."""
    provider_value = getattr(provider, "value", provider)
    key = _key(str(provider_value), service_code, country_code)
    async with _lock:
        _BLOCKS.pop(key, None)


async def is_blocked(provider, service_code: str, country_code: str) -> bool:
    provider_value = getattr(provider, "value", provider)
    key = _key(str(provider_value), service_code, country_code)
    async with _lock:
        item = _BLOCKS.get(key)
        if not item:
            return False
        expires_at, _ = item
        if expires_at <= time.monotonic():
            _BLOCKS.pop(key, None)
            return False
        return True


async def filter_prices(
    prices: dict,
    service_code: str,
    country_code: str,
) -> dict:
    """يزيل المزودين المحظورين مؤقتاً من قاموس الأسعار."""
    if not prices:
        return prices
    out = {}
    for provider, price in prices.items():
        if await is_blocked(provider, service_code, country_code):
            continue
        out[provider] = price
    if len(out) != len(prices):
        logger.info(
            "فلترة مخزون وهمي %s/%s: %s → %s مزود",
            service_code, country_code, len(prices), len(out),
        )
    return out


async def active_blocks() -> list[dict]:
    """لقطة للتشخيص ولوحة الأدمن."""
    now = time.monotonic()
    async with _lock:
        expired = [k for k, (exp, _) in _BLOCKS.items() if exp <= now]
        for k in expired:
            _BLOCKS.pop(k, None)
        return [
            {
                "provider": k[0],
                "service": k[1],
                "country": k[2],
                "expires_in_seconds": max(0, int(exp - now)),
                "failures": failures,
            }
            for k, (exp, failures) in _BLOCKS.items()
        ]


async def clear_all() -> int:
    async with _lock:
        n = len(_BLOCKS)
        _BLOCKS.clear()
        return n
