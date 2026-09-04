"""
خدمة البناء التلقائي لأقسام «الرشق» الداخلية.

المهمة:
- داخل كل تطبيق من تطبيقات قسم الرشق العشرة (إنستغرام، تيك توك، ...) تُنشأ
  تلقائياً أقسام داخلية حسب الخدمات المسحوبة من المزود: متابعون/لايكات/
  مشاهدات/تعليقات/...
- يُنشر في كل قسم داخلي أرخص ``max_per_section`` خدمات (افتراضياً 10)
  كمنتجات للبيع، بسعر = تكلفة المزود + هامش ربح.
- الخدمات بلا سعر (rate_usd ≤ 0) لا تُنشر إطلاقاً: كتالوجات المزودين
  تحوي أسطراً مثل «Server 1» أو عناوين أقسام تصل بسعر صفر، ونشرها
  يُنتج منتجات مجانية في المتجر.

الضمانات:
- Idempotent: إعادة التشغيل لا تكرر ولا تحذف منتجات الأدمن اليدوية.
- المنتجات التي ينشئها البناء تلقائياً وسمها ``is_auto_published``، وعند
  تغيّر قائمة الأرخص تُعطَّل (لا تُحذف) المنتجات التلقائية الخارجة من
  أول N، حتى يبقى كل قسم داخلي محدثاً بأرخص العروض.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, true
from sqlalchemy.orm import selectinload

from database.models import (
    ApiProvider,
    Category,
    CategoryType,
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductStatus,
    ProviderService,
    ProviderServiceStatus,
    SubCategory,
)
from services.feature_service import FeatureService
from services.pulled_services_service import PulledServicesService
from services.smm_catalog import (
    SMM_KIND_SPECS,
    SHORT_TO_PLATFORM,
    classify_smm_service,
    kind_meta,
    platform_meta,
)

logger = logging.getLogger(__name__)

FEATURE_KEY = "smm_auto_sections"
DEFAULT_MAX_PER_SECTION = 10
# القيمة القديمة (قبل الترقية إلى 10) — تُستخدم لترقية القواعد القديمة مرة واحدة.
LEGACY_MAX_PER_SECTION = 5
LIMIT_UPGRADE_SETTING = "smm_auto_sections_limit_upgraded_to_10"
DEFAULT_MARGIN_PERCENT = Decimal("50")


def _kind_sort_index(kind_key: str) -> int:
    for index, kind in enumerate(SMM_KIND_SPECS, start=1):
        if kind.key == kind_key:
            return index
    return 100


def is_sellable_service(service) -> bool:
    """هل الخدمة المسحوبة قابلة للنشر كمنتج؟

    كتالوجات مزودي الرشق مليئة بمداخل ليست خدمات فعلية: عناوين أقسام،
    وأسطر «Server 1 / سيرفر 2»، وخدمات معطّلة مؤقتاً — وكلها تصل بسعر
    صفر أو شبه صفر. نشرها يُنتج منتجات بسعر 0$ يشتريها الزبون مجاناً.

    القاعدتان:
    1. لا سعر (rate_usd ≤ 0) = لا نشر.
    2. اسمها فيه «سيرفر/Server» وسعرها شبه صفر = سطر كتالوج لا خدمة = لا نشر.
    """
    from services.junk_products_service import is_junk_service

    try:
        rate = Decimal(str(getattr(service, "rate_usd", 0) or 0))
    except Exception:
        return False
    if not rate.is_finite() or rate <= 0:
        return False
    # «سيرفر N» بسعر شبه صفر ليست خدمة قابلة للبيع مهما بدت مسعّرة.
    return not is_junk_service(service)


class SmmSectionsService:
    """يبني أقسام الرشق الداخلية وينشر أرخص الخدمات المسحوبة فيها."""

    @staticmethod
    async def auto_build_enabled() -> bool:
        """هل البناء التلقائي مفعّل ويُشغَّل عند إقلاع البوت؟"""
        if not await FeatureService.enabled(FEATURE_KEY, default=False):
            return False
        return await FeatureService.config_bool(FEATURE_KEY, "auto_on_startup", True)

    @staticmethod
    async def _margin() -> Decimal:
        raw = await FeatureService.config(FEATURE_KEY, "margin_percent", 50)
        try:
            margin = Decimal(str(raw))
            if margin <= 0:
                raise ValueError
        except Exception:
            margin = DEFAULT_MARGIN_PERCENT
        return margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    async def _limit() -> int:
        try:
            value = int(
                await FeatureService.config(FEATURE_KEY, "max_per_section", DEFAULT_MAX_PER_SECTION)
            )
            if value <= 0:
                raise ValueError
        except (TypeError, ValueError):
            value = DEFAULT_MAX_PER_SECTION
        return value

    @classmethod
    async def limit(cls) -> int:
        """عدد الخدمات الأرخص المنشورة في كل قسم داخلي (للعرض في اللوحة)."""
        return await cls._limit()

    @classmethod
    async def upgrade_legacy_limit(cls) -> bool:
        """يرفع الحد المحفوظ من 5 (القديم) إلى 10 مرة واحدة فقط.

        القواعد المُنشأة قبل هذا التحديث خزّنت ``max_per_section = 5`` في
        إعدادات الميزة، فلا يكفي تغيير الافتراضي في السجل. نرفعها مرة
        واحدة ونضع علامة في الإعدادات حتى لا نتجاوز اختيار الأدمن لاحقاً.
        """
        from database.engine import async_session_maker
        from services.settings_service import SettingsService

        try:
            if await SettingsService.get_bool(LIMIT_UPGRADE_SETTING, False):
                return False
            current = await cls._limit()
            async with async_session_maker() as session:
                if current == LEGACY_MAX_PER_SECTION:
                    await FeatureService.set_option(
                        session, FEATURE_KEY, "max_per_section", DEFAULT_MAX_PER_SECTION
                    )
                await SettingsService.set(session, LIMIT_UPGRADE_SETTING, "true")
            return current == LEGACY_MAX_PER_SECTION
        except Exception:
            logger.exception("تعذّرت ترقية حد «أرخص N» لأقسام الرشق")
            return False

    @classmethod
    async def build(cls, session, provider_id: int | None = None) -> dict:
        """ينفّذ البناء/التحديث الكامل ويعيد تقريراً بالأرقام.

        آمن للتكرار: يُنشئ الناقص فقط، ويعطّل المنتجات التلقائية الخارجة
        من أول N أرخص، ولا يمسّ منتجات نشرها الأدمن يدوياً.

        ``provider_id`` لتشغيل البناء لمزود واحد فقط (من شاشة «الخدمات
        المسحوبة حسب المزود»). عند تمريره يُلمس فقط كتالوج ذلك المزود —
        لا تُعطَّل منتجات مزود آخر، ولا تُسحب خدمات أخرى.
        """
        report: dict = {
            "apps": 0,
            "sections_created": 0,
            "products_created": 0,
            "products_reactivated": 0,
            "products_deactivated": 0,
            "reordered": 0,
            "skipped_existing": 0,
            "skipped_unpriced": 0,
            "errors": 0,
        }
        touched_apps: set[int] = set()

        limit = await cls._limit()
        margin = await cls._margin()

        # 1) قسم الرشق وتطبيقاته.
        category = (
            await session.execute(
                select(Category).where(Category.type == CategoryType.SMM)
            )
        ).scalar_one_or_none()
        if category is None:
            logger.warning("قسم الرشق غير موجود — لن يُبنى شيء تلقائياً.")
            report["errors"] = 1
            return report

        apps = list(
            (
                await session.execute(
                    select(SubCategory).where(
                        SubCategory.category_id == category.id,
                        SubCategory.parent_sub_category_id.is_(None),
                    )
                )
            ).scalars().all()
        )

        def _find_app(platform_short: str) -> SubCategory | None:
            app_name = SHORT_TO_PLATFORM.get(platform_short)
            if not app_name:
                return None
            from services.smm_catalog import resolve_smm_app

            for app in apps:
                haystack = f"{app.emoji or ''} {app.name_ar or ''}"
                resolved = resolve_smm_app(haystack) or resolve_smm_app(app.name_ar or "")
                if resolved is not None and resolved.name_ar == app_name:
                    return app
            return None

        # 2) الخدمات المسحوبة المفعلة (مع مزود مفعّل) مجمّعة (منصة، نوع).
        result = await session.execute(
            select(ProviderService)
            .options(selectinload(ProviderService.api_provider))
            .where(ProviderService.status == ProviderServiceStatus.ACTIVE)
            .where(
                ProviderService.api_provider_id == provider_id if provider_id else true()
            )
        )
        grouped: dict[tuple[str, str], list[ProviderService]] = defaultdict(list)
        for service in result.scalars().all():
            provider: ApiProvider | None = service.api_provider
            if provider is None or not provider.is_active:
                continue
            if not is_sellable_service(service):
                # خدمات بلا سعر (سطر «سيرفر 1»، عناوين أقسام، خدمات موقوفة)
                # لا تُنشر أبداً حتى لا يظهر منتج بسعر 0$ في المتجر.
                report["skipped_unpriced"] += 1
                continue
            platform_key, kind_key = PulledServicesService.classify(service)
            if platform_key in ("other",) or kind_key in ("other",):
                continue  # لا ننشر خدمات غير مصنفة داخل أقسام الرشق
            grouped[(platform_key, kind_key)].append(service)

        for (platform_key, kind_key), services in grouped.items():
            try:
                services.sort(key=lambda s: (Decimal(str(s.rate_usd or 0)), s.id))
                app_sub = _find_app(platform_key)
                if app_sub is None:
                    # تطبيق مفقود من القسم (قاعدة قديمة) → ننشئه بالاسم المعياري.
                    emoji, label = platform_meta(platform_key)
                    app_sub = SubCategory(
                        category_id=category.id,
                        parent_sub_category_id=None,
                        kind_key=None,
                        name_ar=label[:64],
                        emoji=emoji,
                        description=f"منتجات {label}",
                        is_active=True,
                        sort_order=10,
                    )
                    session.add(app_sub)
                    await session.flush()
                    apps.append(app_sub)

                # 3) ضمان وجود القسم الداخلي للنوع.
                section = (
                    await session.execute(
                        select(SubCategory).where(
                            SubCategory.category_id == category.id,
                            SubCategory.parent_sub_category_id == app_sub.id,
                            SubCategory.kind_key == kind_key,
                        )
                    )
                ).scalar_one_or_none()
                if section is None:
                    # قسم داخلي أُنشئ يدوياً بنفس الاسم؟ نتبناه بدل إنشاء ثانٍ.
                    from services.smm_catalog import normalize_label

                    canonical_label = next(
                        (
                            normalize_label(k.name_ar)
                            for k in SMM_KIND_SPECS
                            if k.key == kind_key
                        ),
                        None,
                    )
                    siblings = list(
                        (
                            await session.execute(
                                select(SubCategory).where(
                                    SubCategory.category_id == category.id,
                                    SubCategory.parent_sub_category_id == app_sub.id,
                                    SubCategory.kind_key.is_(None),
                                )
                            )
                        ).scalars().all()
                    )
                    for sibling in siblings:
                        if (
                            canonical_label
                            and normalize_label(sibling.name_ar or "") == canonical_label
                        ):
                            section = sibling
                            section.kind_key = kind_key
                            break

                if section is None:
                    emoji, label = kind_meta(kind_key)
                    kind_name = next(
                        (k.name_ar for k in SMM_KIND_SPECS if k.key == kind_key), label
                    )
                    section = SubCategory(
                        category_id=category.id,
                        parent_sub_category_id=app_sub.id,
                        kind_key=kind_key,
                        name_ar=kind_name[:64],
                        emoji=emoji,
                        description=f"خدمات رشق {kind_name} {app_sub.name_ar}",
                        is_active=True,
                        sort_order=_kind_sort_index(kind_key) * 10,
                    )
                    session.add(section)
                    await session.flush()
                    report["sections_created"] += 1
                    report["apps"] = max(report["apps"], 1)

                # 4) المنتجات: أول N أرخص، بدون تكرار.
                touched_apps.add(app_sub.id)
                desired_refs: set[int] = set()
                for position, service in enumerate(services[:limit]):
                    desired_refs.add(service.id)
                    existing = (
                        await session.execute(
                            select(Product).where(
                                Product.provider_service_ref_id == service.id,
                                Product.sub_category_id == section.id,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        if existing.is_auto_published:
                            if existing.status != ProductStatus.ACTIVE:
                                existing.status = ProductStatus.ACTIVE
                                report["products_reactivated"] += 1
                            existing.sort_order = position * 10
                            report["reordered"] += 1
                        else:
                            report["skipped_existing"] += 1
                        continue

                    sell_price = (
                        Decimal(str(service.rate_usd or 0))
                        * (Decimal("100") + margin)
                        / Decimal("100")
                    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                    # الاسم بالعربية دائماً: الاسم العربي المحفوظ وقت السحب
                    # (وللسجلات القديمة تعريب على الطايرة) — "TikTok Real
                    # Followers 1000" يصبح "متابعون حقيقي تيك توك (1000)".
                    from services.service_localization_service import service_name_ar

                    product_name = service_name_ar(service)[:128]
                    if not product_name:
                        _, label = kind_meta(kind_key)
                        product_name = f"{label} {app_sub.name_ar}"[:128]
                    product = Product(
                        sub_category_id=section.id,
                        api_provider_id=service.api_provider_id,
                        provider_service_ref_id=service.id,
                        provider_service_id=service.external_service_id,
                        name_ar=product_name,
                        description=(service.description or service.category or "")[:500] or None,
                        price_usd=sell_price,
                        cost_price_usd=Decimal(str(service.rate_usd or 0)),
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
                        sort_order=position * 10,
                        status=ProductStatus.ACTIVE,
                        is_auto_published=True,
                    )
                    session.add(product)
                    report["products_created"] += 1

                # 5) تعطيل المنتجات التلقائية الخارجة عن أول N (تبقى للاسترجاع).
                auto_products = list(
                    (
                        await session.execute(
                            select(Product).where(
                                Product.sub_category_id == section.id,
                                Product.is_auto_published.is_(True),
                                (
                                    Product.api_provider_id == provider_id
                                    if provider_id
                                    else (Product.api_provider_id.is_not(None))
                                ),
                            )
                        )
                    ).scalars().all()
                )
                for product in auto_products:
                    ref = product.provider_service_ref_id
                    if ref not in desired_refs and product.status == ProductStatus.ACTIVE:
                        product.status = ProductStatus.INACTIVE
                        report["products_deactivated"] += 1
            except Exception:
                logger.exception("فشل بناء قسم رشق (%s/%s)", platform_key, kind_key)
                report["errors"] += 1

        try:
            await session.commit()
        except Exception:
            logger.exception("فشل حفظ بناء أقسام الرشق")
            await session.rollback()
            report["errors"] += 1
        report["apps"] = len(touched_apps)
        return report
