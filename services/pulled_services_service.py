"""Admin catalog of pulled SMM provider services.

Pulled services stay hidden from the storefront until the admin publishes
one into a subcategory they created, with an explicit selling price.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import (
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductPricingType,
    ProductStatus,
    ProviderService,
    ProviderServiceStatus,
    SubCategory,
)
from services.dynamic_service import DynamicService
from services.margin_service import MarginService
from services.smm_catalog import (
    OTHER_KIND_KEY,
    OTHER_PLATFORM_KEY,
    classify_smm_service,
    kind_meta,
    platform_meta,
)

SERVICES_PER_PAGE = 8


class PulledServicesService:
    @staticmethod
    def classify(service: ProviderService) -> tuple[str, str]:
        return classify_smm_service(service.name, service.category, service.service_type)

    @staticmethod
    async def load_active(session) -> list[ProviderService]:
        result = await session.execute(
            select(ProviderService)
            .options(selectinload(ProviderService.api_provider))
            .where(ProviderService.status == ProviderServiceStatus.ACTIVE)
        )
        return list(result.scalars().all())

    @staticmethod
    async def platform_counts(session) -> list[tuple[str, str, str, int]]:
        """``[(platform_key, emoji, label, count), ...]`` cheapest-platform first by count desc."""
        services = await PulledServicesService.load_active(session)
        counts: dict[str, int] = defaultdict(int)
        for service in services:
            platform, _kind = PulledServicesService.classify(service)
            counts[platform] += 1
        rows = []
        for key, count in counts.items():
            emoji, label = platform_meta(key)
            rows.append((key, emoji, label, count))
        rows.sort(key=lambda row: (row[0] == OTHER_PLATFORM_KEY, -row[3], row[2]))
        return rows

    @staticmethod
    async def kind_counts(session, platform_key: str) -> list[tuple[str, str, str, int]]:
        services = await PulledServicesService.load_active(session)
        counts: dict[str, int] = defaultdict(int)
        for service in services:
            platform, kind = PulledServicesService.classify(service)
            if platform != platform_key:
                continue
            counts[kind] += 1
        rows = []
        for key, count in counts.items():
            emoji, label = kind_meta(key)
            rows.append((key, emoji, label, count))
        rows.sort(key=lambda row: (row[0] == OTHER_KIND_KEY, -row[3], row[2]))
        return rows

    @staticmethod
    async def list_services(
        session,
        platform_key: str,
        kind_key: str,
        page: int = 0,
        per_page: int = SERVICES_PER_PAGE,
    ) -> tuple[list[ProviderService], int]:
        services = await PulledServicesService.load_active(session)
        matched = []
        for service in services:
            platform, kind = PulledServicesService.classify(service)
            if platform == platform_key and kind == kind_key:
                matched.append(service)
        matched.sort(key=lambda s: (Decimal(str(s.rate_usd or 0)), s.id))
        total = len(matched)
        page = max(0, page)
        start = page * per_page
        return matched[start : start + per_page], total

    @staticmethod
    async def destination_subcategories(session) -> list[SubCategory]:
        """الأقسام القابلة لاستقبال منتج: «الأوراق» فقط (بلا أقسام داخلية).

        القسم الذي يحوي أقساماً داخلية (تطبيق رشق) لا يُنشر فيه مباشرة،
        بل يُنشر داخل أحد أقسامه الداخلية حتى يظهر للزبون.
        """
        return await DynamicService.get_active_leaf_sub_categories(session)

    @staticmethod
    async def platform_app_subcategory(
        session, platform_key: str
    ) -> SubCategory | None:
        """القسم الفرعي (التطبيق) المقابل لمنصة SMM داخل قسم الرشق."""
        from database.models import Category, CategoryType
        from services.smm_catalog import SHORT_TO_PLATFORM, resolve_smm_app

        app_name = SHORT_TO_PLATFORM.get(platform_key)
        if not app_name:
            return None
        smm_category = (
            await session.execute(select(Category).where(Category.type == CategoryType.SMM))
        ).scalar_one_or_none()
        if smm_category is None:
            return None
        apps = await DynamicService.get_active_root_sub_categories(session, smm_category.id)
        for app in apps:
            haystack = f"{app.emoji or ''} {app.name_ar or ''}"
            resolved = resolve_smm_app(haystack) or resolve_smm_app(app.name_ar or "")
            if resolved is not None and resolved.name_ar == app_name:
                return app
        return None

    @staticmethod
    async def app_section_destinations(
        session, app_sub: SubCategory
    ) -> list[tuple[SubCategory, int]]:
        """أقسام تطبيق داخلية مفعلة مع عدد منتجاتها: ``[(section, count), ...]``."""
        sections = await DynamicService.get_active_child_sections(session, app_sub.id)
        counts = await DynamicService.active_product_counts_by_sub(
            session, [section.id for section in sections]
        )
        result = []
        for section in sections:
            count = counts.get(section.id, 0)
            if count > 0:
                result.append((section, count))
        return result

    @staticmethod
    def parse_sell_price(raw: str) -> Decimal:
        try:
            price = Decimal((raw or "").strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("أرسل رقماً صحيحاً أكبر من صفر.") from exc
        if not price.is_finite() or price <= 0:
            raise ValueError("أرسل رقماً صحيحاً أكبر من صفر.")
        return price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    @staticmethod
    async def publish(
        session,
        service: ProviderService,
        sub_category_id: int,
        sell_price: Decimal,
        name_ar: str | None = None,
    ):
        """Create a storefront product from a pulled service. Hidden until this call."""
        from services.service_localization_service import display_service_name

        # الاسم بالعربية دائماً: ما أرسله الأدمن إن أرسل، وإلا تعريب
        # اسم المزود تلقائياً (المنصة + النوع + الكلمات المألوفة).
        default_name = display_service_name(
            service.name, service.category, service.service_type
        )
        product = await DynamicService.create_product(
            session=session,
            sub_category_id=sub_category_id,
            name_ar=(name_ar or default_name or "خدمة رشق")[:128],
            description=(service.description or service.category or "")[:500] or None,
            price_usd=sell_price,
            cost_price_usd=Decimal(str(service.rate_usd or 0)),
            api_provider_id=service.api_provider_id,
            provider_service_id=service.external_service_id,
            provider_service_ref_id=service.id,
            fulfillment_type=ProductFulfillmentType.API,
            min_quantity=int(service.min_quantity or 1),
            max_quantity=int(service.max_quantity or 1),
            requires_link=bool(service.requires_link),
            requires_player_id=bool(service.requires_player_id),
            requires_quantity=bool(service.requires_quantity),
            display_type=(
                ProductDisplayType.PER_1000
                if service.requires_quantity
                else ProductDisplayType.FIXED_TOTAL
            ),
        )
        # إصلاح قسم الرشق: نحفظ الهامش الضمني المستخلص من
        # (سعر البيع مقابل التكلفة) حتى يصبح الهامش مطبقاً وظاهراً
        # وقابلاً للتعديل، ولا يبقى المنتج بلا هامش يُحتسب عليه.
        await MarginService.attach_implicit_margin(product)
        await session.commit()
        return product

    @staticmethod
    async def sync_store_to_section(session, provider) -> dict:
        """
        مزامنة متجر كامل إلى قسم باسم المتجر:
        - ينشئ/يستعمل قسماً باسم المزود (نوع مخصص).
        - ينشئ/يستعمل قسماً فرعياً «الخدمات» داخله.
        - ينشر كل خدمات المزود المزامنة فيه كمنتجات تلقائية
          (تكلفة + الهامش العالمي)، بالعربية، من الأرخص للأغلى.
        - إعادة المزامنة لا تكرّر: المنتجات اليدوية تُهمَل،
          والتلقائية تُعاد تفعيلها وترتيبها.

        بعدها يملك الأدمن كل الصلاحيات: إيقاف أي منتج لا يريد
        بيعه، ضبط هامش أي منتج/قسم، أو نشر أي خدمة بسعره الخاص
        في أي قسم آخر (مسار النشر اليدوي).
        """
        from database.models import Category, CategoryType
        from services.margin_service import MarginService
        from services.service_localization_service import display_service_name

        report = {
            "category": None,
            "section": None,
            "created": 0,
            "reactivated": 0,
            "reordered": 0,
            "skipped_manual": 0,
        }

        # 1) قسم باسم المتجر (أو الموجود مسبقاً)
        result = await session.execute(
            select(Category).where(Category.name_ar == provider.name)
        )
        category = result.scalars().first()
        if category is None:
            category = await DynamicService.create_category(
                session, provider.name[:64], "🏬", CategoryType.CUSTOM
            )
        category.is_active = True

        # 2) قسم فرعي «الخدمات» داخله
        result = await session.execute(
            select(SubCategory).where(
                SubCategory.category_id == category.id,
                SubCategory.name_ar == "الخدمات",
                SubCategory.parent_sub_category_id.is_(None),
            )
        )
        section = result.scalars().first()
        if section is None:
            section = await DynamicService.create_sub_category(
                session,
                category.id,
                "الخدمات",
                "🏬",
                description=f"خدمات المتجر {provider.name}",
            )
        section.is_active = True
        report["category"] = category
        report["section"] = section

        # 3) كل خدمات المزود المزامنة، من الأرخص للأغلى
        result = await session.execute(
            select(ProviderService)
            .where(
                ProviderService.api_provider_id == provider.id,
                ProviderService.status == ProviderServiceStatus.ACTIVE,
            )
            .order_by(ProviderService.rate_usd.asc())
        )
        services = list(result.scalars().all())

        margin = await MarginService.global_percent()

        for position, service in enumerate(services):
            existing_result = await session.execute(
                select(Product).where(
                    Product.provider_service_ref_id == service.id,
                    Product.sub_category_id == section.id,
                )
            )
            existing = existing_result.scalars().first()

            name_ar = display_service_name(
                service.name, service.category, service.service_type
            )[:128] or "خدمة"
            rate = Decimal(str(service.rate_usd or 0))
            sell = (rate * (Decimal("100") + margin) / Decimal("100")).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )

            if existing is not None:
                if existing.is_auto_published:
                    if existing.status != ProductStatus.ACTIVE:
                        existing.status = ProductStatus.ACTIVE
                        report["reactivated"] += 1
                    existing.sort_order = position * 10
                    report["reordered"] += 1
                else:
                    report["skipped_manual"] += 1
                continue

            product = Product(
                sub_category_id=section.id,
                api_provider_id=provider.id,
                provider_service_ref_id=service.id,
                provider_service_id=service.external_service_id,
                name_ar=name_ar,
                description=(service.description or "")[:500] or None,
                price_usd=sell,
                cost_price_usd=rate,
                pricing_type=ProductPricingType.MARGIN_PERCENT,
                profit_margin_percent=margin,
                fulfillment_type=ProductFulfillmentType.API,
                min_quantity=int(service.min_quantity or 1),
                max_quantity=int(service.max_quantity or 1000000),
                requires_link=bool(service.requires_link),
                requires_player_id=bool(service.requires_player_id),
                requires_quantity=bool(service.requires_quantity),
                display_type=(
                    ProductDisplayType.PER_1000
                    if service.requires_quantity
                    else ProductDisplayType.FIXED_TOTAL
                ),
                sort_order=position * 10,
                status=ProductStatus.ACTIVE,
                is_auto_published=True,
            )
            session.add(product)
            report["created"] += 1

        await session.commit()
        return report
