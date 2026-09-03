"""24-hour special offers: manual or API-backed flash deals."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Bot
from sqlalchemy import func, select

from database.models import (
    ApiProvider,
    ProviderService,
    SpecialOffer,
    SpecialOfferOrder,
    TransactionType,
    User,
)
from protocols.base import ProtocolError
from protocols.factory import ProtocolFactory
from services.balance_service import BalanceService, InsufficientBalanceError
from services.currency_service import CurrencyService
from services.notification_service import NotificationService


class SpecialOfferError(Exception):
    pass


class SpecialOfferService:
    DURATION_HOURS = 24
    LATE_REFUND_MINUTES = 60
    EXTEND_VOTES_REQUIRED = 50

    @staticmethod
    def public_text(offer: SpecialOffer) -> str:
        mode = "⚡ تلقائي" if offer.offer_type == "api" else "🧑‍💼 يدوي"
        eta = f"\n⏱ الوقت التقريبي: {offer.eta_text}" if offer.eta_text else ""
        end = f"\n⏳ ينتهي: {offer.ends_at.strftime('%Y-%m-%d %H:%M')} UTC" if offer.ends_at else ""
        return (
            "🔥 <b>عرض خاص 24 ساعة</b>\n\n"
            f"{mode}\n"
            f"📦 <b>{offer.name}</b>\n"
            f"💰 السعر: <b>{offer.price_usd}$</b>\n"
            f"📝 {offer.description or '—'}"
            f"{eta}{end}\n\n"
            "سارع بالحصول على العرض قبل انتهاء المدة."
        )

    @staticmethod
    async def publish(session, offer: SpecialOffer, bot: Bot) -> SpecialOffer:
        now = datetime.utcnow()
        offer.status = "active"
        offer.starts_at = now
        offer.ends_at = now + timedelta(hours=SpecialOfferService.DURATION_HOURS)
        await session.commit()
        await session.refresh(offer)
        await SpecialOfferService.announce(session, offer, bot, initial=True)
        return offer

    @staticmethod
    async def announce(session, offer: SpecialOffer, bot: Bot, initial: bool = False) -> None:
        notifier = NotificationService(bot)
        prefix = "🆕 انضاف عرض جديد لمدة 24 ساعة!\n\n" if initial else "⏰ تذكير بعرض خاص!\n\n"
        text = prefix + SpecialOfferService.public_text(offer)
        # قناة عامة
        await notifier.notify_public_channel(text)
        # كل المستخدمين داخل البوت
        result = await session.execute(select(User.telegram_id).where(User.is_banned.is_(False)))
        for telegram_id in result.scalars().all():
            await notifier.notify_user(
                telegram_id,
                text,
                notification_type="promotion",
                priority="normal",
                title="عرض خاص جديد",
            )

    @staticmethod
    async def create_manual(
        session,
        *,
        admin_id: int,
        name: str,
        description: str,
        price_usd: Decimal,
        eta_text: str,
        input_label: str,
    ) -> SpecialOffer:
        offer = SpecialOffer(
            offer_type="manual",
            name=name[:128],
            description=description[:4000],
            price_usd=price_usd,
            eta_text=eta_text[:128],
            input_label=input_label[:128],
            status="draft",
            created_by=admin_id,
        )
        session.add(offer)
        await session.commit()
        await session.refresh(offer)
        return offer

    @staticmethod
    async def create_api(
        session,
        *,
        admin_id: int,
        provider_id: int,
        external_service_id: str,
        name: str,
        description: str,
        price_usd: Decimal,
        eta_text: str,
        input_label: str,
    ) -> SpecialOffer:
        provider = await session.get(ApiProvider, provider_id)
        if provider is None or not provider.is_active:
            raise SpecialOfferError("المزود غير موجود أو غير مفعّل.")
        result = await session.execute(
            select(ProviderService).where(
                ProviderService.api_provider_id == provider_id,
                ProviderService.external_service_id == external_service_id,
            )
        )
        service = result.scalar_one_or_none()
        offer = SpecialOffer(
            offer_type="api",
            name=name[:128],
            description=description[:4000],
            price_usd=price_usd,
            eta_text=eta_text[:128],
            input_label=input_label[:128],
            status="draft",
            created_by=admin_id,
            api_provider_id=provider_id,
            provider_service_id=external_service_id,
            provider_service_ref_id=service.id if service else None,
            required_quantity=(service.min_quantity if service and service.min_quantity else 1),
        )
        session.add(offer)
        await session.commit()
        await session.refresh(offer)
        return offer

    @staticmethod
    async def purchase(
        session, offer_id: int, user_id: int, target: str, bot: Bot, price_override: Decimal | None = None
    ) -> SpecialOfferOrder:
        offer = await session.get(SpecialOffer, offer_id)
        if offer is None or offer.status != "active" or (offer.ends_at and offer.ends_at <= datetime.utcnow()):
            raise SpecialOfferError("العرض لم يعد متاحاً.")
        # السعر الفعلي المُخصوم (قد يكون أقل من سعر العرض عند وجود خصم وكيل)
        pay_price = price_override if price_override is not None else offer.price_usd
        try:
            await BalanceService.deduct_balance(
                session,
                user_id,
                pay_price,
                TransactionType.PURCHASE,
                description=f"شراء عرض خاص: {offer.name}",
                related_table="special_offers",
                related_id=offer.id,
                is_purchase=True,
            )
        except InsufficientBalanceError as exc:
            raise SpecialOfferError(f"رصيدك غير كافٍ. المطلوب {pay_price}$.") from exc

        order = SpecialOfferOrder(
            offer_id=offer.id,
            user_id=user_id,
            target=target[:500],
            price_usd=pay_price,
            status="pending",
            status_message="بانتظار التنفيذ",
        )
        session.add(order)
        await session.flush()

        if offer.offer_type == "api":
            provider = await session.get(ApiProvider, offer.api_provider_id)
            if provider is None or not provider.is_active:
                await SpecialOfferService.refund_order(session, order, "المزود غير متاح")
                raise SpecialOfferError("المزود غير متاح، تم استرجاع المبلغ.")
            try:
                protocol = ProtocolFactory.create_from_provider(provider)
                result = await protocol.place_order(
                    service_id=offer.provider_service_id,
                    target=target,
                    quantity=max(1, offer.required_quantity or 1),
                )
                order.external_order_id = result.external_order_id
                order.status = "processing"
                order.status_message = "تم إرسال الطلب للمزود"
            except ProtocolError as exc:
                await SpecialOfferService.refund_order(session, order, f"فشل المزود: {exc}")
                raise SpecialOfferError("فشل إرسال الطلب للمزود، تم استرجاع المبلغ.") from exc
        else:
            order.status = "manual_pending"
            order.status_message = "بانتظار تنفيذ الإدارة"

        offer.sales_count += 1
        await session.commit()
        await session.refresh(order)
        return order

    @staticmethod
    async def complete_order(session, order_id: int, bot: Bot) -> bool:
        order = await session.get(SpecialOfferOrder, order_id)
        if order is None or order.status in {"completed", "refunded"}:
            return False
        offer = await session.get(SpecialOffer, order.offer_id)
        user = await session.get(User, order.user_id)
        order.status = "completed"
        order.completed_at = datetime.utcnow()
        order.status_message = "تم التنفيذ بنجاح"
        if offer:
            offer.success_count += 1
        await session.commit()
        notifier = NotificationService(bot)
        if user and offer:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            await notifier.notify_user(
                user.telegram_id,
                f"✅ تم تنفيذ العرض الخاص بنجاح!\n\n📦 {offer.name}\n🆔 الطلب #{order.id}\n\n"
                "قيّم العرض:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔥 العرض ممتاز وتمديده", callback_data=f"special:vote_extend:{order.id}")],
                    [InlineKeyboardButton(text="✅ جيد ونريد عروض جديدة", callback_data="special:home")],
                ]),
                notification_type="order",
                priority="high",
            )
            await notifier.notify_successful_unified_order(
                username=user.username,
                full_name=user.full_name,
                product_name=f"عرض خاص: {offer.name}",
                price_usd=str(order.price_usd),
            )
        return True

    @staticmethod
    async def refund_order(session, order: SpecialOfferOrder, reason: str = "تعذر التنفيذ") -> bool:
        if order.status == "refunded":
            return False
        await BalanceService.add_balance(
            session,
            order.user_id,
            order.price_usd,
            TransactionType.REFUND,
            description=f"استرجاع عرض خاص #{order.id}: {reason}",
            related_table="special_offer_orders",
            related_id=order.id,
            payment_reference=f"special_offer_refund:{order.id}",
        )
        offer = await session.get(SpecialOffer, order.offer_id)
        if offer:
            offer.failed_count += 1
        order.status = "refunded"
        order.refunded_at = datetime.utcnow()
        order.status_message = reason[:500]
        await session.commit()
        return True

    @staticmethod
    async def refund_order_by_id(session, order_id: int, bot: Bot, reason: str = "تعذر تنفيذ العرض") -> bool:
        order = await session.get(SpecialOfferOrder, order_id)
        if order is None:
            return False
        ok = await SpecialOfferService.refund_order(session, order, reason)
        if ok:
            user = await session.get(User, order.user_id)
            offer = await session.get(SpecialOffer, order.offer_id)
            if user:
                await NotificationService(bot).notify_user(
                    user.telegram_id,
                    f"↩️ نعتذر، تعذر تنفيذ العرض الخاص وتمت إعادة المبلغ لرصيدك.\n📦 {offer.name if offer else 'عرض خاص'}",
                    notification_type="order",
                    priority="high",
                )
        return ok

    @staticmethod
    async def vote_extend(session, order_id: int, user_id: int) -> int:
        order = await session.get(SpecialOfferOrder, order_id)
        if order is None or order.user_id != user_id or order.status != "completed" or order.voted_extend:
            raise SpecialOfferError("لا يمكنك التصويت لهذا العرض.")
        offer = await session.get(SpecialOffer, order.offer_id)
        if offer is None:
            raise SpecialOfferError("العرض غير موجود.")
        order.voted_extend = True
        offer.extend_votes += 1
        if offer.extend_votes >= SpecialOfferService.EXTEND_VOTES_REQUIRED and not offer.extended_once:
            offer.extended_once = True
            offer.ends_at = max(offer.ends_at or datetime.utcnow(), datetime.utcnow()) + timedelta(hours=24)
            offer.status = "active"
        await session.commit()
        return offer.extend_votes

    @staticmethod
    async def check_api_order_statuses(session, bot: Bot) -> int:
        cutoff = datetime.utcnow() - timedelta(minutes=SpecialOfferService.LATE_REFUND_MINUTES)
        result = await session.execute(
            select(SpecialOfferOrder).where(SpecialOfferOrder.status == "processing")
        )
        changed = 0
        for order in result.scalars().all():
            offer = await session.get(SpecialOffer, order.offer_id)
            provider = await session.get(ApiProvider, offer.api_provider_id) if offer else None
            if not offer or not provider or not order.external_order_id:
                continue
            if order.created_at and order.created_at < cutoff:
                await SpecialOfferService.refund_order_by_id(session, order.id, bot, "تأخر العرض أكثر من ساعة")
                changed += 1
                continue
            try:
                protocol = ProtocolFactory.create_from_provider(provider)
                status = await protocol.check_order_status(order.external_order_id)
            except Exception:
                continue
            if status.status == "completed":
                await SpecialOfferService.complete_order(session, order.id, bot)
                changed += 1
            elif status.status in {"failed", "refunded"}:
                await SpecialOfferService.refund_order_by_id(session, order.id, bot, "فشل تنفيذ العرض عند المزود")
                changed += 1
        return changed

    @staticmethod
    async def maintenance(session, bot: Bot) -> None:
        now = datetime.utcnow()
        # تذكير بعد 4 ساعات من النشر
        result = await session.execute(
            select(SpecialOffer).where(SpecialOffer.status == "active")
        )
        offers = list(result.scalars().all())
        for offer in offers:
            if offer.starts_at and not offer.reminder_4h_sent and now >= offer.starts_at + timedelta(hours=4):
                offer.reminder_4h_sent = True
                await SpecialOfferService.announce(session, offer, bot, initial=False)
            if offer.ends_at and not offer.reminder_2h_sent and now >= offer.ends_at - timedelta(hours=2):
                offer.reminder_2h_sent = True
                await SpecialOfferService.announce(session, offer, bot, initial=False)
            if offer.ends_at and now >= offer.ends_at and not offer.extended_once:
                offer.status = "expired"
        await session.commit()
        await SpecialOfferService.check_api_order_statuses(session, bot)

    @staticmethod
    async def stats(session) -> dict:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        offers_today = (await session.execute(select(func.count(SpecialOffer.id)).where(SpecialOffer.created_at >= today))).scalar_one()
        sales = (await session.execute(select(func.count(SpecialOfferOrder.id)).where(SpecialOfferOrder.created_at >= today))).scalar_one()
        completed = (await session.execute(select(func.count(SpecialOfferOrder.id)).where(SpecialOfferOrder.status == "completed"))).scalar_one()
        total_orders = (await session.execute(select(func.count(SpecialOfferOrder.id)))).scalar_one()
        success_rate = round(completed / total_orders * 100, 1) if total_orders else 0
        return {"offers_today": offers_today, "sales_today": sales, "success_rate": success_rate}
