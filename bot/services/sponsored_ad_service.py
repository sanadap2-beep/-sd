"""Paid user advertisements reviewed by admins and reposted to public channel."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import desc, func, select

from database.models import SponsoredAd, TransactionType, User
from services.balance_service import BalanceService, InsufficientBalanceError
from services.notification_service import NotificationService
from services.settings_service import SettingsService


class SponsoredAdError(Exception):
    pass


class SponsoredAdService:
    BASE_PRICE_USD = Decimal("2")
    RENEW_PRICE_USD = Decimal("1")
    BASE_HOURS = 24
    RENEW_HOURS = 15
    REPOST_MINUTES = 30

    @staticmethod
    def photos(ad: SponsoredAd) -> list[str]:
        try:
            data = json.loads(ad.photo_file_ids or "[]")
            return [str(item) for item in data if item]
        except Exception:
            return []

    @staticmethod
    def render(ad: SponsoredAd) -> str:
        parts = [
            "📢 <b>إعلان</b>",
            "",
            f"<b>{ad.title}</b>",
            ad.body,
        ]
        if ad.item_type:
            parts.append(f"🏷 النوع: {ad.item_type}")
        if ad.price_text:
            parts.append(f"💰 السعر: {ad.price_text}")
        parts.append(f"📞 التواصل: {ad.contact}")
        if ad.ends_at:
            parts.append(f"⏳ صالح حتى: {ad.ends_at.strftime('%Y-%m-%d %H:%M')} UTC")
        return "\n".join(parts)

    @staticmethod
    async def create_pending(
        session,
        user_id: int,
        *,
        title: str,
        body: str,
        item_type: str | None,
        price_text: str | None,
        contact: str,
        photo_file_ids: list[str],
    ) -> SponsoredAd:
        user = await session.get(User, user_id)
        if user is None:
            raise SponsoredAdError("المستخدم غير موجود.")
        try:
            await BalanceService.deduct_balance(
                session,
                user_id,
                SponsoredAdService.BASE_PRICE_USD,
                TransactionType.PURCHASE,
                description="رسوم نشر إعلان مدفوع",
                is_purchase=True,
            )
        except InsufficientBalanceError as exc:
            raise SponsoredAdError(f"رصيدك غير كافٍ. سعر الإعلان {SponsoredAdService.BASE_PRICE_USD}$.") from exc

        ad = SponsoredAd(
            user_id=user_id,
            title=title[:128],
            body=body[:4000],
            item_type=(item_type or "")[:64] or None,
            price_text=(price_text or "")[:64] or None,
            contact=contact[:128],
            photo_file_ids=json.dumps(photo_file_ids[:5], ensure_ascii=False),
            status="pending",
            amount_paid_usd=SponsoredAdService.BASE_PRICE_USD,
        )
        session.add(ad)
        await session.commit()
        await session.refresh(ad)
        return ad

    @staticmethod
    async def publish_to_public(bot: Bot, ad: SponsoredAd) -> bool:
        channel_id_raw = await SettingsService.get("public_channel_id", "0")
        try:
            channel_id = int(channel_id_raw or 0)
        except ValueError:
            channel_id = 0
        if not channel_id:
            return False
        text = "🆕 <b>تم نشر إعلان جديد</b>\n\n" + SponsoredAdService.render(ad)
        photos = SponsoredAdService.photos(ad)
        try:
            if photos:
                await bot.send_photo(channel_id, photos[0], caption=text, parse_mode="HTML")
            else:
                await bot.send_message(channel_id, text, parse_mode="HTML")
            return True
        except Exception:
            return False

    @staticmethod
    async def approve(session, ad_id: int, admin_id: int, bot: Bot) -> SponsoredAd | None:
        ad = await session.get(SponsoredAd, ad_id)
        if ad is None or ad.status != "pending":
            return None
        now = datetime.utcnow()
        ad.status = "active"
        ad.admin_id = admin_id
        ad.reviewed_at = now
        ad.starts_at = now
        ad.ends_at = now + timedelta(hours=SponsoredAdService.BASE_HOURS)
        ad.last_channel_post_at = now
        await session.commit()
        await session.refresh(ad)
        await SponsoredAdService.publish_to_public(bot, ad)
        user = await session.get(User, ad.user_id)
        if user:
            await NotificationService(bot).notify_user(
                user.telegram_id,
                "✅ <b>تم قبول نشر إعلانك</b>\n\nسررنا بك، تم نشر إعلانك داخل البوت وعلى قناة الإشعارات.",
                notification_type="promotion",
                priority="high",
            )
        return ad

    @staticmethod
    async def reject(session, ad_id: int, admin_id: int, bot: Bot) -> SponsoredAd | None:
        ad = await session.get(SponsoredAd, ad_id)
        if ad is None or ad.status != "pending":
            return None
        await BalanceService.add_balance(
            session,
            ad.user_id,
            SponsoredAdService.BASE_PRICE_USD,
            TransactionType.REFUND,
            description=f"استرجاع إعلان مرفوض #{ad.id}",
            related_table="sponsored_ads",
            related_id=ad.id,
            payment_reference=f"sponsored_ad_reject:{ad.id}",
        )
        ad.status = "rejected"
        ad.admin_id = admin_id
        ad.reviewed_at = datetime.utcnow()
        ad.rejection_reason = "مرفوض من الإدارة"
        await session.commit()
        await session.refresh(ad)
        user = await session.get(User, ad.user_id)
        if user:
            await NotificationService(bot).notify_user(
                user.telegram_id,
                "↩️ <b>تم رفض إعلانك</b>\n\nيرجى تقديم إعلان أفضل. تم إعادة رسوم الإعلان إلى رصيدك.",
                notification_type="promotion",
                priority="high",
            )
        return ad

    @staticmethod
    async def renew(session, ad_id: int, user_id: int, bot: Bot) -> SponsoredAd:
        ad = await session.get(SponsoredAd, ad_id)
        if ad is None or ad.user_id != user_id:
            raise SponsoredAdError("الإعلان غير موجود.")
        if ad.status not in {"active", "expired"}:
            raise SponsoredAdError("لا يمكن تجديد هذا الإعلان حالياً.")
        try:
            await BalanceService.deduct_balance(
                session,
                user_id,
                SponsoredAdService.RENEW_PRICE_USD,
                TransactionType.PURCHASE,
                description=f"تجديد إعلان #{ad.id}",
                related_table="sponsored_ads",
                related_id=ad.id,
                is_purchase=True,
            )
        except InsufficientBalanceError as exc:
            raise SponsoredAdError(f"رصيدك غير كافٍ. سعر التجديد {SponsoredAdService.RENEW_PRICE_USD}$.") from exc
        now = datetime.utcnow()
        ad.status = "active"
        ad.renewal_count += 1
        ad.amount_paid_usd += SponsoredAdService.RENEW_PRICE_USD
        base = ad.ends_at if ad.ends_at and ad.ends_at > now else now
        ad.ends_at = base + timedelta(hours=SponsoredAdService.RENEW_HOURS)
        ad.last_channel_post_at = now
        await session.commit()
        await session.refresh(ad)
        await SponsoredAdService.publish_to_public(bot, ad)
        return ad

    @staticmethod
    async def due_ads(session) -> list[SponsoredAd]:
        cutoff = datetime.utcnow() - timedelta(minutes=SponsoredAdService.REPOST_MINUTES)
        result = await session.execute(
            select(SponsoredAd).where(
                SponsoredAd.status == "active",
                SponsoredAd.ends_at > datetime.utcnow(),
                SponsoredAd.last_channel_post_at <= cutoff,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def repost_due(session, bot: Bot) -> int:
        count = 0
        for ad in await SponsoredAdService.due_ads(session):
            if await SponsoredAdService.publish_to_public(bot, ad):
                ad.last_channel_post_at = datetime.utcnow()
                count += 1
        if count:
            await session.commit()
        return count

    @staticmethod
    async def expire_due(session) -> int:
        result = await session.execute(
            select(SponsoredAd).where(SponsoredAd.status == "active", SponsoredAd.ends_at <= datetime.utcnow())
        )
        expired = 0
        for ad in result.scalars().all():
            ad.status = "expired"
            expired += 1
        if expired:
            await session.commit()
        return expired

    @staticmethod
    async def admin_stats(session) -> dict:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        total_today = (await session.execute(select(func.count(SponsoredAd.id)).where(SponsoredAd.created_at >= today))).scalar_one()
        money_today = (await session.execute(select(func.coalesce(func.sum(SponsoredAd.amount_paid_usd), 0)).where(SponsoredAd.created_at >= today, SponsoredAd.status.in_(["active", "expired"])))) .scalar_one()
        active = (await session.execute(select(func.count(SponsoredAd.id)).where(SponsoredAd.status == "active"))).scalar_one()
        pending = (await session.execute(select(func.count(SponsoredAd.id)).where(SponsoredAd.status == "pending"))).scalar_one()
        return {"today": total_today, "money_today": money_today, "active": active, "pending": pending}
