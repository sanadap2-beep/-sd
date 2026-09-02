"""
مدير مزودي الأرقام مع Failover تلقائي.

المنطق:
1) يجلب أسعار كل المزودين المتاحين للدولة/الخدمة.
2) يرتبهم حسب السعر (الأرخص أولاً).
3) يحاول الشراء من الأرخص، إذا فشل ينتقل للتالي.
4) يتحقق من is_online قبل المحاولة لتجنب التأخير.
5) العملة: دولار أمريكي (USD) بالكامل.
"""

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal

from database.models import Country, NumberService, ProviderName, ProviderStatus
from providers.fivesim import FiveSimProvider
from providers.herosms import HeroSMSProvider
from providers.sms_activate import SMSActivateProvider
from providers.smshub import SMSHubProvider
from services.price_cache_service import PriceCacheService
from config import settings

logger = logging.getLogger(__name__)


class ProviderUnavailableError(Exception):
    pass


@dataclass
class BuyResult:
    provider: ProviderName
    provider_order_id: str
    phone_number: str
    cost_usd: Decimal


class ProviderManager:
    def __init__(self):
        self._providers: dict[ProviderName, object] = {}
        self._init_providers()

    def _init_providers(self):
        """يُنشئ المزودين الذين لديهم API key فقط."""
        if settings.FIVESIM_API_KEY:
            self._providers[ProviderName.FIVESIM] = FiveSimProvider()

        if settings.HEROSMS_API_KEY:
            self._providers[ProviderName.HEROSMS] = HeroSMSProvider()

        if settings.SMS_ACTIVATE_API_KEY:
            self._providers[ProviderName.SMS_ACTIVATE] = SMSActivateProvider()

        if settings.SMSHUB_API_KEY:
            self._providers[ProviderName.SMSHUB] = SMSHubProvider()

        logger.info(
            f"تم تهيئة {len(self._providers)} مزود أرقام: "
            f"{', '.join(p.value for p in self._providers.keys())}"
        )

    def _get_instance(self, provider: ProviderName):
        instance = self._providers.get(provider)
        if instance is None:
            raise ProviderUnavailableError(f"المزود {provider.value} غير مهيأ (لا يوجد API Key)")
        return instance

    def _get_provider_code(
        self,
        provider: ProviderName,
        country: Country,
    ) -> str | None:
        """يجلب كود الدولة الخاص بمزود معين."""
        code_map = {
            ProviderName.FIVESIM: country.fivesim_code,
            ProviderName.HEROSMS: country.herosms_code,
            ProviderName.SMS_ACTIVATE: country.sms_activate_code,
            ProviderName.SMSHUB: country.smshub_code,
        }
        return code_map.get(provider)

    def _get_service_code(
        self,
        provider: ProviderName,
        service: NumberService,
    ) -> str | None:
        """يجلب كود الخدمة الخاص بمزود معين."""
        code_map = {
            ProviderName.FIVESIM: service.fivesim_code,
            ProviderName.HEROSMS: service.herosms_code,
            ProviderName.SMS_ACTIVATE: service.sms_activate_code,
            ProviderName.SMSHUB: service.smshub_code,
        }
        return code_map.get(provider)

    async def _is_provider_online(
        self,
        session,
        provider: ProviderName,
    ) -> bool:
        """يتحقق من حالة المزود في قاعدة البيانات."""
        status = await session.get(ProviderStatus, provider)
        if status is None:
            # لا يوجد سجل حالة بعد (تثبيت جديد أو مزود أضيف حديثاً):
            # نسمح بالمحاولة بدل حجب كل الطلبات حتى أول فحص صحة.
            return True
        # A fresh installation has no health-check result yet. Allow the
        # first real request instead of blocking every provider for the first
        # ten minutes until the scheduler runs.
        if status.last_checked_at is None:
            return True
        return status.is_online

    async def get_cheapest_price(
        self,
        service: NumberService,
        country: Country,
        session=None,
    ) -> dict[ProviderName, Decimal]:
        """
        يجلب أسعار كل المزودين المتاحين لخدمة/دولة معينة.
        يرجع dict مع المزود كمفتاح والسعر بالدولار كقيمة.
        """
        cache_key = f"number-price:{service.code}:{country.code}"
        cached = await PriceCacheService.get(cache_key)
        if cached is not None:
            return cached

        # ── 1) ترشيح المزودين ──
        # فحوصات قاعدة البيانات تبقى تسلسلية: جلسة SQLAlchemy غير آمنة
        # للاستخدام المتزامن، ومحاولة تواريها تُنتج أخطاء حالة صامتة.
        candidates: list[tuple[ProviderName, object, str, str]] = []
        for provider_name, instance in self._providers.items():
            country_code = self._get_provider_code(provider_name, country)
            service_code = self._get_service_code(provider_name, service)
            if not country_code or not service_code:
                continue
            if session and not await self._is_provider_online(session, provider_name):
                continue
            candidates.append((provider_name, instance, country_code, service_code))

        if not candidates:
            await PriceCacheService.set(cache_key, {}, ttl=20)
            return {}

        # ── 2) جولات الشبكة تتوازى ──
        # كانت متتابعة: 4 مزودين = 4 أضعاف زمن أبطأ مزود. الآن كلها دفعة
        # واحدة، فالزمن الكلي ≈ زمن أبطأ مزود فقط.
        async def _fetch(item):
            provider_name, instance, country_code, service_code = item
            try:
                return provider_name, await instance.get_price(country_code, service_code)
            except Exception as e:  # noqa: BLE001 - مزود واحد لا يُسقط البقية
                logger.warning(f"فشل جلب سعر من {provider_name.value}: {e}")
                return provider_name, None

        fetched = await asyncio.gather(*(_fetch(item) for item in candidates))

        result = {
            provider_name: price
            for provider_name, price in fetched
            if price is not None
        }

        ttl = await PriceCacheService.ttl_seconds()
        await PriceCacheService.set(cache_key, result, ttl=ttl)
        return result

    async def _rank_providers_for_purchase(
        self,
        prices: dict[ProviderName, Decimal],
        service_code: str,
        country_code: str,
        session=None,
    ) -> list[tuple[ProviderName, Decimal]]:
        """
        يرتب المزودين للشراء.

        الوضع القديم كان أرخص سعر فقط. هذا جيد نظرياً لكنه يضر الربح إذا
        كان الأرخص يفشل كثيراً فيسبب استرجاعات وشكاوى. عند تفعيل
        smart_number_routing نحسب "سعراً فعالاً" يجمع السعر مع جودة المزود.
        """
        if not prices:
            return []
        cheapest = min(prices.values())
        if cheapest <= 0:
            return sorted(prices.items(), key=lambda item: item[1])

        if session is None:
            return sorted(prices.items(), key=lambda item: item[1])

        try:
            from services.feature_service import FeatureService
            from services.number_provider_stats_service import NumberProviderStatsService

            if not await FeatureService.enabled("smart_number_routing"):
                return sorted(prices.items(), key=lambda item: item[1])
            price_weight = await FeatureService.config_int(
                "smart_number_routing", "price_weight", 65
            )
            quality_weight = await FeatureService.config_int(
                "smart_number_routing", "quality_weight", 35
            )
            total_weight = max(1, price_weight + quality_weight)
            quality_scores = await NumberProviderStatsService.smart_routing_scores(
                session,
                service_code=service_code,
                country_code=country_code,
            )
        except Exception as exc:  # noqa: BLE001 - لا نكسر الشراء بسبب التحليلات
            logger.debug("تعذّر حساب توجيه المزودين الذكي: %s", exc)
            return sorted(prices.items(), key=lambda item: item[1])

        def effective_score(item: tuple[ProviderName, Decimal]) -> Decimal:
            provider, price = item
            price_factor = Decimal(str(price / cheapest))
            quality = Decimal(str(max(0.10, quality_scores.get(provider, 1.0))))
            quality_factor = Decimal("1") / quality
            return (
                price_factor * Decimal(price_weight)
                + quality_factor * Decimal(quality_weight)
            ) / Decimal(total_weight)

        ranked = sorted(prices.items(), key=effective_score)
        logger.debug(
            "ترتيب مزودي الأرقام الذكي %s/%s: %s",
            service_code,
            country_code,
            [(p.value, str(price), quality_scores.get(p, 1.0)) for p, price in ranked],
        )
        return ranked

    async def buy_number(
        self,
        service: NumberService,
        country: Country,
        session=None,
        preferred_provider: ProviderName | None = None,
    ) -> BuyResult:
        """
        يشتري رقماً من أرخص مزود متاح.
        إذا فشل ينتقل تلقائياً للمزود التالي (Failover).
        """
        prices = await self.get_cheapest_price(service, country, session)

        if not prices:
            raise ProviderUnavailableError(
                f"لا يوجد مزود متاح لـ {service.name_ar} - {country.name_ar}"
            )

        sorted_providers = await self._rank_providers_for_purchase(
            prices,
            service_code=service.code,
            country_code=country.code,
            session=session,
        )
        if preferred_provider in prices:
            sorted_providers = [
                (preferred_provider, prices[preferred_provider]),
                *[item for item in sorted_providers if item[0] != preferred_provider],
            ]

        errors = []

        for provider_name, estimated_price in sorted_providers:
            instance = self._providers[provider_name]
            country_code = self._get_provider_code(provider_name, country)
            service_code = self._get_service_code(provider_name, service)

            try:
                # سقف سعر بحد 5% فوق التقدير: يمنع الشراء إذا قفز السعر
                # لحظة الشراء، ويتيح تحولاً للمزود التالي بدل الخسارة.
                max_price = (estimated_price * Decimal("1.05")).quantize(
                    Decimal("0.0001")
                )
                purchased = await instance.buy_number(
                    country_code, service_code, max_price=max_price
                )
                await PriceCacheService.invalidate(f"number-price:{service.code}:{country.code}")
                logger.info(
                    f"شراء ناجح من {provider_name.value}: "
                    f"{purchased.phone_number} "
                    f"بتكلفة {purchased.cost_usd}$"
                )
                return BuyResult(
                    provider=provider_name,
                    provider_order_id=purchased.provider_order_id,
                    phone_number=purchased.phone_number,
                    cost_usd=purchased.cost_usd,
                )
            except Exception as e:
                errors.append(f"{provider_name.value}: {e}")
                logger.warning(f"فشل الشراء من {provider_name.value}: {e}")
                continue

        raise ProviderUnavailableError(f"فشل الشراء من كل المزودين المتاحين. {' | '.join(errors)}")

    async def check_status(
        self,
        provider: ProviderName,
        order_id: str,
    ):
        """يتحقق من حالة طلب عند مزود محدد."""
        return await self._get_instance(provider).check_status(order_id)

    async def cancel_order(
        self,
        provider: ProviderName,
        order_id: str,
    ) -> bool:
        """يلغي طلباً عند مزود محدد."""
        return await self._get_instance(provider).cancel_order(order_id)

    async def finish_order(
        self,
        provider: ProviderName,
        order_id: str,
    ) -> bool:
        """يُنهي طلباً عند مزود محدد بعد استلام الكود."""
        return await self._get_instance(provider).finish_order(order_id)

    async def get_balance(
        self,
        provider: ProviderName,
    ) -> Decimal:
        """يجلب رصيد مزود محدد بالدولار."""
        return await self._get_instance(provider).get_balance()

    def get_available_providers(self) -> list[ProviderName]:
        """يجلب قائمة المزودين المهيأين (لديهم API Key)."""
        return list(self._providers.keys())


provider_manager = ProviderManager()