"""
مزامنة تلقائية لكتالوج مزود «ggsoma» (اشتراكات رقمية) داخل المتجر.

تسحب كل منتجات المزود المرتبط (LINK/COUPON/READY_ACCOUNT) وتنشرها
تلقائياً في «قسم الاشتراكات الرقمية 🔐»:
- قسم فرعي (تطبيق) لكل مزوّد/علامة في كتالوج ggsoma (مثلاً Gemini، CapCut…).
- كل منتج يظهر بسعر بيع = تكلفتك عند المزود (yourPrice) + هامش الربح
  المضبوط في الإضافة (افتراضياً 23%)، بدون أي تدخل يدوي.
- المزامنة إضافية وآمنة: لا تكرر المنتجات الموجودة، لا تحذف شيئاً، تتبنى
  الأقسام اليدوية ذات الاسم نفسه، وتكتفي بتحديث التوفر والأسعار للمنتجات
  التي أنشأتها هي فقط (is_auto_published) — منتجات الأدمن اليدوية تبقى كما هي.
- أي منتج نفد مخزونه عند المزود يُعطَّل تلقائياً (ويُعاد تفعيله إذا رجع)،
  وأي منتج جديد بالمزود يُنشر في المزامنة التالية.

التشغيل: عند إقلاع البوت (إعداد auto_on_startup) + زر «🛍 مزامنة
الاشتراكات الرقمية» في شاشة الخدمات المسحوبة.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from database.models import (
    ApiProvider,
    Category,
    CategoryType,
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductPricingType,
    ProductStatus,
    ProviderPriceType,
    ProviderService,
    ProviderServiceStatus,
    SubCategory,
)
from protocols.factory import ProtocolFactory
from protocols.ggsoma_v1 import GgsomaPartnerProtocol, is_ggsoma_v1_config
from services.feature_service import FeatureService
from services.smm_catalog import normalize_label

logger = logging.getLogger(__name__)

FEATURE_KEY = "subscriptions_auto_sync"

DEFAULT_SUB_CATEGORY_EMOJI = "🔐"
DEFAULT_MARGIN_PERCENT = Decimal("23")


def apply_margin(cost: Decimal, margin_percent: Decimal) -> Decimal:
    """سعر البيع = تكلفة المزود + هامش الربح (مقرب لأقرب سنت)."""
    cost = Decimal(str(cost or 0))
    margin = Decimal(str(margin_percent or 0))
    sell = (cost * (Decimal("100") + margin) / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return max(sell, Decimal("0.01"))


class SubscriptionsSyncService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY, default=True)

    @staticmethod
    async def auto_on_startup() -> bool:
        return bool(
            await FeatureService.config_bool(FEATURE_KEY, "auto_on_startup", True)
        )

    @staticmethod
    async def margin_percent() -> Decimal:
        return Decimal(
            str(await FeatureService.config_decimal(FEATURE_KEY, "margin_percent", 23))
        )

    @staticmethod
    async def providers(session) -> list[ApiProvider]:
        """كل مزودي ggsoma النشطين في قاعدة البيانات."""
        result = await session.execute(
            select(ApiProvider).where(ApiProvider.is_active.is_(True))
        )
        providers = []
        for provider in result.scalars().all():
            try:
                if is_ggsoma_v1_config(json.loads(provider.custom_config or "{}")):
                    providers.append(provider)
            except (TypeError, ValueError):
                continue
        return providers

    @staticmethod
    async def _ensure_category(session) -> Category:
        result = await session.execute(
            select(Category).where(Category.type == CategoryType.SUBSCRIPTIONS)
        )
        category = result.scalar_one_or_none()
        if category is not None:
            return category
        category = Category(
            name_ar="قسم الاشتراكات الرقمية",
            emoji=DEFAULT_SUB_CATEGORY_EMOJI,
            type=CategoryType.SUBSCRIPTIONS,
            is_active=True,
            sort_order=60,
        )
        session.add(category)
        await session.flush()
        return category

    @staticmethod
    async def _ensure_app_section(
        session, category: Category, meta: dict
    ) -> tuple[SubCategory, bool]:
        """قسم فرعي لكل علامة/مزوّد — يتبنى يدوياً موجوداً بنفس الاسم إن وُجد."""
        name = str(meta.get("name") or meta.get("key") or "خدمات")[:64]
        emoji = str(meta.get("emoji") or DEFAULT_SUB_CATEGORY_EMOJI)
        canonical = normalize_label(name)

        existing = list(
            (
                await session.execute(
                    select(SubCategory).where(
                        SubCategory.category_id == category.id,
                        SubCategory.parent_sub_category_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for section in existing:
            if canonical and normalize_label(section.name_ar or "") == canonical:
                if section.emoji != emoji and emoji:
                    section.emoji = emoji
                return section, False

        section = SubCategory(
            category_id=category.id,
            parent_sub_category_id=None,
            kind_key=None,
            name_ar=name,
            emoji=emoji,
            description=f"اشتراكات {name} الرقمية — توصيل فوري",
            is_active=True,
            sort_order=10,
        )
        session.add(section)
        await session.flush()
        return section, True

    @classmethod
    async def sync_provider(cls, session, provider: ApiProvider) -> dict:
        """مزامنة كاملة لمزود واحد. تعيد تقريراً بعدد العمليات المنفذة."""
        report = {
            "provider_id": provider.id,
            "provider_name": provider.name,
            "apps": 0,
            "sections_created": 0,
            "services_created": 0,
            "services_updated": 0,
            "products_created": 0,
            "products_repriced": 0,
            "products_reactivated": 0,
            "products_deactivated": 0,
            "skipped_manual": 0,
            "errors": 0,
        }
        category = await cls._ensure_category(session)
        protocol = ProtocolFactory.create_from_provider(provider)
        if not isinstance(protocol, GgsomaPartnerProtocol):
            report["errors"] += 1
            return report

        try:
            providers_meta = await protocol.get_providers()
            services = await protocol.get_services()
        except Exception as exc:  # noqa: BLE001
            logger.exception("فشل جلب كتالوج ggsoma (المزود %s): %s", provider.name, exc)
            report["errors"] += 1
            return report

        # دمج بيانات العلامات: name/emoji من /catalog/providers عند توفره.
        meta_by_key: dict[str, dict] = {}
        for meta in providers_meta:
            meta_by_key[str(meta.get("key") or "")] = meta

        grouped: dict[str, list] = defaultdict(list)
        for svc in services:
            grouped[svc.service_type or "other"].append(svc)

        # أصحاب الأقسام الحالية (لتعطيل ما اختفى من الكتالوج نهائياً)
        existing_auto_product_ids: set[int] = set()

        for provider_key, group in sorted(grouped.items()):
            group.sort(key=lambda s: int((s.raw or {}).get("sort_order") or 0))
            meta = meta_by_key.get(provider_key, {})
            meta.setdefault("key", provider_key)
            meta.setdefault("name", (group[0].raw or {}).get("provider_name") or provider_key)
            meta.setdefault("emoji", (group[0].raw or {}).get("provider_emoji") or "")

            section, created = await cls._ensure_app_section(session, category, meta)
            report["apps"] += 1
            if created:
                report["sections_created"] += 1

            margin = await cls.margin_percent()
            for svc in group:
                try:
                    service = await cls._upsert_service(
                        session, provider, svc, section.id, report
                    )
                    if service is None:
                        continue
                    product = await cls._upsert_product(
                        session, provider, service, svc, section, margin, report
                    )
                    if product is not None and product.is_auto_published:
                        existing_auto_product_ids.add(product.id)
                except Exception:  # noqa: BLE001
                    logger.exception("فشل نشر منتج %s", svc.name)
                    report["errors"] += 1

        # تعطيل منتجاتنا التلقائية التي اختفت من كتالوج المزود كلياً.
        try:
            vanished = list(
                (
                    await session.execute(
                        select(Product).where(
                            Product.api_provider_id == provider.id,
                            Product.is_auto_published.is_(True),
                            Product.status == ProductStatus.ACTIVE,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for product in vanished:
                if product.id not in existing_auto_product_ids:
                    product.status = ProductStatus.INACTIVE
                    report["products_deactivated"] += 1
        except Exception:  # noqa: BLE001
            logger.exception("فشل تعطيل منتجات ggsoma المختفية")

        await session.commit()
        return report

    @staticmethod
    async def _upsert_service(session, provider, svc, section_id: int, report: dict):
        """ينشئ/يحدّث صف خدمة المزود (ProviderService) ويعيده."""
        result = await session.execute(
            select(ProviderService).where(
                ProviderService.api_provider_id == provider.id,
                ProviderService.external_service_id == svc.external_id,
            )
        )
        service = result.scalar_one_or_none()
        raw = svc.raw or {}
        in_stock = bool(raw.get("in_stock", True))
        if service is None:
            service = ProviderService(
                api_provider_id=provider.id,
                external_service_id=svc.external_id[:64],
                name=svc.name,
                category=(svc.category or None),
                service_type=svc.service_type,
                rate=svc.rate,
                rate_usd=svc.rate,
                price_type=ProviderPriceType.FIXED,
                min_quantity=1,
                max_quantity=1,
                description=(svc.description or None),
                requires_link=False,
                requires_quantity=False,
                requires_player_id=False,
                supports_refill=False,
                supports_cancel=False,
                status=ProviderServiceStatus.ACTIVE,
                raw_data=json.dumps(raw, ensure_ascii=False),
            )
            session.add(service)
            await session.flush()
            report["services_created"] += 1
            return service

        service.name = svc.name
        service.category = svc.category or None
        service.service_type = svc.service_type
        service.rate = svc.rate
        service.rate_usd = svc.rate
        service.description = svc.description or None
        service.raw_data = json.dumps(raw, ensure_ascii=False)
        report["services_updated"] += 1
        return service

    @staticmethod
    async def _upsert_product(
        session, provider, service, svc, section: SubCategory, margin: Decimal, report: dict
    ):
        """ينشر منتجاً في القسم (أو يحدّث التوفر/السعر لمنتجاتنا التلقائية فقط)."""
        result = await session.execute(
            select(Product).where(
                Product.provider_service_ref_id == service.id,
                Product.sub_category_id == section.id,
            )
        )
        existing = list(result.scalars().all())
        # قد يجلس منتج يدوي للأدمن بجانب منتجنا على نفس الخدمة في نفس القسم:
        # نتعامل مع منتجنا التلقائي فقط، ولا نكرر ولا نلمس ما هو يدوي.
        ours = [p for p in existing if p.is_auto_published]
        product = ours[0] if ours else None
        raw = svc.raw or {}
        in_stock = bool(raw.get("in_stock", True))
        sell_price = apply_margin(svc.rate, margin)

        if product is None and existing:
            # الخدمة مغطاة بمنتج يدوي فقط — لا ننشئ نسخة تلقائية بجانبه.
            report["skipped_manual"] += 1
            return None

        if product is None:
            name = (svc.name or "").strip()[:128] or "اشتراك رقمي"
            product = Product(
                sub_category_id=section.id,
                api_provider_id=provider.id,
                provider_service_ref_id=service.id,
                provider_service_id=svc.external_id,
                name_ar=name,
                description=(svc.description or "توصيل فوري")[:500],
                price_usd=sell_price,
                cost_price_usd=Decimal(str(svc.rate or 0)),
                pricing_type=ProductPricingType.MARGIN_PERCENT,
                profit_margin_percent=margin,
                display_type=ProductDisplayType.FIXED_TOTAL,
                fulfillment_type=ProductFulfillmentType.API,
                min_quantity=1,
                max_quantity=1,
                requires_link=False,
                requires_player_id=False,
                requires_quantity=False,
                sort_order=int((raw.get("sort_order") or 0) or 0),
                status=ProductStatus.ACTIVE if in_stock else ProductStatus.INACTIVE,
                is_auto_published=True,
            )
            session.add(product)
            await session.flush()
            report["products_created"] += 1
            return product

        # منتجاتنا التلقائية: تحديث التوفر، وإعادة تسعير بنفس الهامش عند تغيّر التكلفة.
        if in_stock and product.status != ProductStatus.ACTIVE:
            product.status = ProductStatus.ACTIVE
            report["products_reactivated"] += 1
        elif not in_stock and product.status == ProductStatus.ACTIVE:
            product.status = ProductStatus.INACTIVE
            report["products_deactivated"] += 1
        if product.price_usd != sell_price:
            product.price_usd = sell_price
            product.cost_price_usd = Decimal(str(svc.rate or 0))
            product.profit_margin_percent = margin
            report["products_repriced"] += 1
        product.sort_order = int((raw.get("sort_order") or 0) or 0)
        return product

    @classmethod
    async def sync_all(cls, session) -> list[dict]:
        """يطبّق المزامنة على كل مزودي ggsoma النشطين ويعيد تقاريرهم."""
        reports = []
        for provider in await cls.providers(session):
            try:
                reports.append(await cls.sync_provider(session, provider))
            except Exception:  # noqa: BLE001
                logger.exception("فشل مزامنة مزود %s", provider.name)
                reports.append(
                    {
                        "provider_id": provider.id,
                        "provider_name": provider.name,
                        "apps": 0,
                        "sections_created": 0,
                        "products_created": 0,
                        "errors": 1,
                    }
                )
        return reports
