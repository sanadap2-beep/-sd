"""كوبون ترحيبي تلقائي عند أول إيداع.

أول إيداع مقبول للمستخدم = لحظة ذهبية: نعطيه كوبون خصم شخصياً
(نسبة أو مبلغ ثابت، بحد أدنى للإيداع ومدة صلاحية) مع إشعار فيه
الكود وزر الدخول للمتجر.

الكلمة الفصل للأدمن: الميزة مسجّلة في سجل الإضافات
(``welcome_coupon``) فيُطفئها أو يضبطها من اللوحة.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from database.models import Coupon, DepositRequest, DepositStatus
from services.coupon_service import CouponService
from services.feature_service import FeatureService
from services.html_guard import esc

logger = logging.getLogger(__name__)

FEATURE_KEY = "welcome_coupon"


class WelcomeCouponService:
    """منح كوبون ترحيبي عند أول إيداع مقبول."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY, default=True)

    @staticmethod
    async def _unique_code(session, user_id: int) -> str:
        """كود قصير ومضمون التفرد: WLCM-<معرّف>-<٣ أحرف>."""
        for _attempt in range(5):
            suffix = secrets.token_hex(2).upper()
            code = f"WLCM{int(user_id) % 10000:04d}{suffix}"
            exists = await CouponService.get_coupon_by_code(session, code)
            if exists is None:
                return code
        return f"WLCM{int(user_id) % 10000:04d}{secrets.token_hex(3).upper()}"

    @staticmethod
    async def _creator_id(session, user_id: int) -> int:
        """منشئ الكوبون: أدمن إن وُجد في قاعدة البيانات، وإلا المستخدم نفسه."""
        try:
            from config import settings

            for raw in str(getattr(settings, "ADMIN_IDS", "") or "").split(","):
                raw = raw.strip()
                if not raw:
                    continue
                from database.models import User

                admin = (
                    await session.execute(
                        select(User).where(User.telegram_id == int(raw))
                    )
                ).scalar_one_or_none()
                if admin is not None:
                    return int(admin.id)
        except Exception:  # noqa: BLE001
            pass
        return int(user_id)

    @classmethod
    async def grant_for_first_deposit(cls, session, user, deposit, bot=None) -> Coupon | None:
        """يمنح الكوبون إن كان هذا أول إيداع مقبول ويستوفي الحد الأدنى."""
        if not await cls.enabled():
            return None

        try:
            min_deposit = Decimal(
                str(await FeatureService.config(FEATURE_KEY, "min_deposit_usd", 5) or 0)
            )
        except Exception:  # noqa: BLE001
            min_deposit = Decimal("5")
        try:
            amount = Decimal(str(getattr(deposit, "amount_usd", 0) or 0))
        except Exception:  # noqa: BLE001
            return None
        if amount < min_deposit:
            return None

        # أول إيداع مقبول؟ (نستثني هذا الطلب نفسه بعد تغيير حالته)
        earlier = await session.scalar(
            select(func.count(DepositRequest.id)).where(
                DepositRequest.user_id == user.id,
                DepositRequest.status == DepositStatus.APPROVED,
                DepositRequest.id != deposit.id,
            )
        )
        if int(earlier or 0) > 0:
            return None

        try:
            percent = int(await FeatureService.config(FEATURE_KEY, "discount_percent", 5) or 0)
        except (TypeError, ValueError):
            percent = 5
        percent = max(0, min(percent, 90))
        if percent <= 0:
            return None

        try:
            days = int(await FeatureService.config(FEATURE_KEY, "valid_days", 7) or 0)
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(days, 90))

        try:
            min_order = Decimal(
                str(await FeatureService.config(FEATURE_KEY, "min_order_usd", 0) or 0)
            )
        except Exception:  # noqa: BLE001
            min_order = Decimal("0")

        code = await cls._unique_code(session, user.id)
        try:
            coupon = await CouponService.create_coupon(
                session,
                code=code,
                discount_type="percent",
                discount_value=Decimal(percent),
                max_uses=1,
                created_by=await cls._creator_id(session, user.id),
                min_order_usd=min_order,
                expires_at=datetime.utcnow() + timedelta(days=days),
            )
        except Exception:  # noqa: BLE001 - الكوبون هدية: لا يُفشل الإيداع
            logger.exception("تعذّر إنشاء كوبون ترحيبي للمستخدم %s", getattr(user, "id", "?"))
            return None

        if bot is not None:
            try:
                from services.notification_service import NotificationService

                language = getattr(user, "language_code", "ar") or "ar"
                await NotificationService(bot).notify_user(
                    getattr(user, "telegram_id", None),
                    cls.compose_message(language, code, percent, days, min_order),
                    reply_markup=cls.markup(language),
                )
            except Exception:  # noqa: BLE001
                logger.debug("تعذّر إشعار الكوبون الترحيبي")
        return coupon

    @staticmethod
    def compose_message(
        language: str, code: str, percent: int, days: int, min_order: Decimal
    ) -> str:
        ar = not str(language or "").startswith("en")
        if ar:
            note = (
                f"يصلح على طلبات أكثر من <b>{min_order:.2f}$</b>."
                if min_order and min_order > 0
                else "يصلح على أي طلب."
            )
            return (
                "🎁 <b>هدية أول إيداع</b>\n\n"
                "شكراً لثقتك بنا! خصم خاص على طلبك القادم:\n\n"
                f"🏷 الكود: <code>{esc(code)}</code>\n"
                f"💸 الخصم: <b>{percent}%</b>\n"
                f"⏳ صالح لمدة <b>{days}</b> يوماً — {note}"
            )
        note = (
            f"Valid on orders above <b>{min_order:.2f}$</b>."
            if min_order and min_order > 0
            else "Valid on any order."
        )
        return (
            "🎁 <b>First-deposit gift</b>\n\n"
            "Thanks for trusting us! A discount for your next order:\n\n"
            f"🏷 Code: <code>{esc(code)}</code>\n"
            f"💸 Discount: <b>{percent}%</b>\n"
            f"⏳ Valid for <b>{days}</b> days — {note}"
        )

    @staticmethod
    def markup(language: str) -> InlineKeyboardMarkup:
        ar = not str(language or "").startswith("en")
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛍 تسوّق الآن" if ar else "🛍 Shop now",
                        callback_data="store:home",
                        style="success",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🛒 سلتك" if ar else "🛒 Your cart",
                        callback_data="menu:cart",
                        style="primary",
                    )
                ],
            ]
        )
