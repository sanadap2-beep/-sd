"""
خدمة الإشعارات المركزية.

مسؤوليات هذا الملف:
1) إرسال إشعارات للأدمن (قناة خاصة).
2) إرسال إشعارات للقناة العامة (عمليات ناجحة).
3) إرسال إشعارات للمستخدمين.
4) إرسال إشعارات البكاب.
5) إخفاء اسم المستخدم جزئياً في القناة العامة.
"""

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

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
        يرسل إشعاراً نصياً لقناة الأدمن.
        يرجع message_id إذا نجح، أو None إذا فشل.
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
            logger.error(
                f"البوت محظور أو ليس أدمن في قناة الأدمن (chat_id={chat_id}). "
                "تأكد أن البوت أدمن في القناة."
            )
            from services.notification_center_service import NotificationCenterService
            await NotificationCenterService.record_admin(chat_id, text, category=notification_type, priority=priority, status="failed", dedupe_key=dedupe_key, error="forbidden")
            return None
        except TelegramBadRequest as e:
            logger.error(f"خطأ في إرسال إشعار الأدمن (chat_id={chat_id}): {e}")
            from services.notification_center_service import NotificationCenterService
            await NotificationCenterService.record_admin(chat_id, text, category=notification_type, priority=priority, status="failed", dedupe_key=dedupe_key, error=str(e)[:255])
            return None
        except Exception as e:
            logger.error(f"خطأ غير متوقع في إرسال إشعار الأدمن: {e}")
            from services.notification_center_service import NotificationCenterService
            await NotificationCenterService.record_admin(chat_id, text, category=notification_type, priority=priority, status="failed", dedupe_key=dedupe_key, error=str(e)[:255])
            return None

    async def notify_admin_photo(
        self, photo_file_id: str, caption: str, reply_markup=None, parse_mode: str = "HTML"
    ) -> int | None:
        """
        يرسل إشعاراً بصورة لقناة الأدمن.
        يرجع message_id إذا نجح، أو None إذا فشل.
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
        except TelegramForbiddenError:
            logger.error(
                f"البوت محظور أو ليس أدمن في قناة الأدمن (chat_id={chat_id}). "
                "تأكد أن البوت أدمن في القناة."
            )
            return None
        except TelegramBadRequest as e:
            logger.error(f"خطأ في إرسال صورة إشعار الأدمن (chat_id={chat_id}): {e}")
            return None
        except Exception as e:
            logger.error(f"خطأ غير متوقع في إرسال صورة إشعار الأدمن: {e}")
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
        يرجع True إذا نجح، False إذا فشل.
        """
        from services.notification_center_service import NotificationCenterService

        try:
            # نحترم تفضيلات المستخدم قبل الإرسال، إلا للإشعارات الحرجة/المالية.
            from database.engine import async_session_maker
            from database.models import User
            from sqlalchemy import select

            async with async_session_maker() as session:
                user = (
                    await session.execute(select(User).where(User.telegram_id == int(telegram_id)))
                ).scalar_one_or_none()
                user_id = user.id if user else None
                allowed = await NotificationCenterService.should_send(session, user_id, notification_type, priority)
                if not allowed:
                    await NotificationCenterService.record(
                        session,
                        user_id=user_id,
                        recipient_chat_id=int(telegram_id),
                        channel="user",
                        category=notification_type,
                        priority=priority,
                        title=title,
                        body=text,
                        status="skipped",
                    )
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
        except TelegramForbiddenError:
            logger.warning(f"المستخدم {telegram_id} حظر البوت، تخطي الإشعار.")
            await NotificationCenterService.record_user_by_telegram(
                telegram_id, text, category=notification_type, priority=priority, title=title, status="failed", error="forbidden"
            )
            return False
        except TelegramBadRequest as e:
            logger.warning(f"خطأ في إرسال إشعار للمستخدم {telegram_id}: {e}")
            await NotificationCenterService.record_user_by_telegram(
                telegram_id, text, category=notification_type, priority=priority, title=title, status="failed", error=str(e)[:255]
            )
            return False
        except Exception as e:
            logger.error(f"خطأ غير متوقع في إرسال إشعار للمستخدم {telegram_id}: {e}")
            await NotificationCenterService.record_user_by_telegram(
                telegram_id, text, category=notification_type, priority=priority, title=title, status="failed", error=str(e)[:255]
            )
            return False

    async def notify_public_channel(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        يرسل إشعار عملية ناجحة للقناة العامة.
        يرجع True إذا نجح، False إذا فشل أو القناة غير محددة.
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
                parse_mode=parse_mode,
            )
            return True
        except TelegramForbiddenError:
            logger.error(f"البوت محظور أو ليس أدمن في القناة العامة (chat_id={channel_id}).")
            return False
        except TelegramBadRequest as e:
            logger.error(f"خطأ في إرسال إشعار للقناة العامة: {e}")
            return False
        except Exception as e:
            logger.error(f"خطأ غير متوقع في إرسال إشعار للقناة العامة: {e}")
            return False

    async def notify_backup_channel(
        self,
        document_bytes: bytes,
        filename: str,
        caption: str,
    ) -> bool:
        """
        يرسل ملف البكاب لقناة البكاب.
        يرجع True إذا نجح، False إذا فشل.
        """
        channel_id_str = await SettingsService.get("backup_channel_id", "0")
        try:
            channel_id = int(channel_id_str)
        except (ValueError, TypeError):
            channel_id = 0

        if not channel_id:
            logger.warning("backup_channel_id غير محدد، تخطي البكاب.")
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
        username: str | None,
        full_name: str | None,
        service_name: str,
        price_usd: str,
    ) -> None:
        """
        يرسل إشعار شراء رقم ناجح للقناة العامة.
        """
        masked = _mask_username(username, full_name)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        text = (
            "✅ <b>عملية شراء ناجحة!</b>\n\n"
            f"👤 المستخدم: {masked}\n"
            f"📞 الخدمة: {service_name}\n"
            f"💰 المبلغ: {price_usd}$\n"
            f"📅 التاريخ: {now} UTC"
        )
        await self.notify_public_channel(text)

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
        """إشعار عام عند اكتمال بيع في سوق المستخدمين بعد تأكيد الصحة/الإفراج."""
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
        """
        يرسل تنبيه لقناة الأدمن عند انخفاض رصيد مزود عن الحد المحدد.
        """
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
        """
        يرسل تنبيه لقناة الأدمن عند توقف مزود عن الاستجابة.
        """
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
        """
        يرسل إشعار إيداع جديد لقناة الأدمن مع صورة الإثبات وأزرار القبول/الرفض.
        يرجع message_id الرسالة في القناة.
        """
        caption = (
            "🆕 <b>طلب شحن رصيد جديد</b>\n\n"
            f"👤 المستخدم: {user_telegram_id}"
            f" (@{username or '-'})\n"
            f"💵 المبلغ: <b>{amount_usd}$</b>\n"
            f"🔢 رقم العملية: <code>{tx_number}</code>\n"
            f"🆔 رقم الطلب: #{deposit_id}\n"
            f"⏰ الوقت: "
            f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
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
        """يرسل إشعار قبول الإيداع للمستخدم."""
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
        """يرسل إشعار رفض الإيداع للمستخدم."""
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
        """يرسل إشعار اكتمال طلب للمستخدم."""
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
        """يرسل إشعار فشل طلب مع استرجاع الرصيد للمستخدم."""
        await self.notify_user(
            telegram_id=user_telegram_id,
            text=(
                "❌ <b>فشل تنفيذ طلبك</b>\n\n"
                f"🛒 المنتج: {product_name}\n"
                f"💰 تم استرجاع <b>{amount_usd}$</b> "
                "إلى رصيدك تلقائياً."
            ),
        )

    async def notify_insufficient_balance(
        self,
        user_telegram_id: int,
        required_usd: str,
        current_balance_usd: str,
        reply_markup=None,
    ) -> None:
        """يرسل إشعار رصيد غير كافٍ للمستخدم مع زر شحن الرصيد."""
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
        """يطلب تأكيد الطلبات الكبيرة من المستخدم."""
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
