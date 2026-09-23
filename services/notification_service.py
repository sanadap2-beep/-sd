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

from services.html_guard import esc
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


def _mask_link(link: str | None) -> str:
    """إخفاء الرابط للقناة العامة: 7 نقاط + آخر 10 أحرف."""
    s = (link or "").strip()
    if not s:
        return "—"
    tail = s[-10:] if len(s) >= 10 else s
    return f"{'•' * 7}{tail}"


def _mask_customer(telegram_id) -> str:
    """إخفاء آيدي العميل: أول 4 أرقام + •••• + آخر رقمين."""
    digits = "".join(ch for ch in str(telegram_id or "") if ch.isdigit())
    if len(digits) >= 6:
        return f"{digits[:4]}••••{digits[-2:]}"
    return "••••••"


def mask_ready_phone(phone: str) -> str:
    """إخفاء رقم الجلسة الجاهزة: مقدمة الدولة ظاهرة والباقي مخفي."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) < 6:
        return "••••••"
    hidden = max(3, len(digits) - 6)
    return f"+{digits[:4]}{'•' * hidden}{digits[-2:]}"


def mask_ready_buyer(telegram_id) -> str:
    """إخفاء آيدي مشتري الجلسة: أول رقمين + •••• + آخر رقمين."""
    digits = "".join(ch for ch in str(telegram_id or "") if ch.isdigit())
    if len(digits) >= 6:
        return f"{digits[:2]}••••{digits[-2:]}"
    return "••••••"


def format_ready_price(price_usd) -> str:
    from decimal import Decimal, InvalidOperation

    try:
        return f"{Decimal(str(price_usd)):.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return "0.00"


def build_tg_ready_public_text(
    *,
    country_name: str,
    country_flag: str,
    price_usd,
    remaining: int,
    phone_number: str,
    buyer_telegram_id,
) -> str:
    """قالب إشعار القناة العامة بعد وصول كود الجلسة الجاهزة بنجاح."""
    sep = "▬" * 16
    try:
        left = max(0, int(remaining))
    except (TypeError, ValueError):
        left = 0
    flag = country_flag or "🌍"
    name = country_name or "غير معروف"
    return (
        "🔔 <b>تفعيل جلسة تلجرام جاهزة</b>\n"
        f"{sep}\n"
        f"🌍 الدولة : {esc(flag)} {esc(name)}\n"
        f"💰 السعر : <b>{format_ready_price(price_usd)}$</b>\n"
        f"📦 الكمية المتبقية : <b>{left}</b>\n"
        f"📱 الرقم : <code>{esc(mask_ready_phone(phone_number))}</code>\n"
        f"🆔 المشتري : <code>{esc(mask_ready_buyer(buyer_telegram_id))}</code>\n"
        "✅ الحالة : وصل الكود بنجاح\n"
        f"{sep}\n"
        "⚡️ تسليم فوري — حساب جاهز للدخول"
    )


# عناصر أُعلن عنها في هذه العملية — يمنع النشر المزدوج قبل ما يُحفظ المفتاح.
_TG_READY_ANNOUNCED: set[int] = set()


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

    async def notify_code_card(
        self,
        telegram_id: int,
        service_name: str,
        country_name: str,
        phone_number: str,
        code: str,
        extra: str | None = None,
        caption: str | None = None,
        reply_markup=None,
    ) -> bool:
        """
        يسلم الكود كبطاقة صورة منسقة إذا فُعّلت ميزة تسليم الكود كصورة،
        وإلا يعود للنص العادي. يرجع True إذا صدرت الصورة.
        """
        from services.code_screenshot_service import CodeScreenshotService

        if await CodeScreenshotService.enabled():
            png = await CodeScreenshotService.build_image(
                service_name=service_name,
                country_name=country_name,
                phone_number=phone_number,
                code=code,
                extra_lines=[extra] if extra else None,
            )
            if png:
                try:
                    from aiogram.types import BufferedInputFile

                    await self.bot.send_photo(
                        chat_id=telegram_id,
                        photo=BufferedInputFile(png, filename="code.png"),
                        caption=caption or f"📱 <b>{service_name}</b>",
                        reply_markup=reply_markup,
                        parse_mode="HTML",
                    )
                    return True
                except Exception as e:
                    logger.warning(f"تعذر إرسال بطاقة الكود للمستخدم {telegram_id}: {e}")
        await self.notify_user(
            telegram_id,
            CodeScreenshotService.fallback_text(service_name, phone_number, code, extra),
            reply_markup=reply_markup,
        )
        return False

    async def notify_order_review_prompt(
        self,
        telegram_id: int,
        order_id: int,
        provider: str,
    ) -> bool:
        """
        دعوة تقييم المزود بعد اكتمال الطلب — مرة واحدة لكل طلب.
        """
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        return await self.notify_user(
            telegram_id,
            (
                "⭐ <b>كيف كانت تجربتك مع المزود؟</b>\n\n"
                f"اطلب #{order_id} · المزود: <b>{provider}</b>\n"
                "قيّم ب 1-5 نجوم ليساعدنا على تحسين جودة الخدمات."
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⭐ قيّم المزود", callback_data=f"engage:review_order:{order_id}", style="primary")]
                ]
            ),
            notification_type="order",
        )

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
        chat_id_override: int | str | None = None,
    ) -> bool:
        """
        يرسل ملف البكاب لقناة النسخ الاحتياطي.
        chat_id_override: إن حُدد يتجاوز backup_channel_id (ميزة db_backup_telegram).
        """
        from config import settings

        raw_id = chat_id_override if chat_id_override else await SettingsService.get("backup_channel_id", "0")
        try:
            channel_id = int(raw_id)
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

        from services.bot_identity import number_buy_start_link, resolve_bot_username

        bot_username = await resolve_bot_username(self.bot)
        deep_link = (
            number_buy_start_link(bot_username, order.service, country.id)
            if bot_username and country is not None
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

    async def notify_successful_tg_ready(
        self,
        *,
        country_name: str,
        country_flag: str,
        price_usd,
        remaining: int,
        phone_number: str,
        buyer_telegram_id,
        item_id: int,
        country_key: str | None = None,
    ) -> bool:
        """إشعار القناة العامة عند وصول كود جلسة جاهزة بنجاح — مرة واحدة لكل رقم."""
        try:
            item_key = int(item_id)
        except (TypeError, ValueError):
            return False
        dedupe = f"tgready_public:{item_key}"
        if item_key in _TG_READY_ANNOUNCED:
            return False
        from services.notification_center_service import NotificationCenterService

        if await NotificationCenterService.was_sent(dedupe):
            _TG_READY_ANNOUNCED.add(item_key)
            return False

        text = build_tg_ready_public_text(
            country_name=country_name,
            country_flag=country_flag,
            price_usd=price_usd,
            remaining=remaining,
            phone_number=phone_number,
            buyer_telegram_id=buyer_telegram_id,
        )
        reply_markup = None
        try:
            from services.bot_identity import resolve_bot_username, tg_ready_start_link

            username = await resolve_bot_username(self.bot)
            link = tg_ready_start_link(username, country_key)
            if link:
                label = f"⚡ اطلب جلسة {country_flag or ''} {country_name or ''}".strip()
                if len(label) > 60:
                    label = "⚡ اطلب جلسة تلجرام جاهزة"
                reply_markup = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text=label, url=link)]]
                )
        except Exception:
            reply_markup = None

        ok = await self.notify_public_channel(text, reply_markup=reply_markup)
        if not ok:
            return False
        _TG_READY_ANNOUNCED.add(item_key)
        try:
            from database.engine import async_session_maker

            async with async_session_maker() as session:
                await NotificationCenterService.record(
                    session,
                    channel="public",
                    category="order",
                    priority="normal",
                    title="تفعيل جلسة تلجرام جاهزة",
                    body=text,
                    status="sent",
                    dedupe_key=dedupe,
                )
        except Exception:
            logger.warning("تعذر تسجيل إشعار الجلسة الجاهزة في السجل، والمنشور أُرسل.")
        return True

    async def notify_successful_unified_order(
        self,
        username: str | None,
        full_name: str | None,
        product_name: str,
        price_usd: str,
        *,
        order_id: int | None = None,
        quantity: int | None = None,
        target: str | None = None,
        app_name: str | None = None,
        section_name: str | None = None,
        service_name: str | None = None,
        user_telegram_id=None,
        is_smm: bool = False,
    ) -> None:
        """
        يرسل إشعار شراء منتج (لعبة/تطبيق/SMM) ناجح للقناة العامة.

        طلبات الرشق (is_smm=True) تُنشر بالقالب الموحد:
        التطبيق/القسم/الخدمة/رقم الطلب/العدد/السعر بالنقاط/الرابط والعميل
        (مخفيان جزئياً).
        """
        if is_smm and order_id is not None:
            from decimal import Decimal, InvalidOperation

            try:
                price_label = f"{Decimal(str(price_usd)).normalize():f}"
            except (InvalidOperation, ValueError, AttributeError):
                price_label = str(price_usd)
            sep = "▬" * 16
            text = (
                "🔔 <b>عملية رشق جديدة</b>\n"
                f"{sep}\n"
                f"🎬 التطبيق : {esc(app_name or '—')}\n"
                f"🧩 القسم : {esc(section_name or '—')}\n"
                f"🛒 الخدمة : {esc(service_name or product_name)}\n"
                f"🆔 رقم الطلب : {order_id}\n"
                f"🗣️ العدد المطلوب : {quantity if quantity is not None else '—'}\n"
                f"💵 سعر الطلب : {price_label}$\n"
                f"🔗 الرابط : {esc(_mask_link(target))}\n"
                f"🆔 العميل : {esc(_mask_customer(user_telegram_id))}\n"
                f"{sep}"
            )
            await self.notify_public_channel(text)
            return
        masked = _mask_username(username, full_name)
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        text = (
            "✅ <b>عملية شراء ناجحة!</b>\n\n"
            f"👤 المستخدم: {esc(masked)}\n"
            f"🛒 المنتج: {esc(product_name)}\n"
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
        bonus_usd: str | None = None,
    ) -> None:
        text = "✅ <b>تم قبول طلب شحن رصيدك!</b>\n\n" f"💰 تمت إضافة <b>{amount_usd}$</b> إلى رصيدك.\n"
        if bonus_usd:
            text += f"🎁 <b>+{bonus_usd}$</b> مكافأة شحن!\n"
        text += "يمكنك الآن استخدام رصيدك لشراء الخدمات."
        await self.notify_user(
            telegram_id=user_telegram_id,
            text=text,
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

    async def _live_feed_active(self, kind: str = "success") -> bool:
        """هل إشعارات «مباشر البوت» مفعّلة لهذا النوع من الأحداث؟

        تُدار من لوحة الأدمن: زر «📡 مباشر البوت» (والإضافة
        ``live_bot_feed`` في مركز الإضافات تحت فئة «الإشعارات»).
        """
        try:
            from services.feature_service import FeatureService

            if not await FeatureService.enabled("live_bot_feed", default=True):
                return False
            option = "notify_success" if kind == "success" else "notify_refund"
            return bool(
                await FeatureService.config_bool("live_bot_feed", option, True)
            )
        except Exception:
            return False

    async def live_purchase_success(
        self,
        *,
        telegram_id: int,
        username: str | None = None,
        full_name: str | None = None,
        item: str,
        amount_usd,
        order_id: int | None = None,
    ) -> None:
        """إشعار مباشر للأدمن: شراء ناجح — خُصم رصيد المستخدم وتُفعِّل/اكتمل.

        يُرسل فقط للوحة الأدمن (وليس للقناة العامة)، ويفهم منه صاحب البوت
        أن العملية تمت فعلاً لا أنها مجرد طلب معلّق.
        """
        if not await self._live_feed_active("success"):
            return
        name = full_name or username or ""
        text = (
            "✅ <b>مباشر البوت — شراء مكتمل</b>\n\n"
            f"👤 المستخدم: <code>{telegram_id}</code> "
            f"{f'(@{esc(username)})' if username else ''} {esc(name[:40])}\n"
            f"📦 المنتج: {esc(item)}\n"
            f"💰 خُصم من رصيده: <b>{amount_usd}$</b>"
        )
        if order_id is not None:
            text += f"\n🆔 الطلب: #{order_id}"
        await self.notify_admin(text, notification_type="live", priority="high")

    async def live_refund(
        self,
        *,
        telegram_id: int,
        username: str | None = None,
        full_name: str | None = None,
        item: str,
        amount_usd,
        reason: str,
        order_id: int | None = None,
    ) -> None:
        """إشعار مباشر للأدمن: استرجاع/فشل — رجع الرصيد للمستخدم."""
        if not await self._live_feed_active("refund"):
            return
        name = full_name or username or ""
        text = (
            "↩️ <b>مباشر البوت — استرجاع رصيد</b>\n\n"
            f"👤 المستخدم: <code>{telegram_id}</code> "
            f"{f'(@{esc(username)})' if username else ''} {esc(name[:40])}\n"
            f"📦 المنتج: {esc(item)}\n"
            f"💵 المبلغ المسترجع: <b>{amount_usd}$</b>\n"
            f"📄 السبب: {esc(reason)}"
        )
        if order_id is not None:
            text += f"\n🆔 الطلب: #{order_id}"
        await self.notify_admin(text, notification_type="live", priority="high")

    async def notify_admin_new_user(
        self,
        telegram_id: int,
        username: str | None,
        full_name: str | None,
        *,
        via_referral: bool,
        referrer_telegram_id: int | None = None,
        referrer_username: str | None = None,
    ) -> None:
        """Alert the admin channel whenever someone opens the bot for the first time."""
        source = (
            f"🔗 رابط إحالة من <code>{referrer_telegram_id}</code> "
            f"(@{esc(referrer_username or '-')})"
            if via_referral
            else "🚪 دخول عادي (بدون إحالة)"
        )
        text = (
            "👤 <b>مستخدم جديد دخل البوت</b>\n\n"
            f"🆔 الآيدي: <code>{telegram_id}</code>\n"
            f"👤 الاسم: {esc(full_name or '—')}\n"
            f"🔗 يوزر: @{esc(username or '-')}\n"
            f"📥 المصدر: {source}"
        )
        await self.notify_admin(text, notification_type="users", priority="normal")

    async def notify_referrer_new_join(
        self,
        referrer_telegram_id: int,
        referrer_language: str | None,
        new_telegram_id: int,
        new_username: str | None,
        new_full_name: str | None,
    ) -> None:
        """Tell the referrer immediately that someone used their link."""
        from services.i18n_service import I18nService

        await self.notify_user(
            referrer_telegram_id,
            I18nService.t(
                "referral_join_notification",
                referrer_language,
                name=esc(new_full_name or "مستخدم"),
                username=esc(new_username or "-"),
                user_id=str(new_telegram_id),
            ),
            notification_type="referral",
            priority="normal",
            title="إحالة جديدة",
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