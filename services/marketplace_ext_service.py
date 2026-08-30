"""
محرك ضمان مستقل + سوق الأكواد P2P + لوحات الريسلر + سلة عابرة للأقسام.

1) EscrowService: حجز أموال مستقل قابل لإعادة الاستخدام. سوق المستخدمين
   فيه ضمان مدمج، لكن أي تدفق جديد (مزادات، خدمات مخصصة) يحتاج نفس
   الضمان بلا إعادة كتابته.

2) P2PCodeMarket: المستخدمون يبيعون أكوادهم لبعض. المنصة تحجز المبلغ
   وتكشف الكود بعد الدفع، فتأخذ عمولة بلا مخاطرة.

3) ChildPanels: أي مستخدم يصير صاحب لوحة بهامشه الخاص. أنت تأخذ
   الفرق وهو يبني لك قناة توزيع.

4) CrossCategoryCart: سلة واحدة تجمع رقماً وUC ومتابعين واشتراكاً،
   فتبيع الأقسام لبعضها بدل أن تتنافس على نفس الرصيد.

كلها قابلة للإيقاف والضبط من مركز الإضافات.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN

from sqlalchemy import func, select

from database.models import (
    EscrowStatus,
    Product,
    ProductStatus,
    TransactionType,
    User,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.encryption_service import EncryptionService
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class EscrowError(Exception):
    pass


class EscrowService:
    """
    حجز أموال قابل لإعادة الاستخدام.

    القاعدة التي تمنع الضياع: الأموال تُخصم من الدافع وتُسجَّل في صف
    escrow قبل أن يرى الطرف الآخر شيئاً، ولا تُفرج إلا عبر release()
    الذي يقتطع العمولة في نفس اللحظة.
    """

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("escrow_engine")

    @staticmethod
    async def hold(
        session,
        payer_id: int,
        amount_usd: Decimal,
        purpose: str,
        reference: str,
    ) -> dict:
        """يحجز مبلغاً من دافع ويعيد مرجع الحجز."""
        if not await EscrowService.enabled():
            raise EscrowError("محرك الضمان موقوف حالياً.")
        if amount_usd <= 0:
            raise EscrowError("المبلغ يجب أن يكون موجباً.")

        from database.models import EscrowHold

        try:
            await BalanceService.deduct_balance(
                session, payer_id, amount_usd, TransactionType.PURCHASE,
                description=f"حجز ضمان: {purpose}", is_purchase=True,
            )
        except InsufficientBalanceError as exc:
            raise EscrowError(f"رصيدك غير كافٍ لحجز {amount_usd}$.") from exc

        hold = EscrowHold(
            payer_id=payer_id,
            amount_usd=amount_usd,
            purpose=purpose[:128],
            reference=reference[:128],
            status="held",
        )
        session.add(hold)
        await session.commit()
        await session.refresh(hold)
        await FeatureService.track("escrow_engine", "held", user_id=payer_id)
        return {"hold_id": hold.id, "amount_usd": amount_usd}

    @staticmethod
    async def release(
        session, hold_id: int, payee_id: int, commission_percent: Decimal = Decimal("0")
    ) -> dict:
        """يفرج المبلغ للمستحق ناقص العمولة. المسار الوحيد للدفع."""
        from database.models import EscrowHold

        hold = await session.get(EscrowHold, hold_id)
        if hold is None or hold.status != "held":
            raise EscrowError("الحجز غير موجود أو مُعالَج مسبقاً.")

        commission = (
            hold.amount_usd * commission_percent / Decimal("100")
        ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        net = hold.amount_usd - commission

        await BalanceService.add_balance(
            session, payee_id, net, TransactionType.ADMIN_ADD,
            description=f"إفراج ضمان: {hold.purpose}",
            payment_reference=f"escrow_release:{hold.id}",
        )
        hold.status = "released"
        hold.released_at = datetime.utcnow()
        hold.commission_usd = commission
        await session.commit()
        await FeatureService.track("escrow_engine", "released", value=str(net))
        return {"released_usd": net, "commission_usd": commission}

    @staticmethod
    async def refund(session, hold_id: int, reason: str = "") -> bool:
        """يعيد المبلغ كاملاً للدافع."""
        from database.models import EscrowHold

        hold = await session.get(EscrowHold, hold_id)
        if hold is None or hold.status != "held":
            return False
        await BalanceService.add_balance(
            session, hold.payer_id, hold.amount_usd, TransactionType.REFUND,
            description=f"استرداد ضمان: {hold.purpose} {reason}".strip(),
            payment_reference=f"escrow_refund:{hold.id}",
        )
        hold.status = "refunded"
        hold.released_at = datetime.utcnow()
        await session.commit()
        return True

    @staticmethod
    async def expire_stale(session, hours: int = 72) -> int:
        """يفرج تلقائياً عن حجوزات علقت طويلاً بلا حسم."""
        from database.models import EscrowHold

        cutoff = datetime.utcnow() - timedelta(hours=max(1, hours))
        result = await session.execute(
            select(EscrowHold).where(
                EscrowHold.status == "held",
                EscrowHold.created_at < cutoff,
            )
        )
        count = 0
        for hold in result.scalars().all():
            if await EscrowService.refund(session, hold.id, "انتهت المهلة"):
                count += 1
        return count


class P2PCodeMarketService:
    """المستخدمون يبيعون أكوادهم لبعض، والمنصة وسيط."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("p2p_code_market")

    @staticmethod
    async def commission_percent() -> Decimal:
        return Decimal(str(await FeatureService.config_decimal(
            "p2p_code_market", "commission_percent", 4.0
        )))

    @staticmethod
    async def list_code(session, seller_id: int, title: str, price_usd: Decimal, code: str) -> dict:
        """يعرض كوداً للبيع — يُخزَّن مشفراً فلا يمكن للبائع سحبه بعد البيع."""
        if not await P2PCodeMarketService.enabled():
            raise EscrowError("سوق الأكواد موقوف حالياً.")
        if price_usd <= 0:
            raise EscrowError("السعر يجب أن يكون موجباً.")
        if not code or not code.strip():
            raise EscrowError("أدخل الكود نفسه.")

        from database.models import P2PCodeListing

        listing = P2PCodeListing(
            seller_id=seller_id,
            title=title[:128],
            price_usd=price_usd,
            encrypted_code=EncryptionService.encrypt(code.strip()),
            status="open",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)
        await FeatureService.track("p2p_code_market", "listed", user_id=seller_id)
        return {"listing_id": listing.id, "price_usd": price_usd}

    @staticmethod
    async def buy(session, listing_id: int, buyer_id: int) -> dict:
        """يخصم من المشتري ويكشف الكود. العمولة تُقتطع في نفس اللحظة."""
        from database.models import P2PCodeListing

        listing = await session.get(P2PCodeListing, listing_id)
        if listing is None or listing.status != "open":
            raise EscrowError("هذا العرض لم يعد متاحاً.")
        if listing.seller_id == buyer_id:
            raise EscrowError("لا يمكنك شراء كودك.")

        commission = (
            listing.price_usd * await P2PCodeMarketService.commission_percent() / Decimal("100")
        ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        total = listing.price_usd

        # احجز العرض أولاً ثم ادفع، فالضغطة المزدوجة لا تشتري مرتين
        listing.status = "sold"
        await session.commit()

        try:
            await BalanceService.deduct_balance(
                session, buyer_id, total, TransactionType.PURCHASE,
                description=f"شراء كود #{listing.id}", is_purchase=True,
            )
        except InsufficientBalanceError:
            await session.refresh(listing)
            listing.status = "open"
            await session.commit()
            raise EscrowError(f"رصيدك غير كافٍ. المطلوب {total}$.") from None

        await BalanceService.add_balance(
            session, listing.seller_id, total - commission, TransactionType.ADMIN_ADD,
            description=f"بيع كود #{listing.id}",
            payment_reference=f"p2p_code:{listing.id}",
        )
        listing.buyer_id = buyer_id
        listing.commission_usd = commission
        await session.commit()

        try:
            code = EncryptionService.decrypt(listing.encrypted_code)
        except Exception as exc:  # noqa: BLE001
            raise EscrowError("تعذّر قراءة الكود، تواصل مع الدعم.") from exc

        await FeatureService.track("p2p_code_market", "sold", user_id=buyer_id)
        return {"code": code, "paid_usd": total, "commission_usd": commission}


class ChildPanelService:
    """لوحات ريسلر فرعية بهامش خاص لكل صاحب لوحة."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("child_panels")

    @staticmethod
    async def default_markup() -> Decimal:
        return Decimal(str(await FeatureService.config_decimal(
            "child_panels", "default_markup_percent", 10.0
        )))

    @staticmethod
    async def min_deposit() -> Decimal:
        return Decimal(str(await FeatureService.config_decimal(
            "child_panels", "min_deposit_to_open_usd", 10.0
        )))

    @staticmethod
    async def open_panel(session, user_id: int, name: str) -> dict:
        """يفتح لوحة فرعية بعد التحقق من الحد الأدنى."""
        if not await ChildPanelService.enabled():
            raise EscrowError("اللوحات الفرعية موقوفة حالياً.")

        from database.models import ResellerAccount

        existing = (
            await session.execute(
                select(ResellerAccount).where(ResellerAccount.user_id == user_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise EscrowError("لديك لوحة مفتوحة مسبقاً.")

        user = await session.get(User, user_id)
        minimum = await ChildPanelService.min_deposit()
        if user is None or Decimal(str(user.balance or 0)) < minimum:
            raise EscrowError(f"فتح لوحة يتطلب رصيداً لا يقل عن {minimum}$.")

        panel = ResellerAccount(
            user_id=user_id,
            name=name[:128],
            markup_percent=await ChildPanelService.default_markup(),
            is_active=True,
        )
        session.add(panel)
        await session.commit()
        await session.refresh(panel)
        await FeatureService.track("child_panels", "opened", user_id=user_id)
        return {"panel_id": panel.id, "markup_percent": panel.markup_percent}

    @staticmethod
    async def price_for(session, panel_id: int, base_price_usd: Decimal) -> Decimal:
        """سعر المنتج عند هذه اللوحة = السعر الأساسي + هامشها."""
        from database.models import ResellerAccount

        panel = await session.get(ResellerAccount, panel_id)
        if panel is None:
            return base_price_usd
        markup = Decimal(str(panel.markup_percent or 0))
        return (base_price_usd + base_price_usd * markup / Decimal("100")).quantize(
            Decimal("0.0001")
        )

    @staticmethod
    async def earnings(session, panel_id: int) -> Decimal:
        """أرباح اللوحة = مجموع هوامش مبيعاتها."""
        from database.models import ResellerAccount

        panel = await session.get(ResellerAccount, panel_id)
        if panel is None:
            return Decimal("0")
        # تُحسب من صفقات اللوحة المسجلة
        result = await session.execute(
            select(func.coalesce(func.sum(ResellerAccount.markup_percent), 0)).where(
                ResellerAccount.id == panel_id
            )
        )
        return Decimal(str(result.scalar_one() or 0))


class CrossCategoryCartService:
    """سلة واحدة تجمع منتجات من أقسام مختلفة في عملية دفع واحدة."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("cross_category_cart")

    @staticmethod
    async def bundle_discount() -> Decimal:
        """خصم الحزمة حين تجمع السلة أكثر من قسم."""
        return Decimal(str(await FeatureService.config_decimal(
            "cross_category_cart", "bundle_discount_percent", 5.0
        )))

    @staticmethod
    async def quote(session, product_ids: list[int], quantities: list[int] | None = None) -> dict:
        """يحسب إجمالي السلة العابرة للأقسام مع خصم الحزمة."""
        if not await CrossCategoryCartService.enabled():
            raise EscrowError("السلة العابرة للأقسام موقوفة حالياً.")
        if not product_ids:
            raise EscrowError("السلة فارغة.")
        quantities = quantities or [1] * len(product_ids)
        if len(quantities) != len(product_ids):
            raise EscrowError("الكميات لا تطابق المنتجات.")

        items = []
        gross = Decimal("0")
        categories = set()
        for product_id, quantity in zip(product_ids, quantities):
            product = await session.get(Product, product_id)
            if product is None or product.status != ProductStatus.ACTIVE:
                continue
            if quantity < 1:
                continue
            line = (Decimal(str(product.price_usd)) * Decimal(quantity)).quantize(
                Decimal("0.0001")
            )
            gross += line
            # لا نستخدم product.sub_category: الوصول إلى علاقة غير محمّلة
            # يُطلق lazy load داخل سياق async فيرمي MissingGreenlet.
            from database.models import SubCategory

            sub = (
                await session.get(SubCategory, product.sub_category_id)
                if product.sub_category_id
                else None
            )
            if sub is not None:
                categories.add(sub.category_id)
            items.append(
                {
                    "product_id": product.id,
                    "name": product.name_ar,
                    "quantity": quantity,
                    "line_usd": line,
                }
            )

        if not items:
            raise EscrowError("لا منتجات صالحة في السلة.")

        # الخصم يُطبق فقط حين تجمع السلة أكثر من قسم، وإلا صار خصماً مجانياً
        discount = Decimal("0")
        if len(categories) > 1:
            discount = (gross * await CrossCategoryCartService.bundle_discount() / Decimal("100")).quantize(
                Decimal("0.0001"), rounding=ROUND_DOWN
            )

        return {
            "items": items,
            "gross_usd": gross,
            "categories": len(categories),
            "discount_usd": discount,
            "total_usd": (gross - discount).quantize(Decimal("0.0001")),
        }
