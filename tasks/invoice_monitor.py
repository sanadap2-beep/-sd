"""
مراقبة الفواتير التلقائية (Sam API + Plisio).

المهام:
1) فحص فواتير USDT عبر Plisio Polling كل 30 ثانية.
2) فحص فواتير شام كاش (Sam API) - تتم يدوياً من المستخدم.
3) تحديث حالة الفواتير المدفوعة/الفاشلة/المنتهية.
4) إضافة الرصيد تلقائياً عند نجاح الدفع.
5) تنظيف الفواتير المنتهية.
6) إشعار المستخدم والأدمن بالنتائج.
"""

import json
import logging
from datetime import datetime

from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    AutoInvoice,
    AutoInvoiceMethod,
    AutoInvoiceStatus,
    TransactionType,
    User,
)
from services.balance_service import BalanceService
from services.notification_service import NotificationService
from services.plisio_service import (
    plisio_client,
    PlisioError,
)
from keyboards.main_menu import back_to_main_kb

logger = logging.getLogger(__name__)


async def check_pending_invoices(bot):
    """
    مهمة رئيسية تُشغَّل كل 30 ثانية.
    تفحص كل الفواتير المعلّقة.
    """
    async with async_session_maker() as session:
        result = await session.execute(
            select(AutoInvoice).where(AutoInvoice.status == AutoInvoiceStatus.PENDING)
        )
        invoices = result.scalars().all()

        if not invoices:
            return

        logger.debug(f"فحص {len(invoices)} فاتورة معلّقة...")

        for invoice in invoices:
            try:
                if datetime.utcnow() > invoice.expires_at:
                    await _expire_invoice(session, invoice, bot)
                    continue

                if invoice.method == AutoInvoiceMethod.USDT_AUTO:
                    await _check_usdt_invoice(session, invoice, bot)
                elif invoice.method == AutoInvoiceMethod.SHAMCASH_AUTO:
                    # شام كاش يعتمد على التحقق اليدوي
                    # من المستخدم (يرسل رقم العملية)
                    # لذلك لا نفحصها هنا
                    pass

            except Exception as e:
                logger.error(f"خطأ فحص الفاتورة #{invoice.id}: {e}")


async def _check_usdt_invoice(session, invoice: AutoInvoice, bot):
    """يفحص فاتورة USDT عبر Plisio."""
    try:
        info = await plisio_client.get_payment_info(uuid=invoice.external_invoice_id)
    except PlisioError as e:
        logger.warning(f"فشل فحص فاتورة #{invoice.id}: {e}")
        return

    status = info.get("status", "process")

    if plisio_client.is_paid_status(status):
        await _process_paid_usdt_invoice(session, invoice, info, bot)
    elif plisio_client.is_failed_status(status):
        await _process_failed_invoice(session, invoice, bot, "فشل الدفع")


async def _process_paid_usdt_invoice(session, invoice: AutoInvoice, info: dict, bot):
    """يعالج فاتورة USDT مدفوعة."""
    if invoice.status == AutoInvoiceStatus.PAID:
        return

    # أضف الرصيد أولاً. في حال فشل قاعدة البيانات تبقى الفاتورة معلّقة
    # ويستطيع المراقب إعادة المحاولة بدلاً من فقدان الدفعة.
    user = await BalanceService.add_balance(
        session,
        invoice.user_id,
        invoice.amount_usd,
        TransactionType.DEPOSIT,
        description=(f"شحن USDT تلقائي - فاتورة #{invoice.id}"),
        related_table="auto_invoices",
        related_id=invoice.id,
        payment_reference=f"invoice:{invoice.id}",
    )

    invoice.status = AutoInvoiceStatus.PAID
    invoice.paid_at = datetime.utcnow()
    invoice.raw_data = json.dumps(info, ensure_ascii=False, default=str)
    await session.commit()

    notifier = NotificationService(bot)

    await notifier.notify_user(
        user.telegram_id,
        f"✅ <b>تم شحن رصيدك بنجاح!</b>\n\n"
        f"₮ المبلغ المستلم: <b>{invoice.amount_original} USDT</b>\n"
        f"💰 المضاف للرصيد: <b>{invoice.amount_usd}$</b>\n\n"
        f"يمكنك الآن استخدام رصيدك.",
    )

    await notifier.notify_admin(
        f"₮ <b>شحن USDT تلقائي (Plisio)</b>\n\n"
        f"👤 المستخدم: {user.telegram_id} "
        f"(@{user.username or '-'})\n"
        f"💵 المبلغ: <b>{invoice.amount_usd}$</b>\n"
        f"💰 USDT: {invoice.amount_original}\n"
        f"🌐 الشبكة: TRC20\n"
        f"🆔 فاتورة: #{invoice.id}"
    )

    if invoice.status_chat_id and invoice.status_message_id:
        try:
            await bot.edit_message_text(
                chat_id=invoice.status_chat_id,
                message_id=invoice.status_message_id,
                text=(
                    "✅ <b>تم استلام الدفع بنجاح!</b>\n\n"
                    f"💰 أُضيف <b>{invoice.amount_usd}$</b> "
                    f"إلى رصيدك."
                ),
                reply_markup=back_to_main_kb(),
            )
        except TelegramBadRequest:
            pass

    logger.info(f"فاتورة USDT #{invoice.id} مدفوعة ({invoice.amount_usd}$)")


async def _process_failed_invoice(session, invoice: AutoInvoice, bot, reason: str):
    """يعالج فاتورة فاشلة."""
    if invoice.status != AutoInvoiceStatus.PENDING:
        return

    invoice.status = AutoInvoiceStatus.FAILED
    await session.commit()

    user = await session.get(User, invoice.user_id)
    if not user:
        return

    notifier = NotificationService(bot)
    text = f"❌ <b>فشلت الفاتورة</b>\n\nالسبب: {reason}\nيمكنك إنشاء فاتورة جديدة."

    if invoice.status_chat_id and invoice.status_message_id:
        try:
            await bot.edit_message_text(
                chat_id=invoice.status_chat_id,
                message_id=invoice.status_message_id,
                text=text,
                reply_markup=back_to_main_kb(),
            )
            return
        except TelegramBadRequest:
            pass

    await notifier.notify_user(user.telegram_id, text)


async def _expire_invoice(session, invoice: AutoInvoice, bot):
    """يعالج فاتورة منتهية الصلاحية."""
    if invoice.status != AutoInvoiceStatus.PENDING:
        return

    # قبل تسجيلها كمنتهية، نفحصها مرة أخيرة
    # لعل الدفع تم في اللحظات الأخيرة
    if invoice.method == AutoInvoiceMethod.USDT_AUTO:
        try:
            info = await plisio_client.get_payment_info(uuid=invoice.external_invoice_id)
            status = info.get("status", "")
            if plisio_client.is_paid_status(status):
                await _process_paid_usdt_invoice(session, invoice, info, bot)
                return
        except Exception:
            pass

    invoice.status = AutoInvoiceStatus.EXPIRED
    await session.commit()

    user = await session.get(User, invoice.user_id)
    if not user:
        return

    text = "⌛ <b>انتهت صلاحية الفاتورة</b>\n\nيمكنك إنشاء فاتورة جديدة إذا رغبت."

    if invoice.status_chat_id and invoice.status_message_id:
        try:
            await bot.edit_message_text(
                chat_id=invoice.status_chat_id,
                message_id=invoice.status_message_id,
                text=text,
                reply_markup=back_to_main_kb(),
            )
            return
        except TelegramBadRequest:
            pass

    notifier = NotificationService(bot)
    await notifier.notify_user(user.telegram_id, text)

    logger.info(f"فاتورة #{invoice.id} انتهت صلاحيتها.")
