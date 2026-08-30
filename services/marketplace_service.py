"""
سوق المستخدمين (Peer Marketplace) مع محرك ضمان الوسيط.

المشكلة التي يحلها هذا الملف: كيف يبيع المستخدمون لبعض دون أن ينصب
أحدهم على الآخر، ودون أن تضيع عمولة المنصة؟

الحل — ثلاث فئات من الأصول، ولكل فئة آلية مختلفة:

1) الأكواد الرقمية (DIGITAL_CODE):
   البائع يرفع الكود **مشفراً** لحظة إنشاء الإعلان، فيصبح الأصل بيد
   المنصة لا بيد البائع. عند الشراء يُخصم من المشتري ثم يُكشف له الكود.
   احتمال النصب = صفر، والعمولة تُقتطع في نفس اللحظة.

2) الأصول التي تحتاج تسليماً يدوياً (GAME_ACCOUNT / SERVICE / OTHER):
   أموال المشتري تُحتجز عند المنصة (Escrow) ولا تصل للبائع. الأدمن
   وسيط: ينسّق التسليم ثم يضغط «تأكيد التسليم» فتُفرج الأموال للبائع
   ناقص العمولة. إن لم يؤكد المشتري خلال مهلة، تُفرج تلقائياً.
   وإن فتح المشتري نزاعاً، يقرر الأدمن.

3) أرقام SMS (SMS_NUMBER):
   لا يُسمح بعرض رقم إلا إذا أثبت البائع ملكيته الحالية له من سجل
   طلباته عندنا، فيُحوَّل إلى رقم مقيم باسم المشتري بعد البيع.

لماذا لا تضيع العمولة أبداً:
   تُحسب لحظة الشراء وتُخزَّن في الصف، ولا تُفرج أموال البائع إلا
   عبر دالة release واحدة تقتطعها. لا يوجد مسار يدفع للبائع كامل
   المبلغ، ولا مسار يردّ للمشتري بعد الإفراج.

كل عمليات المال داخل قفل المستخدم من BalanceService، وكل حالة
انتقالية محمية بفحص الحالة الحالية، فالضغط المزدوج لا يبيع مرتين.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from sqlalchemy import func, select

from database.models import (
    EscrowStatus,
    MarketListing,
    MarketListingPhoto,
    MarketListingStatus,
    MarketTransaction,
    NumberOrder,
    OrderStatus,
    Transaction,
    TransactionType,
    User,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.encryption_service import EncryptionService
from services.feature_service import FeatureService
from services.points_service import PointsService

logger = logging.getLogger(__name__)


class MarketError(Exception):
    pass


class MarketplaceService:
    # ─────────── الإعدادات ───────────

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("peer_marketplace")

    @staticmethod
    async def default_commission() -> Decimal:
        value = await FeatureService.config_decimal(
            "peer_marketplace", "default_commission_percent", 5.0
        )
        return Decimal(str(value))

    # ─────────── إنشاء الإعلان ───────────

    @staticmethod
    async def create_listing(
        session,
        seller_id: int,
        kind: str,
        title: str,
        description: str,
        price_usd: Decimal,
        photo_file_ids: list[str] | None = None,
        secret_code: str | None = None,
        ownership_proof: str | None = None,
    ) -> MarketListing:
        """
        ينشئ إعلاناً بحالة PENDING_REVIEW ويجهّزه ليصل للأدمن.
        لا يُنشر لأي مستخدم قبل موافقة الأدمن وتحديد العمولة.
        """
        if not await MarketplaceService.enabled():
            raise MarketError("سوق المستخدمين موقوف حالياً.")

        user = await session.get(User, seller_id)
        if user is None:
            raise MarketError("المستخدم غير موجود.")
        if user.is_banned:
            raise MarketError("حسابك موقوف.")

        min_age = await FeatureService.config_int(
            "peer_marketplace", "require_min_account_age_hours", 24
        )
        if min_age > 0 and user.joined_at is not None:
            age_hours = (datetime.utcnow() - user.joined_at).total_seconds() / 3600
            if age_hours < min_age:
                raise MarketError(
                    f"البيع في السوق يتطلب حساباً بعمر {min_age} ساعة على الأقل."
                )

        min_balance = await FeatureService.config_decimal(
            "peer_marketplace", "require_min_balance_usd", 5.0
        )
        if min_balance > 0 and Decimal(str(user.balance or 0)) < Decimal(str(min_balance)):
            raise MarketError(
                f"البيع في السوق يتطلب رصيداً لا يقل عن {min_balance}$ كضمان جدية."
            )

        min_price = await FeatureService.config_decimal("peer_marketplace", "min_price_usd", 1.0)
        max_price = await FeatureService.config_decimal(
            "peer_marketplace", "max_price_usd", 10000.0
        )
        if price_usd < Decimal(str(min_price)) or price_usd > Decimal(str(max_price)):
            raise MarketError(f"السعر يجب أن يكون بين {min_price}$ و{max_price}$.")

        max_listings = await FeatureService.config_int(
            "peer_marketplace", "max_active_listings_per_user", 10
        )
        if max_listings > 0:
            active = (
                await session.execute(
                    select(func.count(MarketListing.id)).where(
                        MarketListing.seller_id == seller_id,
                        MarketListing.status.in_(
                            [
                                MarketListingStatus.DRAFT,
                                MarketListingStatus.PENDING_REVIEW,
                                MarketListingStatus.APPROVED,
                            ]
                        ),
                    )
                )
            ).scalar_one()
            if int(active) >= max_listings:
                raise MarketError(f"لديك {max_listings} إعلانات نشطة كحد أقصى.")

        # ── حماية خاصة بكل نوع أصل ──
        secret_payload = None
        if kind in ("digital_code", "game_account"):
            if not secret_code or not secret_code.strip():
                raise MarketError("هذا النوع يتطلب رفع بيانات التسليم ليتم حفظها مشفرة.")
            secret_payload = EncryptionService.encrypt(secret_code.strip())
        elif kind == "sms_number":
            await MarketplaceService._verify_number_ownership(
                session, seller_id, ownership_proof
            )

        auto_approved = await MarketplaceService._should_auto_approve_listing(
            session,
            seller_id=seller_id,
            price_usd=price_usd,
        )
        listing = MarketListing(
            seller_id=seller_id,
            kind=kind,
            title=title[:128],
            description=(description or "")[:4000],
            seller_price_usd=price_usd,
            commission_percent=await MarketplaceService.default_commission(),
            secret_payload=secret_payload,
            ownership_proof=(ownership_proof or "")[:255] or None,
            status=MarketListingStatus.APPROVED if auto_approved else MarketListingStatus.PENDING_REVIEW,
            published_at=datetime.utcnow() if auto_approved else None,
        )
        session.add(listing)
        await session.flush()

        for index, file_id in enumerate(photo_file_ids or []):
            session.add(
                MarketListingPhoto(listing_id=listing.id, file_id=file_id, sort_order=index)
            )
        await session.commit()
        await session.refresh(listing)

        await FeatureService.track(
            "peer_marketplace", "listing_created", user_id=seller_id, value=kind
        )
        return listing

    @staticmethod
    async def _verify_number_ownership(session, seller_id: int, proof: str | None) -> None:
        """
        يتأكد أن البائع يملك الرقم فعلاً من سجل طلباته،
        فلا يستطيع أحد عرض رقم لا يملكه.
        """
        if not proof or not proof.strip():
            raise MarketError("أدخل رقم الطلب الذي اشتريت به هذا الرقم من البوت.")
        try:
            order_id = int(proof.strip())
        except ValueError:
            raise MarketError("رقم الطلب يجب أن يكون رقماً.") from None

        order = await session.get(NumberOrder, order_id)
        if order is None or order.user_id != seller_id:
            raise MarketError("لا يوجد طلب بهذا الرقم باسمك.")
        if order.status not in (
            OrderStatus.COMPLETED,
            OrderStatus.CODE_RECEIVED,
            OrderStatus.PENDING,
        ):
            raise MarketError("هذا الطلب لم يعد نشطاً فلا يمكن عرض رقمه.")

    # ─────────── مراجعة الأدمن ───────────

    @staticmethod
    async def _should_auto_approve_listing(session, seller_id: int, price_usd: Decimal) -> bool:
        """Auto-approve only trusted sellers, within a safe price ceiling."""
        if not await FeatureService.enabled("trusted_seller_auto_approve"):
            return False
        max_price = Decimal(str(await FeatureService.config_decimal(
            "trusted_seller_auto_approve", "max_auto_price_usd", 50.0
        )))
        if price_usd > max_price:
            return False
        try:
            from services.market_profile_service import MarketProfileService

            profile = await MarketProfileService.get(session, seller_id)
            stats = MarketProfileService.stats(profile)
            min_sales = await FeatureService.config_int(
                "trusted_seller_auto_approve", "min_successful_sales", 5
            )
            min_rate = await FeatureService.config_int(
                "trusted_seller_auto_approve", "min_success_rate", 90
            )
            return stats["successful_sales"] >= min_sales and stats["success_rate"] >= min_rate
        except Exception:
            return False

    @staticmethod
    async def pending_listings(session, limit: int = 20) -> list[MarketListing]:
        result = await session.execute(
            select(MarketListing)
            .where(MarketListing.status == MarketListingStatus.PENDING_REVIEW)
            .order_by(MarketListing.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def approve(
        session, listing_id: int, admin_id: int, commission_percent: Decimal
    ) -> MarketListing | None:
        """
        الأدمن يحدد العمولة ويسمح بالنشر.
        العمولة تُضاف فوق سعر البائع، فلا تُخصم منه.
        """
        listing = await session.get(MarketListing, listing_id)
        if listing is None or listing.status != MarketListingStatus.PENDING_REVIEW:
            return None
        if commission_percent < 0 or commission_percent > Decimal("90"):
            raise MarketError("العمولة يجب أن تكون بين 0% و90%.")

        expire_days = await FeatureService.config_int(
            "peer_marketplace", "listing_expire_days", 30
        )
        listing.commission_percent = commission_percent
        listing.status = MarketListingStatus.APPROVED
        listing.reviewed_by = admin_id
        listing.published_at = datetime.utcnow()
        listing.expires_at = datetime.utcnow() + timedelta(days=max(1, expire_days))
        await session.commit()
        await session.refresh(listing)
        await FeatureService.track("peer_marketplace", "listing_approved", value=str(listing_id))
        return listing

    @staticmethod
    async def reject(
        session, listing_id: int, admin_id: int, reason: str
    ) -> MarketListing | None:
        listing = await session.get(MarketListing, listing_id)
        if listing is None or listing.status != MarketListingStatus.PENDING_REVIEW:
            return None
        listing.status = MarketListingStatus.REJECTED
        listing.rejection_reason = reason[:255]
        listing.reviewed_by = admin_id
        await session.commit()
        return listing

    # ─────────── العرض ───────────

    @staticmethod
    async def browse(
        session, kind: str | None = None, page: int = 0, per_page: int = 8
    ) -> tuple[list[MarketListing], int]:
        query = select(MarketListing).where(
            MarketListing.status == MarketListingStatus.APPROVED
        )
        count_query = select(func.count(MarketListing.id)).where(
            MarketListing.status == MarketListingStatus.APPROVED
        )
        if kind and kind != "all":
            query = query.where(MarketListing.kind == kind)
            count_query = count_query.where(MarketListing.kind == kind)
        total = int((await session.execute(count_query)).scalar_one())
        result = await session.execute(
            query.order_by(MarketListing.published_at.desc())
            .offset(max(0, page) * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), total

    @staticmethod
    async def total_price(listing: MarketListing) -> Decimal:
        """ما يدفعه المشتري = سعر البائع + العمولة."""
        commission = (
            listing.seller_price_usd * listing.commission_percent / Decimal("100")
        ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        return (listing.seller_price_usd + commission).quantize(Decimal("0.0001"))

    @staticmethod
    async def commission_amount(listing: MarketListing) -> Decimal:
        return (
            listing.seller_price_usd * listing.commission_percent / Decimal("100")
        ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)

    # ─────────── الشراء (Escrow) ───────────

    @staticmethod
    async def purchase(
        session,
        listing_id: int,
        buyer_id: int,
        use_points: bool = False,
    ) -> MarketTransaction:
        """
        يخصم من المشتري ويحتجز الأموال عند المنصة.
        لا يصل شيء للبائع قبل تأكيد التسليم.
        """
        if not await MarketplaceService.enabled():
            raise MarketError("سوق المستخدمين موقوف حالياً.")

        listing = await session.get(MarketListing, listing_id)
        if listing is None or listing.status != MarketListingStatus.APPROVED:
            raise MarketError("هذا الإعلان لم يعد متاحاً.")
        if listing.seller_id == buyer_id:
            raise MarketError("لا يمكنك شراء إعلانك الخاص.")

        buyer = await session.get(User, buyer_id)
        if buyer is None:
            raise MarketError("المستخدم غير موجود.")
        if buyer.is_banned:
            raise MarketError("حسابك موقوف.")

        total = await MarketplaceService.total_price(listing)
        commission = await MarketplaceService.commission_amount(listing)

        # ── احجز الإعلان أولاً ثم ادفع ──
        # نقلب الحالة ونحفظها قبل أي خصم، فالضغطة المزدوجة أو طلبان
        # متزامنان على نفس الإعلان لا يبيعانه مرتين.
        listing.status = MarketListingStatus.SOLD
        await session.commit()

        # ── الدفع: نقاط أولاً إن طُلبت، والباقي من الرصيد ──
        points_used = 0
        points_usd = Decimal("0")
        cash_usd = total
        try:
            if use_points:
                split = await PointsService.split_payment(
                    session,
                    buyer_id,
                    total,
                    use_points=True,
                    description=f"شراء من السوق: {listing.title}",
                    related_table="market_listings",
                    related_id=listing.id,
                )
                points_used = split["points_used"]
                points_usd = split["points_usd"]
                cash_usd = split["cash_usd"]

            if cash_usd > 0:
                await BalanceService.deduct_balance(
                    session,
                    buyer_id,
                    cash_usd,
                    TransactionType.PURCHASE,
                    description=f"شراء من السوق (محجوز): {listing.title}",
                    related_table="market_listings",
                    related_id=listing.id,
                    is_purchase=True,
                )
        except InsufficientBalanceError:
            # فشل الدفع -> نعيد الإعلان للعرض كأن شيئاً لم يحدث
            await session.refresh(listing)
            listing.status = MarketListingStatus.APPROVED
            await session.commit()
            raise MarketError(
                f"رصيدك غير كافٍ. المطلوب {total}$ والمتاح لديك أقل من ذلك."
            ) from None
        except Exception:
            await session.refresh(listing)
            listing.status = MarketListingStatus.APPROVED
            await session.commit()
            raise

        transaction = MarketTransaction(
            listing_id=listing.id,
            seller_id=listing.seller_id,
            buyer_id=buyer_id,
            seller_price_usd=listing.seller_price_usd,
            commission_percent=listing.commission_percent,
            commission_usd=commission,
            total_charged_usd=total,
            points_used=points_used,
            points_usd=points_usd,
            status=EscrowStatus.FUNDED,
        )
        session.add(transaction)
        await session.commit()
        await session.refresh(transaction)

        await FeatureService.track(
            "peer_marketplace", "purchased", user_id=buyer_id, value=str(listing.id)
        )
        return transaction

    @staticmethod
    async def reveal_secret(session, transaction_id: int, buyer_id: int) -> str:
        """يكشف الكود المشفر للمشتري فقط، وبعد الدفع."""
        transaction = await session.get(MarketTransaction, transaction_id)
        if transaction is None or transaction.buyer_id != buyer_id:
            raise MarketError("عملية غير موجودة.")
        if transaction.status not in (
            EscrowStatus.FUNDED,
            EscrowStatus.DELIVERED,
            EscrowStatus.RELEASED,
        ):
            raise MarketError("لم يتم الدفع بعد.")
        listing = await session.get(MarketListing, transaction.listing_id)
        if listing is None or not listing.secret_payload:
            raise MarketError("هذا الإعلان لا يحتوي كوداً.")
        try:
            return EncryptionService.decrypt(listing.secret_payload)
        except Exception as exc:
            logger.error("فشل فك تشفير كود الإعلان %s: %s", listing.id, exc)
            raise MarketError("تعذّر قراءة الكود، تواصل مع الدعم.") from exc

    # ─────────── الإفراج / الاسترداد ───────────

    @staticmethod
    async def mark_delivered(session, transaction_id: int, actor_id: int) -> bool:
        """يعلن أن البائع سلّم الأصل (يضغطها الأدمن أو البائع)."""
        transaction = await session.get(MarketTransaction, transaction_id)
        if transaction is None or transaction.status != EscrowStatus.FUNDED:
            return False
        transaction.status = EscrowStatus.DELIVERED
        transaction.delivered_at = datetime.utcnow()
        await session.commit()
        return True

    @staticmethod
    async def release(session, transaction_id: int, admin_id: int | None = None) -> bool:
        """
        يفرج الأموال للبائع ناقص العمولة. المسار الوحيد الذي يدفع للبائع.
        """
        transaction = await session.get(MarketTransaction, transaction_id)
        if transaction is None:
            return False
        if transaction.status not in (EscrowStatus.FUNDED, EscrowStatus.DELIVERED):
            return False

        await BalanceService.add_balance(
            session,
            transaction.seller_id,
            transaction.seller_price_usd,
            TransactionType.ADMIN_ADD,
            description=f"بيع في سوق المستخدمين #{transaction.listing_id}",
            related_table="market_transactions",
            related_id=transaction.id,
            payment_reference=f"market_release:{transaction.id}",
        )
        transaction.status = EscrowStatus.RELEASED
        transaction.released_at = datetime.utcnow()
        transaction.resolved_by = admin_id
        await session.commit()
        try:
            from services.market_profile_service import MarketProfileService

            await MarketProfileService.record_success(session, transaction.seller_id)
        except Exception:
            pass
        await FeatureService.track(
            "peer_marketplace", "released", user_id=transaction.seller_id,
            value=str(transaction.commission_usd),
        )
        return True

    @staticmethod
    async def refund(session, transaction_id: int, admin_id: int | None = None,
                     note: str | None = None) -> bool:
        """يعيد كامل المبلغ للمشتري. يُستخدم قبل الإفراج فقط."""
        transaction = await session.get(MarketTransaction, transaction_id)
        if transaction is None:
            return False
        if transaction.status not in (
            EscrowStatus.FUNDED,
            EscrowStatus.DELIVERED,
            EscrowStatus.DISPUTED,
        ):
            return False

        await BalanceService.add_balance(
            session,
            transaction.buyer_id,
            transaction.total_charged_usd - transaction.points_usd,
            TransactionType.REFUND,
            description=f"استرداد عملية سوق #{transaction.listing_id}",
            related_table="market_transactions",
            related_id=transaction.id,
            payment_reference=f"market_refund:{transaction.id}",
        )
        if transaction.points_used > 0:
            from database.models import LoyaltyEvent
            from uuid import uuid4

            session.add(
                LoyaltyEvent(
                    user_id=transaction.buyer_id,
                    event_key=f"market_refund:{transaction.id}:{uuid4().hex}",
                    event_type="refund",
                    points=transaction.points_used,
                    description="إرجاع نقاط عملية سوق ملغاة",
                )
            )
            buyer = await session.get(User, transaction.buyer_id)
            if buyer is not None:
                buyer.loyalty_points = (buyer.loyalty_points or 0) + transaction.points_used

        # نعيد الإعلان للعرض إن لم تنتهِ صلاحيته
        listing = await session.get(MarketListing, transaction.listing_id)
        if listing is not None and listing.status == MarketListingStatus.SOLD:
            if listing.expires_at is None or listing.expires_at > datetime.utcnow():
                listing.status = MarketListingStatus.APPROVED
            else:
                listing.status = MarketListingStatus.EXPIRED

        transaction.status = EscrowStatus.REFUNDED
        transaction.dispute_note = (note or "")[:500]
        transaction.resolved_by = admin_id
        await session.commit()
        return True

    @staticmethod
    async def dispute(session, transaction_id: int, buyer_id: int, note: str) -> bool:
        """المشتري يفتح نزاعاً قبل الإفراج، فيُجمَّد كل شيء حتى قرار الأدمن."""
        transaction = await session.get(MarketTransaction, transaction_id)
        if transaction is None or transaction.buyer_id != buyer_id:
            return False
        if transaction.status not in (EscrowStatus.FUNDED, EscrowStatus.DELIVERED):
            return False
        transaction.status = EscrowStatus.DISPUTED
        transaction.dispute_note = note[:500]
        await session.commit()
        try:
            from services.market_profile_service import MarketProfileService

            await MarketProfileService.record_failure(session, transaction.seller_id)
        except Exception:
            pass
        await FeatureService.track("peer_marketplace", "disputed", user_id=buyer_id)
        return True

    # ─────────── المهام الخلفية ───────────

    @staticmethod
    async def auto_release_due(session) -> list[int]:
        """
        يفرج تلقائياً عن العمليات التي سلمها البائع ولم يعترض المشتري
        خلال مهلة الإفراج، فلا تبقى الأموال معلّقة للأبد.
        """
        hours = await FeatureService.config_int(
            "peer_marketplace", "auto_release_hours", 72
        )
        if hours <= 0:
            return []
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        result = await session.execute(
            select(MarketTransaction).where(
                MarketTransaction.status == EscrowStatus.DELIVERED,
                MarketTransaction.delivered_at.is_not(None),
                MarketTransaction.delivered_at < cutoff,
            )
        )
        released = []
        for transaction in result.scalars().all():
            if await MarketplaceService.release(session, transaction.id):
                released.append(transaction.id)
        return released

    @staticmethod
    async def expire_listings(session) -> int:
        result = await session.execute(
            select(MarketListing).where(
                MarketListing.status == MarketListingStatus.APPROVED,
                MarketListing.expires_at.is_not(None),
                MarketListing.expires_at < datetime.utcnow(),
            )
        )
        count = 0
        for listing in result.scalars().all():
            listing.status = MarketListingStatus.EXPIRED
            count += 1
        if count:
            await session.commit()
        return count

    # ─────────── إحصاءات ───────────

    @staticmethod
    async def stats(session) -> dict:
        total = (
            await session.execute(
                select(func.count(MarketListing.id)).where(
                    MarketListing.status.in_(
                        [MarketListingStatus.APPROVED, MarketListingStatus.SOLD]
                    )
                )
            )
        ).scalar_one()
        commission = (
            await session.execute(
                select(func.coalesce(func.sum(MarketTransaction.commission_usd), 0)).where(
                    MarketTransaction.status == EscrowStatus.RELEASED
                )
            )
        ).scalar_one()
        volume = (
            await session.execute(
                select(func.coalesce(func.sum(MarketTransaction.total_charged_usd), 0)).where(
                    MarketTransaction.status.in_(
                        [EscrowStatus.RELEASED, EscrowStatus.DELIVERED, EscrowStatus.FUNDED]
                    )
                )
            )
        ).scalar_one()
        pending = (
            await session.execute(
                select(func.count(MarketListing.id)).where(
                    MarketListing.status == MarketListingStatus.PENDING_REVIEW
                )
            )
        ).scalar_one()
        disputed = (
            await session.execute(
                select(func.count(MarketTransaction.id)).where(
                    MarketTransaction.status == EscrowStatus.DISPUTED
                )
            )
        ).scalar_one()
        return {
            "listings": int(total),
            "pending_review": int(pending),
            "volume_usd": Decimal(str(volume or 0)),
            "commission_usd": Decimal(str(commission or 0)),
            "open_disputes": int(disputed),
        }
