"""🧹 تنظيف منتجات «سيرفر» الوهمية من أقسام الرشق واستبدالها ببدائل مسعّرة.

المشكلة التي تعالجها هذه الخدمة
────────────────────────────────
كتالوجات مزودي الرشق تحوي أسطراً ليست خدمات حقيقية: عناوين أقسام وفواصل
مثل ``Server 1`` / ``سيرفر 2`` / ``Instagram Followers [Server 3]``، وتصل
غالباً بسعر صفر أو شبه صفر. عندما تُنشر تظهر للزبون منتجات مثل:

    «متابعين انستجرام سيرفر 1» — 0$
    «لايكات تيك توك سيرفر 2»  — 0$

وهي غير قابلة للتنفيذ فعلياً (المزود لا يقبل طلباً عليها) وشراؤها بسعر صفر
خسارة صافية. المطلوب: حذفها من **كل** تطبيقات الرشق وكل أقسامها الفرعية،
واستبدالها بخدمات لها سعر صحيح من نفس المزود ونفس القسم.

القواعد
───────
منتج يُعتبر «وهمياً» عندما:

1. اسمه يحوي كلمة سيرفر (``سيرفر`` أو ``server`` أو ``srv``) — بأي صيغة
   عربية/إنجليزية وبين أقواس أو بدونها؛ **و**
2. سعره شبه صفر: ``price_usd <= JUNK_PRICE_THRESHOLD`` (افتراضياً 0.05$)،
   أو تكلفته شبه صفر (فلا يمكن حساب ربح صحيح عليه).

الاستبدال
─────────
لكل قسم فرعي حُذف منه منتج وهمي، نبحث عن أرخص الخدمات المسحوبة **المسعّرة**
(rate_usd فوق العتبة) من نفس المنصة/النوع، وننشرها بسعر = التكلفة + هامش
القسم الفعّال. لا نلمس المنتجات اليدوية ولا المنتجات السليمة.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from database.models import (
    Category,
    CategoryType,
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductPricingType,
    ProductStatus,
    ProviderService,
    ProviderServiceStatus,
    SubCategory,
)
from services.margin_service import MarginService
from services.pulled_services_service import PulledServicesService

logger = logging.getLogger(__name__)


# السعر الذي نعتبر ما دونه «شبه صفر» — منتج بهذا السعر ليس خدمة حقيقية.
JUNK_PRICE_THRESHOLD = Decimal("0.05")

# عدد البدائل التي تُنشر كحد أقصى في القسم الواحد بدل المحذوفات.
DEFAULT_REPLACEMENT_LIMIT = 10

# كلمة «سيرفر» بكل صيغها: عربية (سيرفر/السيرفر/سرفر) وإنجليزية
# (server/servers/srv/svr) مع أرقام لاحقة اختيارية.
_SERVER_WORD_RE = re.compile(
    r"(?:^|[\s\[\(\{\-–—_|/•·،,:])"          # بداية أو فاصل قبل الكلمة
    r"(?:ال)?"                                 # «ال» التعريف اختيارية
    r"(?:س[يی]?رفر|سرفر|servers?|serv|srv|svr)"
    r"(?:$|[\s\]\)\}\-–—_|/•·،,:.]|\d)",       # نهاية أو فاصل/رقم بعدها
    re.IGNORECASE,
)


def _decimal(value) -> Decimal:
    try:
        number = Decimal(str(value or 0))
    except Exception:
        return Decimal("0")
    return number if number.is_finite() else Decimal("0")


def has_server_word(name: str | None) -> bool:
    """هل يحوي الاسم كلمة «سيرفر» (عربي/إنجليزي) كوحدة مستقلة؟

    ``"متابعين انستجرام سيرفر 1"`` → True
    ``"Instagram Followers [Server 2]"`` → True
    ``"خدمة مميزة"`` → False — ولا نلتقط كلمات تحوي الحروف عرضاً.
    """
    text = (name or "").strip()
    if not text:
        return False
    return bool(_SERVER_WORD_RE.search(text))


def is_junk_price(
    price, cost=None, threshold: Decimal = JUNK_PRICE_THRESHOLD
) -> bool:
    """هل السعر صفر أو شبه صفر (فالمنتج غير قابل للبيع بربح)؟"""
    sell = _decimal(price)
    if sell <= threshold:
        return True
    if cost is not None and _decimal(cost) <= 0:
        # سعر بيع بلا تكلفة معروفة = لا ربح محسوب ولا تنفيذ مضمون.
        return True
    return False


def is_junk_product(product, threshold: Decimal = JUNK_PRICE_THRESHOLD) -> bool:
    """منتج «سيرفر» وهمي: اسمه فيه «سيرفر» وسعره شبه صفر."""
    if not has_server_word(getattr(product, "name_ar", "")):
        return False
    return is_junk_price(
        getattr(product, "price_usd", 0),
        getattr(product, "cost_price_usd", 0),
        threshold,
    )


def is_junk_service(service, threshold: Decimal = JUNK_PRICE_THRESHOLD) -> bool:
    """خدمة مزود مسحوبة وهمية: «Server N» بسعر شبه صفر — لا تُنشر أبداً."""
    rate = _decimal(getattr(service, "rate_usd", 0))
    if rate <= 0:
        return True
    if rate <= threshold and has_server_word(getattr(service, "name", "")):
        return True
    if rate <= threshold and has_server_word(getattr(service, "name_ar", "")):
        return True
    return False


@dataclass
class CleanupReport:
    """تقرير عملية التنظيف والاستبدال."""

    scanned: int = 0
    deleted: int = 0
    replaced: int = 0
    sections: int = 0
    no_replacement: int = 0
    errors: int = 0
    deleted_names: list[str] = field(default_factory=list)
    replaced_names: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.deleted or self.replaced)


class JunkProductsService:
    """كشف/حذف منتجات «سيرفر» الوهمية واستبدالها ببدائل مسعّرة."""

    THRESHOLD = JUNK_PRICE_THRESHOLD

    # ─────────────── الكشف ───────────────

    @staticmethod
    def _name_filter():
        """فلتر SQL مبدئي: الأسماء التي قد تحوي «سيرفر».

        يقلّل الصفوف المحمّلة فقط؛ التأكيد النهائي يتم بـ ``is_junk_product``
        (فهي وحدها التي تفهم حدود الكلمة).
        """
        patterns = ("%سيرفر%", "%سرفر%", "%server%", "%Server%", "%SERVER%", "%srv%")
        return or_(*[Product.name_ar.ilike(pattern) for pattern in patterns])

    @classmethod
    async def find_in_subs(
        cls, session, sub_ids: list[int], threshold: Decimal | None = None
    ) -> list[Product]:
        """المنتجات الوهمية داخل مجموعة أقسام فرعية."""
        if not sub_ids:
            return []
        threshold = threshold if threshold is not None else cls.THRESHOLD
        result = await session.execute(
            select(Product).where(
                Product.sub_category_id.in_(sub_ids),
                cls._name_filter(),
            )
        )
        return [
            product
            for product in result.scalars().all()
            if is_junk_product(product, threshold)
        ]

    @classmethod
    async def smm_sub_ids(cls, session) -> list[int]:
        """كل الأقسام الفرعية داخل كل أقسام الرشق (تطبيقات + أقسامها)."""
        result = await session.execute(
            select(SubCategory.id)
            .join(Category, Category.id == SubCategory.category_id)
            .where(Category.type == CategoryType.SMM)
        )
        return [int(row) for row in result.scalars().all()]

    @classmethod
    async def find_all_smm(
        cls, session, threshold: Decimal | None = None
    ) -> list[Product]:
        """كل منتجات «سيرفر» الوهمية في قسم الرشق كله."""
        return await cls.find_in_subs(
            session, await cls.smm_sub_ids(session), threshold
        )

    @classmethod
    async def count_all_smm(cls, session, threshold: Decimal | None = None) -> int:
        return len(await cls.find_all_smm(session, threshold))

    @classmethod
    async def count_in_tree(
        cls, session, sub_id: int, *, include_children: bool = True
    ) -> int:
        """عدد المنتجات الوهمية في قسم (وأقسامه الداخلية)."""
        from services.product_service import ProductService

        ids = (
            await ProductService.subcategory_tree_ids(session, sub_id)
            if include_children
            else [sub_id]
        )
        return len(await cls.find_in_subs(session, ids))

    # ─────────────── البدائل ───────────────

    @classmethod
    async def _candidate_services(
        cls, session, sub: SubCategory, limit: int
    ) -> list[ProviderService]:
        """أرخص الخدمات المسحوبة المسعّرة المناسبة لهذا القسم.

        المطابقة على (المنصة، النوع) المستخلصين من التطبيق الأب وقسم النوع،
        وتُستثنى الخدمات الوهمية والخدمات المنشورة في القسم أصلاً.
        """
        parent = None
        if sub.parent_sub_category_id:
            parent = await session.get(SubCategory, sub.parent_sub_category_id)

        from services.smm_catalog import PLATFORM_SHORT_KEYS, resolve_smm_app

        platform_key = None
        app_source = parent or sub
        resolved = resolve_smm_app(
            f"{app_source.emoji or ''} {app_source.name_ar or ''}"
        ) or resolve_smm_app(app_source.name_ar or "")
        if resolved is not None:
            platform_key = PLATFORM_SHORT_KEYS.get(resolved.name_ar)
        kind_key = sub.kind_key

        published = set(
            (
                await session.execute(
                    select(Product.provider_service_ref_id).where(
                        Product.sub_category_id == sub.id,
                        Product.provider_service_ref_id.is_not(None),
                    )
                )
            ).scalars().all()
        )

        result = await session.execute(
            select(ProviderService)
            .options(selectinload(ProviderService.api_provider))
            .where(ProviderService.status == ProviderServiceStatus.ACTIVE)
        )
        candidates: list[ProviderService] = []
        for service in result.scalars().all():
            if service.id in published:
                continue
            provider = service.api_provider
            if provider is None or not provider.is_active:
                continue
            if is_junk_service(service, cls.THRESHOLD):
                continue
            service_platform, service_kind = PulledServicesService.classify(service)
            if platform_key and service_platform != platform_key:
                continue
            if kind_key and service_kind != kind_key:
                continue
            candidates.append(service)

        candidates.sort(key=lambda s: (_decimal(s.rate_usd), s.id))
        return candidates[:limit]

    @classmethod
    async def _publish_replacement(
        cls, session, sub: SubCategory, service: ProviderService, position: int
    ) -> Product | None:
        """ينشر خدمة مسعّرة كمنتج بديل داخل القسم بسعر = التكلفة + الهامش."""
        from services.service_localization_service import service_name_ar

        cost = _decimal(service.rate_usd)
        if cost <= 0:
            return None
        percent, _source = await MarginService.resolve_sub_margin(session, sub)
        sell = (
            cost * (Decimal("100") + Decimal(str(percent))) / Decimal("100")
        ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if sell <= cls.THRESHOLD:
            return None

        name = (service_name_ar(service) or service.name or "خدمة رشق")[:128]
        product = Product(
            sub_category_id=sub.id,
            api_provider_id=service.api_provider_id,
            provider_service_ref_id=service.id,
            provider_service_id=service.external_service_id,
            name_ar=name,
            description=(service.description or service.category or "")[:500] or None,
            estimated_time="1 - 25 دقيقة",
            price_usd=sell,
            cost_price_usd=cost,
            pricing_type=ProductPricingType.MARGIN_PERCENT,
            profit_margin_percent=Decimal(str(percent)),
            margin_manual=False,
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
        return product

    # ─────────────── التنفيذ ───────────────

    @classmethod
    async def clean(
        cls,
        session,
        sub_ids: list[int] | None = None,
        *,
        replace: bool = True,
        replacement_limit: int = DEFAULT_REPLACEMENT_LIMIT,
    ) -> CleanupReport:
        """يحذف منتجات «سيرفر» الوهمية ثم ينشر بدائل مسعّرة مكانها.

        ``sub_ids = None`` يعني «كل قسم الرشق بكل تطبيقاته وأقسامه».
        """
        report = CleanupReport()
        if sub_ids is None:
            sub_ids = await cls.smm_sub_ids(session)
        if not sub_ids:
            return report

        junk = await cls.find_in_subs(session, sub_ids)
        report.scanned = len(junk)
        if not junk:
            return report

        # كم منتجاً وهمياً حُذف من كل قسم — لننشر نفس العدد بدائل.
        removed_per_sub: dict[int, int] = {}
        for product in junk:
            try:
                removed_per_sub[product.sub_category_id] = (
                    removed_per_sub.get(product.sub_category_id, 0) + 1
                )
                if len(report.deleted_names) < 20:
                    report.deleted_names.append(product.name_ar)
                await session.delete(product)
                report.deleted += 1
            except Exception:
                logger.exception("فشل حذف منتج سيرفر وهمي %s", product.id)
                report.errors += 1

        try:
            await session.flush()
        except Exception:
            logger.exception("فشل حذف منتجات سيرفر الوهمية")
            await session.rollback()
            report.errors += 1
            report.deleted = 0
            return report

        report.sections = len(removed_per_sub)

        if replace:
            for sub_id, removed in removed_per_sub.items():
                sub = await session.get(SubCategory, sub_id)
                if sub is None:
                    continue
                wanted = min(removed, replacement_limit)
                try:
                    services = await cls._candidate_services(session, sub, wanted)
                except Exception:
                    logger.exception("فشل جلب بدائل القسم %s", sub_id)
                    report.errors += 1
                    continue
                if not services:
                    report.no_replacement += 1
                    continue
                for position, service in enumerate(services):
                    product = await cls._publish_replacement(
                        session, sub, service, position
                    )
                    if product is not None:
                        report.replaced += 1
                        if len(report.replaced_names) < 20:
                            report.replaced_names.append(product.name_ar)
                if len(services) < wanted:
                    report.no_replacement += 1

        try:
            await session.commit()
        except Exception:
            logger.exception("فشل حفظ تنظيف منتجات سيرفر الوهمية")
            await session.rollback()
            report.errors += 1
        return report

    @classmethod
    async def clean_tree(
        cls,
        session,
        sub_id: int,
        *,
        include_children: bool = True,
        replace: bool = True,
    ) -> CleanupReport:
        """ينظّف قسماً واحداً (وأقسامه الداخلية)."""
        from services.product_service import ProductService

        ids = (
            await ProductService.subcategory_tree_ids(session, sub_id)
            if include_children
            else [sub_id]
        )
        return await cls.clean(session, ids, replace=replace)
