"""
غرفة العمليات التنبؤية + الإدارة الذاتية للكتالوج + أسعار الصرف الحية.

ثلاث مشاكل تشغيلية يحلها هذا الملف:

1) المزود يُكتشف تعطله **بعد** فشله. `provider_manager` يجرّب ثم يقع ثم
   ينتقل للتالي، والأدمن يعرف من شكاوى المستخدمين. هنا تُسجَّل مقاييس
   كل مزود وتُكتشف التدهور قبل الفشل الكامل.

2) الكتالوج يدار يدوياً: الأدمن يزامن ويضبط الهوامش ويعطّل المنتجات
   المعطوبة بنفسه. هنا يتولى النظام ذلك.

3) أسعار الصرف يدوية (`rate_*_to_usd` يدخلها الأدمن)، فأسعارك قديمة
   دائماً في سوق يتحرك كل دقيقة.

كل شيء قابل للإيقاف من مركز الإضافات، وكل قرار آلي يُسجَّل حتى يمكن
تدقيقه أو التراجع عنه.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from statistics import mean, pstdev

import aiohttp
from sqlalchemy import select

from database.models import (
    ApiProvider,
    Product,
    ProductStatus,
    ProviderService,
    ProviderServiceStatus,
    ProviderStatus,
    UnifiedOrder,
    UnifiedOrderStatus,
)
from services.feature_service import FeatureService
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


class SelfHealingService:
    """يكشف تدهور المزود قبل فشله الكامل ويخفض وزنه."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("self_healing_ops")

    @staticmethod
    async def record(session, provider_key: str, ok: bool, latency_ms: float) -> None:
        """يسجل نتيجة محاولة واحدة في نافذة زمنية منزلقة."""
        if not await SelfHealingService.enabled():
            return
        key = f"ops:{provider_key}:{datetime.utcnow():%Y%m%d%H%M}"
        current = await SettingsService.get(key, "0,0,0")
        try:
            successes, failures, total_latency = current.split(",")
            successes, failures = int(successes), int(failures)
            total_latency = float(total_latency)
        except (ValueError, AttributeError):
            successes, failures, total_latency = 0, 0, 0.0
        if ok:
            successes += 1
        else:
            failures += 1
        total_latency += float(latency_ms or 0)
        await SettingsService.set(
            session, key, f"{successes},{failures},{total_latency:.1f}"
        )

    @staticmethod
    async def health(session, provider_key: str, minutes: int = 30) -> dict:
        """يجمع نافذة زمنية ويحسب نسبة النجاح ومتوسط الزمن."""
        now = datetime.utcnow()
        successes = failures = 0
        latencies: list[float] = []
        for offset in range(minutes):
            bucket = now - timedelta(minutes=offset)
            raw = await SettingsService.get(f"ops:{provider_key}:{bucket:%Y%m%d%H%M}", None)
            if not raw:
                continue
            try:
                ok, bad, latency = raw.split(",")
                successes += int(ok)
                failures += int(bad)
                if int(ok) > 0:
                    latencies.append(float(latency) / int(ok))
            except (ValueError, AttributeError):
                continue

        total = successes + failures
        success_rate = round(successes / total * 100, 1) if total else 100.0
        return {
            "provider": provider_key,
            "attempts": total,
            "success_rate": success_rate,
            "avg_latency_ms": round(mean(latencies), 1) if latencies else None,
            "degraded": success_rate < 80 and total >= 5,
        }

    @staticmethod
    async def weight(session, provider_key: str) -> int:
        """
        وزن بين 0 و100 يُستخدم لترتيب المزودين.
        مزود متدهور لا يُستبعد كلياً (قد يتعافى) لكن ينزل ترتيبه.
        """
        if not await SelfHealingService.enabled():
            return 100
        data = await SelfHealingService.health(session, provider_key)
        if data["attempts"] < 5:
            return 100
        floor = await FeatureService.config_int("self_healing_ops", "min_weight", 10)
        return max(floor, int(data["success_rate"]))

    @staticmethod
    async def anomalies(session) -> list[dict]:
        """كل المزودين المتدهورين — تُرسل للأدمن كتنبيه قابل للتنفيذ."""
        if not await SelfHealingService.enabled():
            return []
        result = await session.execute(select(ProviderStatus))
        out = []
        for status in result.scalars().all():
            key = getattr(status.provider, "value", str(status.provider))
            data = await SelfHealingService.health(session, key)
            if data["degraded"]:
                out.append(data)
        return out


class CatalogAutopilotService:
    """يدير الكتالوج: هوامش، تعطيل المعطوب، وتنبيه المراجحة."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("catalog_autopilot")

    @staticmethod
    async def target_margin() -> Decimal:
        return Decimal(
            str(await FeatureService.config_decimal(
                "catalog_autopilot", "target_margin_percent", 50.0
            ))
        )

    @staticmethod
    async def disable_deleted_services(session) -> list[int]:
        """
        يعطّل المنتجات التي حُذفت خدمتها من المزود.
        حالة DELETED_FROM_PROVIDER موجودة أصلاً لكن المعالجة كانت يدوية،
        فبقيت منتجات معروضة لا يمكن تنفيذها.
        """
        if not await CatalogAutopilotService.enabled():
            return []
        if not await FeatureService.config_bool(
            "catalog_autopilot", "auto_disable_deleted", True
        ):
            return []

        result = await session.execute(
            select(Product).where(Product.status == ProductStatus.ACTIVE)
        )
        disabled: list[int] = []
        for product in result.scalars().all():
            if not product.provider_service_ref_id:
                continue
            service = await session.get(ProviderService, product.provider_service_ref_id)
            if service is None:
                continue
            if service.status == ProviderServiceStatus.DELETED_FROM_PROVIDER:
                product.status = ProductStatus.INACTIVE
                disabled.append(product.id)
        if disabled:
            await session.commit()
            logger.info("عطّل الكتالوج الذاتي %s منتجاً لخدمات محذوفة.", len(disabled))
            await FeatureService.track(
                "catalog_autopilot", "auto_disabled", value=str(len(disabled))
            )
        return disabled

    @staticmethod
    async def arbitrage_alerts(session) -> list[dict]:
        """
        خدمات متاحة عند أكثر من مزود بفارق سعر كبير.
        تنبيه للأدمن ليحوّل المنتج للأرخص فيربح الفرق بلا تغيير سعر.
        """
        if not await CatalogAutopilotService.enabled():
            return []

        groups: dict[str, list[dict]] = {}
        result = await session.execute(
            select(ProviderService).where(
                ProviderService.status == ProviderServiceStatus.ACTIVE
            )
        )
        for service in result.scalars().all():
            key = (service.name or "").strip().casefold()
            if not key:
                continue
            groups.setdefault(key, []).append(
                {
                    "provider_id": service.api_provider_id,
                    "rate_usd": service.rate_usd,
                    "service_id": service.external_service_id,
                }
            )

        alerts = []
        for name, options in groups.items():
            if len(options) < 2:
                continue
            rates = [o["rate_usd"] for o in options if o["rate_usd"] and o["rate_usd"] > 0]
            if len(rates) < 2:
                continue
            cheapest, dearest = min(rates), max(rates)
            if cheapest <= 0:
                continue
            spread = (dearest - cheapest) / cheapest * 100
            if spread >= 15:
                alerts.append(
                    {
                        "service": name,
                        "cheapest_usd": cheapest,
                        "dearest_usd": dearest,
                        "spread_percent": round(spread, 1),
                    }
                )
        alerts.sort(key=lambda a: a["spread_percent"], reverse=True)
        return alerts[:20]


class FxFeedService:
    """أسعار صرف حية بدل الإدخال اليدوي."""

    SOURCES = {
        "exchangerate_host": "https://api.frankfurter.app/latest?from=USD",
    }

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("live_fx_feed")

    @staticmethod
    async def refresh(session) -> dict:
        """يجلب الأسعار ويحدّث الإعدادات، مع حماية من القفزات غير المنطقية."""
        if not await FxFeedService.enabled():
            return {}
        source = await FeatureService.config("live_fx_feed", "source", "exchangerate_host")
        url = FxFeedService.SOURCES.get(source)
        if url is None:
            logger.warning("مصدر أسعار صرف غير معروف: %s", source)
            return {}

        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.get(url) as response:
                    if response.status != 200:
                        logger.warning("مصدر أسعار الصرف أرجع %s", response.status)
                        return {}
                    payload = await response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("تعذّر جلب أسعار الصرف: %s", exc)
            return {}

        rates = payload.get("rates") if isinstance(payload, dict) else None
        if not isinstance(rates, dict):
            return {}

        max_deviation = await FeatureService.config_decimal(
            "live_fx_feed", "max_deviation_percent", 20.0
        )
        applied: dict[str, float] = {}
        for currency, value in rates.items():
            if currency == "USD":
                continue
            try:
                rate = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                continue
            if rate <= 0:
                continue

            # حماية: لا نقبل قفزة تتجاوز الحد، فخطأ في المصدر يكسر كل الأسعار
            previous = await SettingsService.get(f"rate_{currency.lower()}_to_usd", None)
            if previous:
                try:
                    old = Decimal(previous)
                    if old > 0:
                        change = abs(rate - old) / old * 100
                        if change > Decimal(str(max_deviation)):
                            logger.warning(
                                "تجاهل سعر %s: تغير %.1f%% يتجاوز الحد %.1f%%",
                                currency, change, max_deviation,
                            )
                            continue
                except (InvalidOperation, ValueError):
                    pass

            # البوت يخزّن "كم وحدة من العملة = 1 دولار"
            per_usd = (Decimal(1) / rate).quantize(Decimal("0.000001"))
            await SettingsService.set(
                session, f"rate_{currency.lower()}_to_usd", str(per_usd)
            )
            applied[currency] = float(per_usd)

        if applied:
            await FeatureService.track(
                "live_fx_feed", "refreshed", value=str(len(applied))
            )
            logger.info("حُدِّثت أسعار صرف %s عملة.", len(applied))
        return applied
