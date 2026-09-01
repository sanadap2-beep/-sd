"""
خدمة نجوم تليجرام.
تنشئ فواتير الدفع بالنجوم وتعالج الدفع الناجح.
"""

import logging
from decimal import Decimal, InvalidOperation

from aiogram import Bot
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery

from database.models import TransactionType
from services.balance_service import BalanceService
from services.dynamic_service import DynamicService
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class StarsService:
    @staticmethod
    async def send_stars_invoice(
        bot: Bot,
        chat_id: int,
        package_id: int,
        session,
    ) -> bool:
        """
        يرسل فاتورة دفع بالنجوم للمستخدم.
        يرجع True إذا نجح، False إذا فشل.
        """
        package = await DynamicService.get_stars_package(session, package_id)
        if package is None or not package.is_active:
            return False

        try:
            await bot.send_invoice(
                chat_id=chat_id,
                title=package.label,
                description=(
                    f"شحن رصيد بقيمة {package.usd_amount}$ مقابل {package.stars_amount} نجمة"
                ),
                payload=(f"stars_pkg:{package.id}:{package.stars_amount}:{package.usd_amount}"),
                currency="XTR",
                prices=[
                    LabeledPrice(
                        label=package.label,
                        amount=package.stars_amount,
                    )
                ],
            )
            return True
        except Exception as e:
            logger.error(f"فشل إرسال فاتورة النجوم: {e}")
            return False

    @staticmethod
    async def handle_pre_checkout(
        pre_checkout_query: PreCheckoutQuery,
    ) -> None:
        """
        يوافق على الدفع قبل اكتماله.
        يجب الرد خلال 10 ثوانٍ.
        """
        await pre_checkout_query.answer(ok=True)

    @staticmethod
    async def handle_successful_payment(
        message: Message,
        session,
        db_user,
        bot: Bot,
    ) -> Decimal:
        """
        يعالج الدفع الناجح بالنجوم.
        يضيف الرصيد للمستخدم ويرسل إشعارات.
        يرجع المبلغ المضاف بالدولار.
        """
        payment = message.successful_payment
        payload = payment.invoice_payload

        if not payload.startswith("stars_pkg:"):
            logger.error(f"payload غير معروف: {payload}")
            return Decimal("0")

        parts = payload.split(":")
        try:
            package_id = int(parts[1])
            invoice_stars = int(parts[2])
            invoice_usd = Decimal(parts[3])
            if not invoice_usd.is_finite() or invoice_usd <= 0:
                raise ValueError
        except (ValueError, IndexError, InvalidOperation):
            logger.error(f"payload غير صالح: {payload}")
            return Decimal("0")

        package = await DynamicService.get_stars_package(session, package_id)
        if package is None:
            logger.error(f"باقة النجوم {package_id} غير موجودة")
            return Decimal("0")

        if (
            payment.currency != "XTR"
            or invoice_stars != package.stars_amount
            or payment.total_amount != invoice_stars
            or not payment.telegram_payment_charge_id
        ):
            logger.error(
                "بيانات دفع نجوم غير مطابقة للباقة "
                f"{package_id}: currency={payment.currency}, "
                f"amount={payment.total_amount}"
            )
            return Decimal("0")

        amount_usd = invoice_usd

        await BalanceService.add_balance(
            session=session,
            user_id=db_user.id,
            amount=amount_usd,
            tx_type=TransactionType.STARS_DEPOSIT,
            description=(f"شحن بنجوم تليجرام - {payment.total_amount} نجمة"),
            related_table="stars_payments",
            related_id=package.id,
            payment_reference=(f"telegram_stars:{payment.telegram_payment_charge_id}"),
        )

        notifier = NotificationService(bot)

        await notifier.notify_user(
            telegram_id=db_user.telegram_id,
            text=(
                "✅ <b>تم شحن رصيدك بنجاح!</b>\n\n"
                f"⭐ النجوم المدفوعة: "
                f"{payment.total_amount}\n"
                f"💰 الرصيد المضاف: "
                f"<b>{amount_usd}$</b>\n\n"
                "يمكنك الآن استخدام رصيدك."
            ),
        )

        await notifier.notify_admin(
            text=(
                "⭐ <b>شحن بنجوم تليجرام</b>\n\n"
                f"👤 المستخدم: {db_user.telegram_id} "
                f"(@{db_user.username or '-'})\n"
                f"⭐ النجوم: {payment.total_amount}\n"
                f"💰 المبلغ: {amount_usd}$"
            ),
        )

        logger.info(
            f"دفع نجوم ناجح: المستخدم {db_user.telegram_id}, "
            f"{payment.total_amount} نجمة, {amount_usd}$"
        )

        return amount_usd
