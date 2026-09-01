"""
خدمة النظام الديناميكي.
تجلب كل الأقسام والمنتجات والمزودين من قاعدة البيانات.
تُستخدم من القائمة الرئيسية وكل الهاندلرز لضمان أن كل شيء ديناميكي.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import (
    Category,
    SubCategory,
    Product,
    ApiProvider,
    NumberService,
    StarsPackage,
    CategoryType,
    ProductStatus,
    ProductFulfillmentType,
    ApiProviderType,
)


class DynamicService:
    # ══════════════ الأقسام الرئيسية ══════════════

    @staticmethod
    async def get_active_categories(session) -> list[Category]:
        """يجلب كل الأقسام الرئيسية المفعلة مرتبة حسب sort_order."""
        result = await session.execute(
            select(Category)
            .where(Category.is_active.is_(True))
            .order_by(Category.sort_order, Category.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all_categories(session) -> list[Category]:
        """يجلب كل الأقسام (للأدمن).

        مع تحميل مسبق للأقسام الفرعية، لأن قائمة الأدمن تعرض
        len(cat.sub_categories) والتحميل الكسول عبر AsyncSession
        يرفع MissingGreenlet.
        """
        result = await session.execute(
            select(Category)
            .options(selectinload(Category.sub_categories))
            .order_by(Category.sort_order, Category.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_category(session, category_id: int) -> Category | None:
        # Admin details render category.sub_categories; load it explicitly for
        # AsyncSession instead of triggering unsupported lazy IO.
        result = await session.execute(
            select(Category)
            .options(selectinload(Category.sub_categories))
            .where(Category.id == category_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_category(
        session,
        name_ar: str,
        emoji: str,
        category_type: CategoryType,
        sort_order: int = 0,
    ) -> Category:
        category = Category(
            name_ar=name_ar,
            emoji=emoji,
            type=category_type,
            sort_order=sort_order,
            is_active=True,
        )
        session.add(category)
        await session.commit()
        await session.refresh(category)
        return category

    @staticmethod
    async def update_category(
        session,
        category_id: int,
        **kwargs,
    ) -> Category | None:
        category = await session.get(Category, category_id)
        if category is None:
            return None
        for key, value in kwargs.items():
            if hasattr(category, key):
                setattr(category, key, value)
        await session.commit()
        await session.refresh(category)
        return category

    @staticmethod
    async def delete_category(session, category_id: int) -> bool:
        category = await session.get(Category, category_id)
        if category is None:
            return False
        await session.delete(category)
        await session.commit()
        return True

    # ══════════════ الأقسام الفرعية ══════════════

    @staticmethod
    async def get_active_sub_categories(session, category_id: int) -> list[SubCategory]:
        """يجلب الأقسام الفرعية المفعلة لقسم رئيسي معين."""
        result = await session.execute(
            select(SubCategory)
            .where(
                SubCategory.category_id == category_id,
                SubCategory.is_active.is_(True),
            )
            .order_by(SubCategory.sort_order, SubCategory.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all_sub_categories(session, category_id: int) -> list[SubCategory]:
        """يجلب كل الأقسام الفرعية لقسم رئيسي (للأدمن).

        مع تحميل مسبق للمنتجات، لأن قائمة الأدمن تعرض
        len(sub.products) والتحميل الكسول عبر AsyncSession
        يرفع MissingGreenlet.
        """
        result = await session.execute(
            select(SubCategory)
            .options(selectinload(SubCategory.products))
            .where(SubCategory.category_id == category_id)
            .order_by(SubCategory.sort_order, SubCategory.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_sub_category(session, sub_category_id: int) -> SubCategory | None:
        # Admin details read sub.products; eager-load it because lazy loading
        # through AsyncSession would raise MissingGreenlet at render time.
        result = await session.execute(
            select(SubCategory)
            .options(selectinload(SubCategory.products))
            .where(SubCategory.id == sub_category_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_sub_category(
        session,
        category_id: int,
        name_ar: str,
        emoji: str,
        description: str | None = None,
        sort_order: int = 0,
    ) -> SubCategory:
        sub = SubCategory(
            category_id=category_id,
            name_ar=name_ar,
            emoji=emoji,
            description=description,
            sort_order=sort_order,
            is_active=True,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        return sub

    @staticmethod
    async def update_sub_category(
        session,
        sub_category_id: int,
        **kwargs,
    ) -> SubCategory | None:
        sub = await session.get(SubCategory, sub_category_id)
        if sub is None:
            return None
        for key, value in kwargs.items():
            if hasattr(sub, key):
                setattr(sub, key, value)
        await session.commit()
        await session.refresh(sub)
        return sub

    @staticmethod
    async def delete_sub_category(session, sub_category_id: int) -> bool:
        sub = await session.get(SubCategory, sub_category_id)
        if sub is None:
            return False
        await session.delete(sub)
        await session.commit()
        return True

    # ══════════════ المنتجات ══════════════

    @staticmethod
    async def get_active_products(session, sub_category_id: int) -> list[Product]:
        """يجلب المنتجات المفعلة لقسم فرعي معين."""
        result = await session.execute(
            select(Product)
            .where(
                Product.sub_category_id == sub_category_id,
                Product.status == ProductStatus.ACTIVE,
            )
            .order_by(Product.sort_order, Product.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all_products(session, sub_category_id: int) -> list[Product]:
        """يجلب كل منتجات قسم فرعي (للأدمن)."""
        result = await session.execute(
            select(Product)
            .where(Product.sub_category_id == sub_category_id)
            .order_by(Product.sort_order, Product.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_product(session, product_id: int) -> Product | None:
        result = await session.execute(
            select(Product)
            .where(Product.id == product_id)
            .options(
                selectinload(Product.sub_category),
                selectinload(Product.api_provider),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_product(
        session,
        sub_category_id: int,
        name_ar: str,
        price_usd: Decimal,
        cost_price_usd: Decimal = Decimal("0"),
        api_provider_id: int | None = None,
        provider_service_id: str | None = None,
        provider_service_ref_id: int | None = None,
        fulfillment_type: ProductFulfillmentType = ProductFulfillmentType.API,
        description: str | None = None,
        min_quantity: int = 1,
        max_quantity: int = 1,
        requires_player_id: bool = False,
        requires_link: bool = False,
        requires_quantity: bool = False,
        sort_order: int = 0,
    ) -> Product:
        product = Product(
            sub_category_id=sub_category_id,
            name_ar=name_ar,
            price_usd=price_usd,
            cost_price_usd=cost_price_usd,
            api_provider_id=api_provider_id,
            provider_service_ref_id=provider_service_ref_id,
            provider_service_id=provider_service_id,
            fulfillment_type=fulfillment_type,
            description=description,
            min_quantity=min_quantity,
            max_quantity=max_quantity,
            requires_player_id=requires_player_id,
            requires_link=requires_link,
            requires_quantity=requires_quantity,
            sort_order=sort_order,
            status=ProductStatus.ACTIVE,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)
        return product

    @staticmethod
    async def update_product(
        session,
        product_id: int,
        **kwargs,
    ) -> Product | None:
        product = await session.get(Product, product_id)
        if product is None:
            return None
        for key, value in kwargs.items():
            if hasattr(product, key):
                setattr(product, key, value)
        await session.commit()
        await session.refresh(product)
        return product

    @staticmethod
    async def delete_product(session, product_id: int) -> bool:
        product = await session.get(Product, product_id)
        if product is None:
            return False
        await session.delete(product)
        await session.commit()
        return True

    @staticmethod
    async def increment_product_sold(session, product_id: int, quantity: int = 1) -> None:
        """يزيد عداد المبيعات عند كل عملية شراء ناجحة."""
        product = await session.get(Product, product_id)
        if product:
            product.total_sold = product.total_sold + quantity
            await session.commit()

    # ══════════════ مزودو API (ألعاب/SMM) ══════════════

    @staticmethod
    async def get_active_providers(
        session,
        provider_type: ApiProviderType | None = None,
    ) -> list[ApiProvider]:
        """يجلب المزودين المفعلين مرتبين حسب الأولوية."""
        query = select(ApiProvider).where(ApiProvider.is_active.is_(True))
        if provider_type:
            query = query.where(ApiProvider.type == provider_type)
        query = query.order_by(ApiProvider.priority, ApiProvider.id)
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_all_providers(session) -> list[ApiProvider]:
        """يجلب كل المزودين (للأدمن)."""
        result = await session.execute(
            select(ApiProvider).order_by(ApiProvider.type, ApiProvider.priority)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_provider(session, provider_id: int) -> ApiProvider | None:
        return await session.get(ApiProvider, provider_id)

    @staticmethod
    async def create_provider(
        session,
        name: str,
        provider_type: ApiProviderType,
        api_url: str,
        api_key: str,
        priority: int = 1,
        low_balance_threshold: Decimal = Decimal("10"),
    ) -> ApiProvider:
        provider = ApiProvider(
            name=name,
            type=provider_type,
            api_url=api_url,
            api_key=api_key,
            priority=priority,
            low_balance_threshold=low_balance_threshold,
            is_active=True,
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        return provider

    @staticmethod
    async def update_provider(
        session,
        provider_id: int,
        **kwargs,
    ) -> ApiProvider | None:
        provider = await session.get(ApiProvider, provider_id)
        if provider is None:
            return None
        for key, value in kwargs.items():
            if hasattr(provider, key):
                setattr(provider, key, value)
        await session.commit()
        await session.refresh(provider)
        return provider

    @staticmethod
    async def delete_provider(session, provider_id: int) -> bool:
        provider = await session.get(ApiProvider, provider_id)
        if provider is None:
            return False
        await session.delete(provider)
        await session.commit()
        return True

    # ══════════════ خدمات الأرقام الديناميكية ══════════════

    @staticmethod
    async def get_active_number_services(
        session,
    ) -> list[NumberService]:
        """يجلب خدمات الأرقام المفعلة مرتبة حسب sort_order."""
        result = await session.execute(
            select(NumberService)
            .where(NumberService.is_active.is_(True))
            .order_by(NumberService.sort_order, NumberService.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all_number_services(session) -> list[NumberService]:
        """يجلب كل خدمات الأرقام (للأدمن)."""
        result = await session.execute(
            select(NumberService).order_by(NumberService.sort_order, NumberService.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_number_service_by_code(session, code: str) -> NumberService | None:
        result = await session.execute(select(NumberService).where(NumberService.code == code))
        return result.scalar_one_or_none()

    @staticmethod
    async def create_number_service(
        session,
        code: str,
        name_ar: str,
        emoji: str,
        fivesim_code: str | None = None,
        herosms_code: str | None = None,
        sms_activate_code: str | None = None,
        smshub_code: str | None = None,
        sort_order: int = 0,
    ) -> NumberService:
        svc = NumberService(
            code=code,
            name_ar=name_ar,
            emoji=emoji,
            fivesim_code=fivesim_code,
            herosms_code=herosms_code,
            sms_activate_code=sms_activate_code,
            smshub_code=smshub_code,
            sort_order=sort_order,
            is_active=True,
        )
        session.add(svc)
        await session.commit()
        await session.refresh(svc)
        return svc

    @staticmethod
    async def update_number_service(
        session,
        service_id: int,
        **kwargs,
    ) -> NumberService | None:
        svc = await session.get(NumberService, service_id)
        if svc is None:
            return None
        for key, value in kwargs.items():
            if hasattr(svc, key):
                setattr(svc, key, value)
        await session.commit()
        await session.refresh(svc)
        return svc

    @staticmethod
    async def delete_number_service(session, service_id: int) -> bool:
        svc = await session.get(NumberService, service_id)
        if svc is None:
            return False
        await session.delete(svc)
        await session.commit()
        return True

    # ══════════════ باقات النجوم ══════════════

    @staticmethod
    async def get_active_stars_packages(
        session,
    ) -> list[StarsPackage]:
        """يجلب باقات النجوم المفعلة مرتبة حسب sort_order."""
        result = await session.execute(
            select(StarsPackage)
            .where(StarsPackage.is_active.is_(True))
            .order_by(StarsPackage.sort_order, StarsPackage.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_all_stars_packages(session) -> list[StarsPackage]:
        """يجلب كل باقات النجوم (للأدمن)."""
        result = await session.execute(
            select(StarsPackage).order_by(StarsPackage.sort_order, StarsPackage.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_stars_package(session, package_id: int) -> StarsPackage | None:
        return await session.get(StarsPackage, package_id)

    @staticmethod
    async def create_stars_package(
        session,
        stars_amount: int,
        usd_amount: Decimal,
        label: str,
        sort_order: int = 0,
    ) -> StarsPackage:
        pkg = StarsPackage(
            stars_amount=stars_amount,
            usd_amount=usd_amount,
            label=label,
            sort_order=sort_order,
            is_active=True,
        )
        session.add(pkg)
        await session.commit()
        await session.refresh(pkg)
        return pkg

    @staticmethod
    async def update_stars_package(
        session,
        package_id: int,
        **kwargs,
    ) -> StarsPackage | None:
        pkg = await session.get(StarsPackage, package_id)
        if pkg is None:
            return None
        for key, value in kwargs.items():
            if hasattr(pkg, key):
                setattr(pkg, key, value)
        await session.commit()
        await session.refresh(pkg)
        return pkg

    @staticmethod
    async def delete_stars_package(session, package_id: int) -> bool:
        pkg = await session.get(StarsPackage, package_id)
        if pkg is None:
            return False
        await session.delete(pkg)
        await session.commit()
        return True
