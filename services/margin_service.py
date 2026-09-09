"""
هوامش الربح متعددة المستويات:

    المنتج ← القسم الفرعي (والسلسلة الداخلية) ← القسم الرئيسي ← الهامش العالمي

- كل مستوى (منتج/قسم فرعي/قسم) يمكن ضبط هامش خاص به؛ ما لم يُضبط
  يُرث المستوى الأعلى، وآخر المستويات هو ``default_profit_margin_percent``.
- تغيير هامش قسم يعيد حساب أسعار كل منتجاته «التي بلا هامش خاص»
  تلقائياً (cost × (1 + margin/100))، والمنتجات التي ضبطها الأدمن
  بسنر/هامش خاص تبقى كما هي (المنتج له الأولوية دائماً).
- خدمات الرشق: عند النشر تُحفظ نسبة الهامش الضمنية المستخلصة من
  (سعر البيع مقابل التكلفة) حتى يصبح هامش الخدمة ظاهراً وقابلاً
  للتعديل — فلا يبقى قسم بلا هامش مطبق.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from database.models import Category, Product, ProductPricingType, ProviderService, SubCategory
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

GLOBAL_KEY = "default_profit_margin_percent"

# حدود منطقية لهامش القسم/المنتج (نفس حدود هامش الأرقام)
MIN_MARGIN = Decimal("-95")
MAX_MARGIN = Decimal("1000")


def format_percent(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{Decimal(str(value))}%"


class MarginService:
    # حدود الهامش متاحة كسمات صنف أيضاً (تستخدمها لوحة الأدمن للتحقق).
    MIN_MARGIN: Decimal = MIN_MARGIN
    MAX_MARGIN: Decimal = MAX_MARGIN

    # ─────────── الحل (التحديد) ───────────

    @staticmethod
    async def global_percent() -> Decimal:
        return await SettingsService.get_decimal(GLOBAL_KEY, Decimal("50"))

    @classmethod
    async def _sub_chain(cls, session, sub: SubCategory) -> list[SubCategory]:
        """
        القسم الفرعي ثم آباؤه الداخليون صعوداً حتى جذر القسم.
        يُحمَّل كل أقسام القسم الرئيسي دفعة واحدة (بما فيها الجذور)
        حتى يصل المشي الصاعد إلى الجذر.
        """
        from sqlalchemy.orm import selectinload

        result = await session.execute(
            select(SubCategory)
            .options(selectinload(SubCategory.category))
            .where(SubCategory.category_id == sub.category_id)
        )
        by_id = {row.id: row for row in result.scalars().unique().all()}

        chain: list[SubCategory] = []
        seen: set[int] = set()
        current: SubCategory | None = sub
        while current is not None and current.id not in seen:
            seen.add(current.id)
            chain.append(current)
            parent_id = current.parent_sub_category_id
            current = by_id.get(parent_id) if parent_id else None
        return chain

    @classmethod
    async def resolve_product_margin(
        cls, session, product: Product
    ) -> tuple[Decimal, str]:
        """هوامش المنتج الفعّالة (النسبة، مصدرها).

        الهامش الموجود على المنتج يُحترم فقط إذا ضبطه الأدمن يدوياً
        (``margin_manual=True``)؛ أما الهامش الضمني من السحب فيخضع لهامش
        قسمه (القسم الفرعي/الداخلي ثم القسم الرئيسي ثم العالمي).
        """
        if product.profit_margin_percent is not None and getattr(product, "margin_manual", False):
            return Decimal(str(product.profit_margin_percent)), "منتج"

        sub = await session.get(SubCategory, product.sub_category_id)
        if sub is not None:
            for row in await cls._sub_chain(session, sub):
                if row.profit_margin_percent is not None:
                    return (
                        Decimal(str(row.profit_margin_percent)),
                        f"قسم فرعي ({row.name_ar})",
                    )

            category = sub.category
            if category is not None and category.profit_margin_percent is not None:
                return (
                    Decimal(str(category.profit_margin_percent)),
                    f"قسم ({category.name_ar})",
                )

        return await cls.global_percent(), "عالمي"

    @classmethod
    async def effective_margin(
        cls, session, product: Product, server=None
    ) -> tuple[Decimal, str]:
        """الهامش الفعّال مع أولوية: منتج > قسم فرعي > قسم > سيرفر > عالمي."""
        percent, source = await cls.resolve_product_margin(session, product)
        if source != "عالمي":
            return Decimal(str(percent)), source
        if server is not None and getattr(server, "margin_percent", None) is not None:
            return Decimal(str(server.margin_percent)), "سيرفر"
        return Decimal(str(percent)), "عالمي"

    @classmethod
    async def product_sell_price(
        cls, session, product: Product, server=None, default_price=None
    ) -> Decimal:
        """سعر البيع النهائي حسب أولوية الهوامش.

        يطبّق حساب التكلفة عندما يتوفر هامش فعلي (منتج/قسم فرعي/قسم/سيرفر)؛
        وإلا يُبقي السعر المحفوظ (كأنه بلا هامش مخصص) حتى لا يغيّر
        أسعار منتجاتٍ جاهزة بلا إرادة الأدمن.
        """
        percent, source = await cls.effective_margin(session, product, server)

        # المنتجات المربوطة بخدمة مزود (SMM/ألعاب/تطبيقات): نقرأ تكلفة المزود
        # اللحظية بدل `cost_price_usd` المخزّن، فتبقى الأسعار في القائمة
        # والشراء محدّثة تلقائياً مع تغيّر أسعار المزود (إصلاح «الأسعار عالقة»).
        live_cost = None
        if getattr(product, "provider_service_ref_id", None):
            svc = await session.get(ProviderService, product.provider_service_ref_id)
            if svc is not None and svc.rate_usd is not None and svc.rate_usd > 0:
                live_cost = Decimal(str(svc.rate_usd))

        if live_cost is not None:
            return cls.price_from_cost(live_cost, percent)

        if source == "عالمي":
            if default_price is not None:
                return Decimal(str(default_price))
            return Decimal(str(getattr(product, "price_usd", 0) or 0))
        cost = Decimal(str(getattr(product, "cost_price_usd", 0) or 0))
        if cost <= 0:
            return Decimal(str(default_price if default_price is not None else getattr(product, "price_usd", 0) or 0))
        return cls.price_from_cost(cost, percent)

    @classmethod
    async def resolve_sub_margin(
        cls, session, sub: SubCategory
    ) -> tuple[Decimal, str]:
        """هوامش القسم الفرعي الفعّالة (بدون منتج)."""
        for row in await cls._sub_chain(session, sub):
            if row.profit_margin_percent is not None:
                return Decimal(str(row.profit_margin_percent)), f"قسم فرعي ({row.name_ar})"
        category = sub.category
        if category is not None and category.profit_margin_percent is not None:
            return Decimal(str(category.profit_margin_percent)), f"قسم ({category.name_ar})"
        return await cls.global_percent(), "عالمي"

    # ─────────── التطبيق ───────────

    @staticmethod
    def price_from_cost(cost: Decimal, margin_percent: Decimal) -> Decimal:
        if cost <= 0:
            return cost
        return (cost * (Decimal("1") + margin_percent / Decimal("100"))).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

    @staticmethod
    def implicit_margin(cost: Decimal, sell: Decimal) -> Decimal | None:
        """الهامش الضمني المستخلص من (التكلفة، سعر البيع)."""
        if cost <= 0 or sell <= 0:
            return None
        margin = (sell / cost - Decimal("1")) * Decimal("100")
        return margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    def is_margin_priced(cls, product: Product) -> bool:
        """هل سعرة مشتق من هامش (وليس ثبات يدوي صريح)؟"""
        return (
            product.pricing_type == ProductPricingType.MARGIN_PERCENT
            and (product.cost_price_usd or Decimal("0")) > 0
        )

    @classmethod
    async def recalc_product_from_margin(
        cls, session, product: Product, percent: Decimal | None = None
    ) -> bool:
        """
        يعيد حساب سعر منتج حسب الهامش المعطى (أو المُحدد للمنتج).
        يُطبَّق فقط على منتجات مسعّرة بهامش ولكلفة > 0.
        يعيد True إن تغيّر السعر.
        """
        if percent is None:
            percent = product.profit_margin_percent
        if percent is None:
            return False
        if not cls.is_margin_priced(product):
            return False
        new_price = cls.price_from_cost(
            Decimal(str(product.cost_price_usd)), Decimal(str(percent))
        )
        if new_price == Decimal(str(product.price_usd)):
            return False
        product.price_usd = new_price
        return True

    @staticmethod
    def recalc_forced_from_margin(product: Product, percent: Decimal | None) -> bool:
        """يعيد حساب المنتج من هامشه حتى لو كان سعره يدوياً.

        يستخدمه تغيير هامش القسم/القسم الفرعي ليطبّق الهامش على كل
        المنتجات غير اليدوية (لا يتجاوز هامشاً ضبطه الأدمن يدوياً).
        """
        if percent is None:
            return False
        cost = Decimal(str(product.cost_price_usd or 0))
        if cost <= 0:
            return False
        new_price = MarginService.price_from_cost(cost, Decimal(str(percent)))
        product.pricing_type = ProductPricingType.MARGIN_PERCENT
        product.profit_margin_percent = Decimal(str(percent))
        if new_price == Decimal(str(product.price_usd)):
            return False
        product.price_usd = new_price
        return True

    @classmethod
    async def cascade_subtree(cls, session, sub: SubCategory) -> int:
        """يعيد حساب أسعار كل منتجات الشجرة الفرعية (بلا هامش يدوي خاص)."""
        # كل الأقسام الفرعية في الشجرة (القسم + الأبناء)
        all_subs = await cls._subtree(session, sub)
        updated = 0
        for row in all_subs:
            result = await session.execute(
                select(Product).where(Product.sub_category_id == row.id)
            )
            for product in result.scalars().all():
                if getattr(product, "margin_manual", False):
                    continue  # الأدمن ضبط هامش المنتج يدوياً → الأولوية له
                cost = Decimal(str(product.cost_price_usd or 0))
                if cost <= 0:
                    continue
                percent, _src = await cls.resolve_product_margin(session, product)
                if cls.recalc_forced_from_margin(product, percent):
                    updated += 1
        return updated

    @classmethod
    async def cascade_category(cls, session, category: Category) -> int:
        """يعيد حساب أسعار كل منتجات القسم الرئيسي (بلا هامش خاص)."""
        result = await session.execute(
            select(SubCategory).where(SubCategory.category_id == category.id)
        )
        updated = 0
        for row in result.scalars().all():
            updated += await cls.cascade_subtree(session, row)
        return updated

    @classmethod
    async def _subtree(cls, session, sub: SubCategory) -> list[SubCategory]:
        """القسم الفرعي وكل أحفاده الداخليين."""
        result = await session.execute(
            select(SubCategory).where(SubCategory.category_id == sub.category_id)
        )
        all_rows = list(result.scalars().all())
        children: dict[int, list[SubCategory]] = {}
        for row in all_rows:
            if row.parent_sub_category_id:
                children.setdefault(row.parent_sub_category_id, []).append(row)

        out: list[SubCategory] = [sub]
        stack = [sub.id]
        seen = {sub.id}
        while stack:
            current = stack.pop()
            for child in children.get(current, []):
                if child.id in seen:
                    continue
                seen.add(child.id)
                out.append(child)
                stack.append(child.id)
        return out

    # ─────────── ضبط الهوامش ───────────

    @classmethod
    async def set_category_margin(
        cls, session, category: Category, percent: Decimal | None
    ) -> int:
        """يضبط هامش القسم الرئيسي ويعيد حساب أسعار منتجاته. يرجع عدد المحدّث."""
        category.profit_margin_percent = cls._clamp(percent)
        updated = await cls.cascade_category(session, category)
        await session.commit()
        return updated

    @classmethod
    async def set_sub_margin(
        cls, session, sub: SubCategory, percent: Decimal | None
    ) -> int:
        sub.profit_margin_percent = cls._clamp(percent)
        updated = await cls.cascade_subtree(session, sub)
        await session.commit()
        return updated

    @classmethod
    async def set_product_margin(
        cls, session, product: Product, percent: Decimal | None
    ) -> bool:
        """يضبط هامش منتج ويعيد سعره إن كان مسعّراً بهامش."""
        percent = cls._clamp(percent)
        product.profit_margin_percent = percent
        product.margin_manual = percent is not None
        if percent is not None:
            product.pricing_type = ProductPricingType.MARGIN_PERCENT
            changed = await cls.recalc_product_from_margin(session, product, percent)
        else:
            # إلغاء الهامش الخاص: يُعيد السعر لسعر الهامش المُحدد أعلى منه
            changed = False
            effective, _src = await cls.resolve_product_margin(session, product)
            if cls.is_margin_priced(product):
                changed = await cls.recalc_product_from_margin(session, product, effective)
        await session.commit()
        return changed

    @staticmethod
    def _clamp(percent: Decimal | None) -> Decimal | None:
        if percent is None:
            return None
        value = Decimal(str(percent))
        if value < MIN_MARGIN:
            return MIN_MARGIN
        if value > MAX_MARGIN:
            return MAX_MARGIN
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @classmethod
    async def attach_implicit_margin(cls, product: Product) -> Decimal | None:
        """
        يحفظ الهامش الضمني لمنتج منشأ بسعر يدوي (مثل خدمات الرشق)
        حتى يصبح الهامش ظاهراً وقابلاً للتعديل. لا يغيّر السعر.
        """
        cost = Decimal(str(product.cost_price_usd or 0))
        sell = Decimal(str(product.price_usd or 0))
        implicit = cls.implicit_margin(cost, sell)
        if implicit is None:
            return None
        product.profit_margin_percent = implicit
        product.pricing_type = ProductPricingType.MARGIN_PERCENT
        product.margin_manual = False
        return implicit
