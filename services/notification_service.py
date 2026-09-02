"""
خدمة الإشعارات المركزية:
- إرسال إشعارات للأدمن (القناة الخاصة).
- إرسال إشعارات للقناة العامة (بقالب التفعيل الجديد وزر الشراء المباشر).
- إرسال إشعارات للمستخدمين والبكاب.
"""

import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


def _mask_username(username: str | None, full_name: str | None) -> str:
    """
    يخفي اسم المستخدم جزئياً للقناة العامة.
    مثال: "Ahmed Ali" → "Ah***Ali"
    مثال: "@sanad" → "@sa***"
    """
    name = username or full_name or "مستخدم"
    if len(name) <= 3:
        return name[0] + "***"
    visible_start = name[:2]
    visible_end = name[-2:] if len(name) > 4 else ""
    return f"{visible_start}***{visible_end}"


class NotificationService:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def notify_admin(
        self,
        text: str,
        reply_markup=None,
        parse_mode: str = "HTML",
        notification_type: str = "system",
        priority: str = "high",
        dedupe_key: str | None = None,
    ) -> int | None:
        """
        يرسل إشعاراً نصياً لقناة الأدمن الخاصة.
        """
        from config import settings

        chat_id = settings.ADMIN_NOTIFY_CHAT_ID
        if not chat_id:
            logger.error("ADMIN_NOTIFY_CHAT_ID غير محدد في .env")
            return None
        try:
            sent = await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            from services.notification_center_service import NotificationCenterService

            await NotificationCenterService.record_admin(
                chat_id,
                text,
                category=notification_type,
                priority=priority,
                status="sent",
                dedupe_key=dedupe_key,
            )
            return sent.message_id
        except TelegramForbiddenError:
            logger.error(f"البوت ليس مشرفاً في قناة الأدمن (chat_id={chat_id}).")
            return None
        except TelegramBadRequest as e:
            logger.error(f"خطأ في إرسال إشعار الأدمن: {e}")
            return None
        except Exception as e:
            logger.error(f"خطأ غير متوقع في إرسال إشعار الأدمن: {e}")
            return None

    async def notify_admin_photo(
        self, photo_file_id: str, caption: str, reply_markup=None, parse_mode: str = "HTML"
    ) -> int | None:
        """
        يرسل إشعاراً بصورة لقناة الأدمن.
        """
        from config import settings

        chat_id = settings.ADMIN_NOTIFY_CHAT_ID
        if not chat_id:
            logger.error("ADMIN_NOTIFY_CHAT_ID غير محدد في .env")
            return None
        try:
            sent = await self.bot.send_photo(
                chat_id=chat_id,
                photo=photo_file_id,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return sent.message_id
        except Exception as e:
            logger.error(f"خطأ إرسال صورة للأدمن: {e}")
            return None

    async def notify_user(
        self,
        telegram_id: int,
        text: str,
        reply_markup=None,
        parse_mode: str = "HTML",
        notification_type: str = "system",
        priority: str = "normal",
        title: str | None = None,
    ) -> bool:
        """
        يرسل إشعاراً للمستخدم.
        """
        from services.notification_center_service import NotificationCenterService
        from database.engine import async_session_maker
        from database.models import User
        from sqlalchemy import select

        try:
            async with async_session_maker() as session:
                user = (
                    await session.execute(select(User).where(User.telegram_id == int(telegram_id)))
                ).scalar_one_or_none()
                user_id = user.id if user else None
                allowed = await NotificationCenterService.should_send(session, user_id, notification_type, priority)
                if not allowed:
                    return True

            await self.bot.send_message(
                chat_id=telegram_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            await NotificationCenterService.record_user_by_telegram(
                telegram_id,
                text,
                category=notification_type,
                priority=priority,
                title=title,
                status="sent",
            )
            return True
        except Exception as e:
            logger.warning(f"تعذر إرسال إشعار للمستخدم {telegram_id}: {e}")
            return False

    async def notify_public_channel(self, text: str, reply_markup=None, parse_mode: str = "HTML") -> bool:
        """
        يرسل إشعار عملية ناجحة للقناة العامة مع دعم الأزرار التفاعلية.
        """
        channel_id_str = await SettingsService.get("public_channel_id", "0")
        try:
            channel_id = int(channel_id_str)
        except (ValueError, TypeError):
            channel_id = 0

        if not channel_id:
            return False

        try:
            await self.bot.send_message(
                chat_id=channel_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return True
        except Exception as e:
            logger.error(f"خطأ في إرسال إشعار للقناة العامة: {e}")
            return False

    async def notify_backup_channel(
        self,
        document_bytes: bytes,
        filename: str,
        caption: str,
    ) -> bool:
        """
        يرسل ملف البكاب لقناة النسخ الاحتياطي.
        """
        channel_id_str = await SettingsService.get("backup_channel_id", "0")
        try:
            channel_id = int(channel_id_str)
        except (ValueError, TypeError):
            channel_id = 0

        if not channel_id:
            return False

        try:
            from aiogram.types import BufferedInputFile

            file = BufferedInputFile(document_bytes, filename=filename)
            await self.bot.send_document(
                chat_id=channel_id,
                document=file,
                caption=caption,
            )
            return True
        except Exception as e:
            logger.error(f"خطأ في إرسال البكاب: {e}")
            return False

    async def notify_successful_number_order(
        self,
        order,
        country=None,
        service=None,
        user=None,
    ) -> None:
        """
        إرسال إشعار تفعيل رقم ناجح بالقالب المطلوب مع زر شراء مباشر.
        """
        phone = str(order.phone_number or "")
        if len(phone) > 6:
            masked_phone = phone[:6] + "×××"
        else:
            masked_phone = phone + "×××"

        user_id_str = str(user.telegram_id if user else order.user_id)
        if len(user_id_str) >= 6:
            mid = user_id_str[3:6]
            masked_user = f"••{mid}••••"
        else:
            masked_user = "••••••"

        country_name = country.name_ar if country else order.country_code
        country_flag = country.flag if country else "🌍"
        service_name = service.name_ar if service else order.service
        sms_code = str(order.sms_code or "—")
        price = f"{order.price_sell_usd:.2f}"

        created_str = order.purchased_at.strftime("%Y/%m/%d %H:%M") if order.purchased_at else "—"
        completed_str = (
            order.completed_at.strftime("%Y/%m/%d %H:%M")
            if order.completed_at
            else datetime.utcnow().strftime("%Y/%m/%d %H:%M")
        )
        expires_str = (
            order.expires_at.strftime("%Y/%m/%d %H:%M")
            if order.expires_at
            else (order.purchased_at + timedelta(minutes=5)).strftime("%Y/%m/%d %H:%M")
        )

        text = (
            f"➖ رقم الطلب: <code>{masked_phone}</code> 🛎•\n"
            f"➖ الدولة : {country_name} {country_flag} •\n"
            f"➖ التفعيل : فوري تلقائي ⚡•\n"
            f"➖ السيرفر : عـروض {service_name} 🛍 🎛•\n"
            f"➖ المنصة : {service_name} 🌐•\n"
            f"➖ العميل : <code>{masked_user}</code> 🆔•\n"
            f"➖ السعر : <b>{price}$</b> 💙•\n"
            f"➖ انشاء : {created_str} 📫•\n"
            f"➖ انتهاء : {expires_str} 📭•\n"
            f"➖ الوقت المتبقي : 00:00:00 انتهى ⌛•\n"
            f"➖ الحالة : تم التفعيل بنجاح ✅•\n\n"
            f"📨 رقم الرسالة : 1️⃣\n"
            f"➕ الاستلام : {completed_str} 📥•\n"
            f"➕ المرسل : {service_name} •\n"
            f"➕ كود التفعيل : <code>{sms_code}</code> 🧿•\n"
            f"➖➖➖➖➖➖"
        )

        from services.bot_identity import resolve_bot_username

        bot_username = await resolve_bot_username(self.bot)
        deep_link = (
            f"https://t.me/{bot_username}?start=buy_{order.service}__{order.country_code}"
            if bot_username
            else ""
        )

        reply_markup = None
        if deep_link:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"⚡ اطلب رقم {service_name} ({country_name})",
                            url=deep_link,
                        )
                    ]
                ]
            )

        await self.notify_public_channel(text, reply_markup=reply_markup)

    async def notify_successful_unified_order(
        self,
        username: str | None,
        full_name: str | None,
        product_name: str,
        price_usd: str,
    ) -> None:
        """
        يرسل إشعار شراء منتج (لعبة/تطبيق/SMM) ناجح للقناة العامة.
        """
        masked = _mask_username(username, full_name)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        text = (
            "✅ <b>عملية شراء ناجحة!</b>\n\n"
            f"👤 المستخدم: {masked}\n"
            f"🛒 المنتج: {product_name}\n"
            f"💰 المبلغ: {price_usd}$\n"
            f"📅 التاريخ: {now} UTC"
        )
        await self.notify_public_channel(text)

    async def notify_successful_market_sale(
        self,
        seller_alias: str,
        listing_title: str,
        price_usd: str,
    ) -> None:
        """إشعار عام عند اكتمال بيع في سوق المستخدمين."""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        text = (
            "🏪 <b>تمت بيعة ناجحة في سوق المستخدمين!</b>\n\n"
            f"👤 التاجر: <b>{seller_alias}</b>\n"
            f"📦 المعروض: {listing_title}\n"
            f"💰 القيمة: {price_usd}$\n"
            f"📅 التاريخ: {now} UTC\n\n"
            "✅ تم تأكيد العملية بنجاح عبر ضمان البوت."
        )
        await self.notify_public_channel(text)

    async def notify_provider_low_balance(
        self,
        provider_name: str,
        balance: str,
        threshold: str,
    ) -> None:
        text = (
            "⚠️ <b>تنبيه: رصيد منخفض!</b>\n\n"
            f"🔌 المزود: {provider_name}\n"
            f"💰 الرصيد الحالي: {balance}$\n"
            f"🚨 الحد الأدنى المحدد: {threshold}$\n\n"
            "يرجى شحن رصيد المزود في أقرب وقت."
        )
        await self.notify_admin(text)

    async def notify_provider_offline(
        self,
        provider_name: str,
        error: str,
    ) -> None:
        text = (
            "🔴 <b>تنبيه: مزود غير متاح!</b>\n\n"
            f"🔌 المزود: {provider_name}\n"
            f"⚠️ الخطأ: {error[:200]}\n\n"
            "تم تعطيل المزود تلقائياً حتى يعود للعمل."
        )
        await self.notify_admin(text)

    async def notify_new_deposit(
        self,
        user_telegram_id: int,
        username: str | None,
        amount_usd: str,
        tx_number: str,
        deposit_id: int,
        photo_file_id: str,
        reply_markup,
    ) -> int | None:
        caption = (
            "🆕 <b>طلب شحن رصيد جديد</b>\n\n"
            f"👤 المستخدم: {user_telegram_id} (@{username or '-'})\n"
            f"💵 المبلغ: <b>{amount_usd}$</b>\n"
            f"🔢 رقم العملية: <code>{tx_number}</code>\n"
            f"🆔 رقم الطلب: #{deposit_id}\n"
            f"⏰ الوقت: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
        )
        return await self.notify_admin_photo(
            photo_file_id=photo_file_id,
            caption=caption,
            reply_markup=reply_markup,
        )

    async def notify_deposit_approved(
        self,
        user_telegram_id: int,
        amount_usd: str,
    ) -> None:
        await self.notify_user(
            telegram_id=user_telegram_id,
            text=(
                "✅ <b>تم قبول طلب شحن رصيدك!</b>\n\n"
                f"💰 تمت إضافة <b>{amount_usd}$</b> إلى رصيدك.\n"
                "يمكنك الآن استخدام رصيدك لشراء الخدمات."
            ),
        )

    async def notify_deposit_rejected(
        self,
        user_telegram_id: int,
        reason: str | None = None,
    ) -> None:
        text = "❌ <b>تم رفض طلب شحن رصيدك</b>\n\n"
        if reason:
            text += f"📝 السبب: {reason}\n\n"
        text += "يرجى التأكد من صحة بيانات التحويل والمحاولة مجدداً، أو التواصل مع الدعم الفني."
        await self.notify_user(
            telegram_id=user_telegram_id,
            text=text,
        )

    async def notify_order_completed(
        self,
        user_telegram_id: int,
        product_name: str,
        result_text: str,
    ) -> None:
        await self.notify_user(
            telegram_id=user_telegram_id,
            text=(f"✅ <b>تم تنفيذ طلبك بنجاح!</b>\n\n🛒 المنتج: {product_name}\n\n{result_text}"),
        )

    async def notify_order_failed(
        self,
        user_telegram_id: int,
        product_name: str,
        amount_usd: str,
    ) -> None:
        await self.notify_user(
            telegram_id=user_telegram_id,
            text=(
                "❌ <b>فشل تنفيذ طلبك</b>\n\n"
                f"🛒 المنتج: {product_name}\n"
                f"💰 تم استرجاع <b>{amount_usd}$</b> إلى رصيدك تلقائياً."
            ),
        )

    async def notify_insufficient_balance(
        self,
        user_telegram_id: int,
        required_usd: str,
        current_balance_usd: str,
        reply_markup=None,
    ) -> None:
        await self.notify_user(
            telegram_id=user_telegram_id,
            text=(
                "⚠️ <b>رصيدك غير كافٍ!</b>\n\n"
                f"💰 رصيدك الحالي: <b>{current_balance_usd}$</b>\n"
                f"💵 المبلغ المطلوب: <b>{required_usd}$</b>\n\n"
                "اشحن رصيدك للمتابعة."
            ),
            reply_markup=reply_markup,
        )

    async def notify_large_order_confirmation(
        self,
        user_telegram_id: int,
        product_name: str,
        amount_usd: str,
        reply_markup,
    ) -> None:
        await self.notify_user(
            telegram_id=user_telegram_id,
            text=(
                "⚠️ <b>تأكيد الطلب</b>\n\n"
                f"🛒 المنتج: {product_name}\n"
                f"💰 المبلغ: <b>{amount_usd}$</b>\n\n"
                "هذا طلب بمبلغ كبير. هل أنت متأكد من المتابعة؟"
            ),
            reply_markup=reply_markup,
        )