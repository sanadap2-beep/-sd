"""
لوحة أسعار دول خدمة الأرقام.

المهمة:
عندما يفتح المستخدم خدمة (واتساب/تيليجرام) نبني «لوحة أسعار» لكل الدول
المفعّلة دفعة واحدة:
1) نجلب أرخص تكلفة لكل دولة من المزودين (بتوازٍ محدود ومع كاش).
2) نطبّق نسبة الربح لنتحصل على سعر البيع.
3) نرتب الدول من الأرخص إلى الأغلى، ونستبعد أي دولة بلا سعر/مخزون.

بهذا يرى المستخدم السعر مباشرة بجانب كل دولة، ولا يضغط على دولة
بلا أرقام متاحة.
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

# مدة الاحتفاظ بلوحة الأسعار في الذاكرة (ثانية).
# أطول من كاش سعر المزود الواحد حتى لا تُقصف واجهة المزود بطلبات.
BOARD_TTL_SECONDS = 120
# عدد طلبات الأسعار المتوازية.
BOARD_CONCURRENCY = 8


@dataclass
class BoardEntry:
    """دولة واحدة ضمن لوحة الأسعار."""

    code: str
    name_ar: str
    flag: str
    cost_usd: Decimal
    sell_usd: Decimal


# كاش اللوحات في ذاكرة العملية: {service_code: (expires_at, entries)}
_BOARD_CACHE: dict[str, tuple[float, list[BoardEntry]]] = {}
_CACHE_LOCK = asyncio.Lock()


def format_price(price: Decimal) -> str:
    """تنسيق سعر للعرض في نص الزر.

    دقة عالية للأسعار الصغيرة (3 منازل تحت 0.1$) وأقل للكبيرة (منزلتان)،
    مع إزالة الأصفار الزائدة والحفاظ على منزلتين على الأقل.
    0.03$ / 0.075$ / 1.50$
    """
    if price < Decimal("0.1"):
        quant = Decimal("0.001")
    else:
        quant = Decimal("0.01")
    value = price.quantize(quant, rounding=ROUND_UP)
    text = f"{value.normalize():f}"
    if "." not in text:
        return f"{text}.00"
    fraction = text.split(".", 1)[1]
    if len(fraction) < 2:
        return f"{text}0"
    return text


def invalidate_board(service_code: str | None = None) -> None:
    """يمسح كاش اللوحة (كلها أو خدمة معينة)."""
    if service_code is None:
        _BOARD_CACHE.clear()
    else:
        _BOARD_CACHE.pop(service_code, None)


async def _fetch_cost(manager, service, country, manual_cost=None) -> tuple[object, Decimal] | None:
    """أرخص (مزود، تكلفة) لدولة واحدة، أو None إن لم تتوفر أسعار/مخزون.

    إن وُجدت تكلفة يدوية للأدمن تُستخدم مباشرة دون أي طلب شبكي —
    هكذا تعمل اللوحة حتى لو كانت أسعار المزود الحية معطلة.

    ملاحظة: نمرر session=None عن قصد في المسار الحي — فحص حالة المزود
    داخل get_cheapest_price يستخدم الجلسة، وجلسة AsyncSession واحدة
    غير آمنة للاستخدام المتوازي. المزودون المعطّلون يفشل طلبهم ببساطة.
    """
    if manual_cost is not None and hasattr(manager, "manual_prices_for"):
        prices = manager.manual_prices_for(service, country, manual_cost)
        if prices:
            provider = min(prices, key=prices.get)
            return provider, prices[provider]
        return None
    try:
        prices = await manager.get_cheapest_price(service, country, session=None)
    except Exception:  # noqa: BLE001 - دولة واحدة لا تُسقط اللوحة
        return None
    if not prices:
        return None
    provider = min(prices, key=prices.get)
    return provider, prices[provider]


async def build_board(session, service, manager=None) -> list[BoardEntry]:
    """يبني لوحة الأسعار لخدمة أرقام: مرتبة من الأرخص للأغلى.

    Args:
        session: جلسة قاعدة البيانات (تستخدم فقط لقراءة الدول والهوامش،
                 بشكل تسلسلي آمن).
        service: كائن NumberService.
        manager: مدير المزودين (قابل للحقن للاختبارات).

    Returns:
        قائمة BoardEntry مرتبة تصاعدياً بسعر البيع. الدول بلا أرقام
        متاحة تُستبعد تلقائياً.
    """
    from providers.manager import provider_manager as default_manager

    manager = manager or default_manager

    # ── كاش الذاكرة ──
    async with _CACHE_LOCK:
        cached = _BOARD_CACHE.get(service.code)
        if cached and cached[0] > monotonic():
            return list(cached[1])

    countries = await get_active_countries(session)
    if not countries:
        return []

    # ── التكاليف اليدوية للأدمن (تسلسلياً وبأمان عبر الجلسة) ──
    # تُقرأ قبل الجولات المتوازية لأن قراءة KV سريعة، وتُمرر للعمال
    # فتعرض الدول المُسعّرة يدوياً حتى بلا اتصال بالمزود.
    manual_costs: dict[str, Decimal] = {}
    for country in countries:
        try:
            cost = await PricingService.get_manual_cost(session, service.code, country.code)
        except Exception:  # noqa: BLE001 - خلل القراءة يعني لا تسعير يدوياً
            cost = None
        if cost is not None:
            manual_costs[country.code] = cost

    # ── جلب التكاليف بالتوازي (لا جلسة قاعدة بيانات هنا) ──
    semaphore = asyncio.Semaphore(max(1, BOARD_CONCURRENCY))
    costs: dict[str, tuple[object, Decimal]] = {}

    async def _worker(country):
        async with semaphore:
            fetched = await _fetch_cost(
                manager, service, country, manual_cost=manual_costs.get(country.code)
            )
        if fetched is not None:
            costs[country.code] = fetched

    await asyncio.gather(*(_worker(c) for c in countries))

    # ── حساب أسعار البيع تسلسلياً (يستخدم الجلسة) ──
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
        except Exception:  # noqa: BLE001 - هامش فاشل لا يحجب الدولة
            sell = cost
        entries.append(
            BoardEntry(
                code=country.code,
                name_ar=country.name_ar,
                flag=country.flag or "🌍",
                cost_usd=cost,
                sell_usd=sell,
            )
        )

    entries.sort(key=lambda e: e.sell_usd)

    async with _CACHE_LOCK:
        _BOARD_CACHE[service.code] = (monotonic() + BOARD_TTL_SECONDS, list(entries))
    return entries
