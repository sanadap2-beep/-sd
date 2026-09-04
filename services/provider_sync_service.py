"""
خدمة تزامن ومزامنة الخدمات من المزودين.

المهام الرئيسية:
1) سحب كل خدمات المزود وحفظها في قاعدة البيانات
2) تحديث الخدمات الموجودة إذا تغيرت
3) تعطيل الخدمات المحذوفة من المزود
4) تحويل الأسعار من عملة المزود إلى دولار
5) فحص رصيد المزود
6) البحث في خدمات مزود معين
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, and_, or_, func
from sqlalchemy.exc import IntegrityError

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ProviderService,
    ProviderServiceStatus,
    ProviderPriceType,
    Product,
    ProductStatus,
)
from protocols.base import ProtocolError
from protocols.factory import ProtocolFactory
from services.currency_service import CurrencyService
from services.service_localization_service import display_service_name

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """نتيجة عملية التزامن."""

    provider_id: int
    provider_name: str
    total_fetched: int = 0
    new_services: int = 0
    updated_services: int = 0
    deactivated_services: int = 0
    reactivated_services: int = 0
    failed_services: int = 0
    products_affected: int = 0
    success: bool = True
    error_message: str | None = None
    duration_seconds: float = 0

    def summary(self) -> str:
        """ملخص نصي للنتيجة."""
        if not self.success:
            return f"❌ فشل تزامن {self.provider_name}\nالسبب: {self.error_message}"

        return (
            f"✅ اكتمل تزامن {self.provider_name}\n\n"
            f"📊 <b>النتائج:</b>\n"
            f"• تم سحب: {self.total_fetched} خدمة\n"
            f"• جديدة: {self.new_services}\n"
            f"• محدّثة: {self.updated_services}\n"
            f"• معطّلة (محذوفة من المزود): "
            f"{self.deactivated_services}\n"
            f"• أعيد تفعيلها: {self.reactivated_services}\n"
            f"• فشلت: {self.failed_services}\n"
            f"• منتجات متأثرة: {self.products_affected}\n"
            f"⏱ الوقت: {self.duration_seconds:.1f} ثانية"
        )


class ProviderSyncService:
    """خدمة تزامن الخدمات من المزودين."""

    @staticmethod
    async def test_provider_connection(
        provider: ApiProvider,
    ) -> tuple[bool, str, Decimal | None, str | None]:
        """
        يختبر الاتصال بمزود ويجلب رصيده.

        Returns:
            (success, message, balance, currency)
        """
        try:
            protocol = ProtocolFactory.create_from_provider(provider)
        except ProtocolError as e:
            return False, f"خطأ في البروتوكول: {e}", None, None

        try:
            balance_obj = await protocol.get_balance()
            return (
                True,
                "اتصال ناجح",
                balance_obj.amount,
                balance_obj.currency,
            )
        except ProtocolError as e:
            return False, str(e), None, None
        except Exception as e:
            logger.error(f"خطأ غير متوقع اختبار مزود {provider.id}: {e}")
            return False, f"خطأ غير متوقع: {e}", None, None

    @staticmethod
    async def update_provider_balance(
        provider_id: int,
    ) -> tuple[bool, str]:
        """يحدّث رصيد المزود في قاعدة البيانات."""
        async with async_session_maker() as session:
            provider = await session.get(ApiProvider, provider_id)
            if not provider:
                return False, "المزود غير موجود"

            (
                success,
                message,
                balance,
                currency,
            ) = await ProviderSyncService.test_provider_connection(provider)

            provider.last_checked_at = datetime.utcnow()

            if not success:
                provider.last_error = message[:500]
                await session.commit()
                return False, message

            provider.balance = balance
            if currency:
                provider.currency = currency
            provider.last_error = None
            await session.commit()

            return True, (f"الرصيد الحالي: {balance} {currency}")

    @staticmethod
    async def sync_provider_services(
        provider_id: int,
    ) -> SyncResult:
        """
        يسحب كل خدمات المزود ويحفظها/يحدّثها.

        هذه الدالة الرئيسية للتزامن.
        """
        start_time = datetime.utcnow()

        async with async_session_maker() as session:
            provider = await session.get(ApiProvider, provider_id)
            if not provider:
                return SyncResult(
                    provider_id=provider_id,
                    provider_name="Unknown",
                    success=False,
                    error_message="المزود غير موجود",
                )

            result = SyncResult(
                provider_id=provider_id,
                provider_name=provider.name,
            )

            try:
                protocol = ProtocolFactory.create_from_provider(provider)
            except ProtocolError as e:
                result.success = False
                result.error_message = f"خطأ في البروتوكول: {e}"
                return result

            try:
                services = await protocol.get_services()
                result.total_fetched = len(services)
            except ProtocolError as e:
                result.success = False
                result.error_message = str(e)
                provider.last_error = str(e)[:500]
                await session.commit()
                return result
            except Exception as e:
                logger.error(f"خطأ غير متوقع سحب خدمات {provider.id}: {e}")
                result.success = False
                result.error_message = f"خطأ غير متوقع: {e}"
                return result

            existing_result = await session.execute(
                select(ProviderService).where(ProviderService.api_provider_id == provider_id)
            )
            existing_services = {s.external_service_id: s for s in existing_result.scalars().all()}

            rate_to_usd = provider.rate_to_usd or Decimal("1")
            if provider.currency and provider.currency != "USD":
                if rate_to_usd == Decimal("1"):
                    rate_to_usd = await CurrencyService.get_rate_to_usd(provider.currency)
                    provider.rate_to_usd = rate_to_usd

            fetched_ids = set()

            for service in services:
                try:
                    fetched_ids.add(service.external_id)

                    rate_usd = (service.rate * rate_to_usd).quantize(Decimal("0.0001"))

                    raw_json = json.dumps(
                        service.raw,
                        ensure_ascii=False,
                    )[:5000]

                    # التعريب وقت السحب: كل خدمة مسحوبة تملك اسماً عربياً
                    # محفوظاً (للعرض والبيع والبحث)، والاسم الأصلي الإنجليزي
                    # يبقى في name كما وصل من المزود.
                    name_ar = display_service_name(
                        service.name, service.category, service.service_type
                    )

                    existing = existing_services.get(service.external_id)

                    if existing:
                        was_deleted = existing.status == ProviderServiceStatus.DELETED_FROM_PROVIDER

                        existing.name = service.name
                        existing.name_ar = name_ar
                        existing.category = service.category
                        existing.service_type = service.service_type
                        existing.rate = service.rate
                        existing.rate_usd = rate_usd
                        existing.min_quantity = service.min_quantity
                        existing.max_quantity = service.max_quantity
                        existing.description = service.description
                        existing.requires_link = service.requires_link
                        existing.requires_quantity = service.requires_quantity
                        existing.requires_player_id = service.requires_player_id
                        existing.supports_refill = service.supports_refill
                        existing.supports_cancel = service.supports_cancel
                        existing.raw_data = raw_json
                        existing.last_updated = datetime.utcnow()

                        if was_deleted:
                            existing.status = ProviderServiceStatus.ACTIVE
                            result.reactivated_services += 1
                        else:
                            result.updated_services += 1
                    else:
                        new_service = ProviderService(
                            api_provider_id=provider_id,
                            external_service_id=(service.external_id),
                            name=service.name,
                            name_ar=name_ar,
                            category=service.category,
                            service_type=service.service_type,
                            rate=service.rate,
                            rate_usd=rate_usd,
                            price_type=(ProviderPriceType.PER_1000),
                            min_quantity=service.min_quantity,
                            max_quantity=service.max_quantity,
                            description=service.description,
                            requires_link=service.requires_link,
                            requires_quantity=(service.requires_quantity),
                            requires_player_id=(service.requires_player_id),
                            supports_refill=(service.supports_refill),
                            supports_cancel=(service.supports_cancel),
                            status=(ProviderServiceStatus.ACTIVE),
                            raw_data=raw_json,
                        )
                        session.add(new_service)
                        result.new_services += 1

                except Exception as e:
                    logger.warning(f"فشل حفظ خدمة {service.external_id}: {e}")
                    result.failed_services += 1
                    continue

            deleted_ids = set(existing_services.keys()) - fetched_ids
            for deleted_id in deleted_ids:
                existing = existing_services[deleted_id]
                if existing.status != ProviderServiceStatus.DELETED_FROM_PROVIDER:
                    existing.status = ProviderServiceStatus.DELETED_FROM_PROVIDER
                    result.deactivated_services += 1

                    products_result = await session.execute(
                        select(Product).where(Product.provider_service_ref_id == existing.id)
                    )
                    affected_products = products_result.scalars().all()
                    for product in affected_products:
                        product.status = ProductStatus.INACTIVE
                        result.products_affected += 1

            provider.last_sync_at = datetime.utcnow()
            # total_fetched already contains existing and newly discovered
            # active services; deleted legacy services are not part of it.
            provider.total_services = result.total_fetched
            provider.last_error = None

            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                logger.error(f"خطأ integrity في تزامن {provider.id}: {e}")
                result.success = False
                result.error_message = str(e)[:200]
                return result

            duration = (datetime.utcnow() - start_time).total_seconds()
            result.duration_seconds = duration

            logger.info(
                f"تزامن {provider.name} انتهى: "
                f"{result.new_services} جديدة, "
                f"{result.updated_services} محدّثة, "
                f"{result.deactivated_services} معطّلة "
                f"في {duration:.1f}s"
            )

            return result

    @staticmethod
    async def get_provider_services_count(
        provider_id: int,
        active_only: bool = True,
    ) -> int:
        """يجلب عدد خدمات مزود معين."""
        async with async_session_maker() as session:
            query = select(func.count(ProviderService.id)).where(
                ProviderService.api_provider_id == provider_id
            )

            if active_only:
                query = query.where(ProviderService.status == ProviderServiceStatus.ACTIVE)

            result = await session.execute(query)
            return result.scalar_one()

    @staticmethod
    async def get_provider_services(
        provider_id: int,
        limit: int = 20,
        offset: int = 0,
        active_only: bool = True,
        search: str | None = None,
        category: str | None = None,
    ) -> list[ProviderService]:
        """
        يجلب خدمات مزود مع دعم البحث والـ Pagination.
        """
        async with async_session_maker() as session:
            query = select(ProviderService).where(ProviderService.api_provider_id == provider_id)

            if active_only:
                query = query.where(ProviderService.status == ProviderServiceStatus.ACTIVE)

            if search:
                search_lower = f"%{search.lower()}%"
                query = query.where(
                    or_(
                        func.lower(ProviderService.name).like(search_lower),
                        func.lower(ProviderService.category).like(search_lower),
                        ProviderService.external_service_id == search,
                    )
                )

            if category:
                query = query.where(ProviderService.category == category)

            query = query.order_by(
                ProviderService.category,
                ProviderService.name,
            )
            query = query.limit(limit).offset(offset)

            result = await session.execute(query)
            return list(result.scalars().all())

    @staticmethod
    async def get_provider_categories(
        provider_id: int,
    ) -> list[tuple[str, int]]:
        """
        يجلب كل التصنيفات لدى مزود معين مع عدد الخدمات في كل واحد.
        """
        async with async_session_maker() as session:
            result = await session.execute(
                select(
                    ProviderService.category,
                    func.count(ProviderService.id).label("count"),
                )
                .where(
                    and_(
                        ProviderService.api_provider_id == provider_id,
                        ProviderService.status == ProviderServiceStatus.ACTIVE,
                        ProviderService.category.isnot(None),
                    )
                )
                .group_by(ProviderService.category)
                .order_by(ProviderService.category)
            )

            return [(row.category, row.count) for row in result.all()]

    @staticmethod
    async def get_service_by_id(
        service_id: int,
    ) -> ProviderService | None:
        """يجلب خدمة مزود بواسطة الـ ID الداخلي."""
        async with async_session_maker() as session:
            return await session.get(ProviderService, service_id)
