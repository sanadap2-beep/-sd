"""
سوق الأرقام المستعملة.

مستخدم اشترى رقماً (لم يمتلئ بعد) يمكنه عرضه للبيع بسعر يحدده،
ومستخدم آخر يشتريه بضغطة. البائع يستلم الرصيد فوراً بعد خصم
عمولة يحددها الأدمن، والرقم يُنقل للمشتري مباشرة من المزود.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select

from database.models import NumberResaleListing, TransactionType
from services.balance_service import BalanceService
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class ResaleError(Exception):
    """خطأ واضح في سوق الأرقام المستعملة."""


class ResaleService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("number_resale_market")

    @staticmethod
    async def commission_percent(session) -> Decimal:
        return await FeatureService.config("number_resale_market", "commission_percent", Decimal("5"))

    @staticmethod
    async def open_listings(session, exclude_seller_id: int | None = None) -> list[NumberResaleListing]:
        query = select(NumberResaleListing).where(NumberResaleListing.status == "open")
        if exclude_seller_id is not None:
            query = query.where(NumberResaleListing.seller_id != exclude_seller_id)
        query = query.order_by(NumberResaleListing.price_usd.asc(), NumberResaleListing.created_at.desc())
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def seller_listings(session, seller_id: int) -> list[NumberResaleListing]:
        result = await session.execute(
            select(NumberResaleListing)
            .where(NumberResaleListing.seller_id == seller_id)
            .order_by(NumberResaleListing.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    def _canonical_phone(raw: str) -> str:
        digits = "".join(ch for ch in raw if ch.isdigit())
        return digits if digits else raw.strip()

    @staticmethod
    async def create_listing(
        session,
        seller_id: int,
        source_order_id: int,
        phone_number: str,
        service_name: str,
        country_name: str,
        price_usd: Decimal,
    ) -> NumberResaleListing:
        limit = await FeatureService.config("number_resale_market", "max_active_sales", 5)
        active = [
            l
            for l in await ResaleService.seller_listings(session, seller_id)
            if l.status == "open"
        ]
        if len(active) >= int(limit):
            raise ResaleError("وصلت للحد الأقصى من الإعلانات النشطة.")

        price_usd = price_usd.quantize(Decimal("0.0001"))
        if price_usd <= 0:
            raise ResaleError("السعر يجب أن يكون أكبر من صفر.")

        listing = NumberResaleListing(
            seller_id=seller_id,
            source_order_id=int(source_order_id),
            phone_number=ResaleService._canonical_phone(phone_number),
            service_name=service_name[:64],
            country_name=country_name[:64],
            price_usd=price_usd,
            commission_usd=Decimal("0"),
            status="open",
        )
        session.add(listing)
        await session.commit()
        await session.refresh(listing)
        return listing

    @staticmethod
    async def cancel_listing(session, listing_id: int, seller_id: int) -> bool:
        listing = await session.get(NumberResaleListing, listing_id)
        if listing is None or listing.seller_id != seller_id or listing.status == "sold":
            return False
        listing.status = "cancelled"
        await session.commit()
        return True

    @staticmethod
    async def buy_listing(
        session, listing_id: int, buyer_id: int, external_transfer: callable | None = None
    ) -> dict:
        """
        شراء رقم مستعمل: يخصم من المشتري، يضيف للبائع بعد العمولة،
        ويضع السجل بحالة sold مع platform_delivery توضيحاً.

        external_transfer: دالة تقوم بنقل الرقم فعلياً إلى المشتري لدى
        المزود (يستدعيها البوت في المعالج مع رسالة نجاح/فشل).
        """
        from sqlalchemy import update

        result = await session.execute(
            update(NumberResaleListing)
            .where(
                NumberResaleListing.id == listing_id,
                NumberResaleListing.status == "open",
                NumberResaleListing.seller_id != buyer_id,
            )
            .values(status="sold", buyer_id=buyer_id, commission_usd=None)
        )
        if result.rowcount == 0:
            listing = await session.get(NumberResaleListing, listing_id)
            if listing and listing.status == "open":
                raise ResaleError("لا يمكنك شراء رقمك الخاص.")
            raise ResaleError("هذا الإعلان لم يعد متاحاً.")

        listing = await session.get(NumberResaleListing, listing_id)
        if listing is None:
            raise ResaleError("الإعلان غير موجود.")

        price = listing.price_usd
        balance_ok = await BalanceService.has_balance(session, buyer_id, price)
        if not balance_ok:
            await session.execute(
                update(NumberResaleListing)
                .where(NumberResaleListing.id == listing_id)
                .values(status="open", buyer_id=None)
            )
            await session.commit()
            raise ResaleError("رصيدك غير كافٍ لهذه العملية.")

        percent = await ResaleService.commission_percent(session)
        commission = (price * percent / Decimal("100")).quantize(Decimal("0.0001"))
        net = price - commission

        try:
            await BalanceService.deduct_balance(
                session,
                buyer_id,
                price,
                TransactionType.PURCHASE,
                description=f"شراء رقم مستعمل #{listing.id} ({listing.service_name})",
                related_table="number_resale_listings",
                related_id=listing.id,
            )
        except Exception:
            await session.rollback()
            await session.execute(
                update(NumberResaleListing)
                .where(NumberResaleListing.id == listing_id)
                .values(status="open", buyer_id=None)
            )
            await session.commit()
            logger.exception("فشل خصم ثمن الرقم المستعمل %s", listing_id)
            raise ResaleError("فشلت المعاملة، حاول مجدداً.")

        try:
            await BalanceService.add_balance(
                session,
                listing.seller_id,
                net,
                TransactionType.ADMIN_ADD,
                description=f"بيع رقم مستعمل #{listing.id} (بعد عمولة {percentage_round(percent)}%)",
                related_table="number_resale_listings",
                related_id=listing.id,
            )
            listing.commission_usd = commission
            await session.commit()
        except Exception:
            # نعيد للمشتري ما دفع ونجعل الإعلان متاحاً مجدداً.
            await session.rollback()
            await BalanceService.add_balance(
                session,
                buyer_id,
                price,
                TransactionType.REFUND,
                description=f"استرداد شراء رقم مستعمل #{listing.id}",
                related_table="number_resale_listings",
                related_id=listing.id,
            )
            await session.execute(
                update(NumberResaleListing)
                .where(NumberResaleListing.id == listing_id)
                .values(status="open", buyer_id=None)
            )
            await session.commit()
            logger.exception("فشل إيداع ثمن الرقم المستعمل %s", listing_id)
            raise ResaleError("فشلت المعاملة، حاول مجدداً.")

        return {
            "listing": listing,
            "price_usd": price,
            "commission_usd": commission,
            "seller_net_usd": net,
            "phone_number": listing.phone_number,
            "service_name": listing.service_name,
        }


def percentage_round(percent: Decimal) -> str:
    return f"{percent:g}"