"""تذكير السلة المتروكة.

الزبون يجمع منتجاته ثم يخرج وينسى. هذه الخدمة ترسل له تذكيراً واحداً
بعد مدة ضبطها الأدمن (افتراضياً ٦ ساعات) فيه محتوى سلتـه ومجموعها
وزرّا إتمام الشراء أو تفريغ السلة.

قاعدة «تذكير واحد»: لا نُرسل إلا إن كانت السلة متروكة فعلاً
(updated_at أقدم من المهلة) ولم تُذكَّر بعد — أو تغيّرت بعد آخر تذكير.
هكذا لا يتحوّل التذكير إلى إزعاج متكرر.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from database.engine import async_session_maker
from database.models import CartItem, Product, ProductStatus, User
from services.cart_service import CartService
from services.feature_service import FeatureService
from services.html_guard import esc

logger = logging.getLogger(__name__)

FEATURE_KEY = "cart_reminder"


class CartReminderService:
    """اختيار السلات المتروكة وإرسال التذكير."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY, default=True)

    @staticmethod
    async def delay_hours() -> int:
        try:
            value = int(await FeatureService.config_int(FEATURE_KEY, "delay_hours", 6))
        except (TypeError, ValueError):
            value = 6
        return max(1, min(value, 72))

    @staticmethod
    def _item_total(item: CartItem) -> Decimal:
        """سعر العنصر — بلا استثناء: منتج لا يقبل كمية متعددة لا يمنع التذكير."""
        try:
            return Decimal(str(CartService.item_total(item)))
        except Exception:  # noqa: BLE001
            price = Decimal(str(getattr(item.product, "price_usd", 0) or 0))
            return price * Decimal(str(item.quantity or 1))

    @classmethod
    def _safe_total(cls, items: list[CartItem], total: Decimal) -> Decimal:
        try:
            return Decimal(str(total))
        except Exception:  # noqa: BLE001
            return sum((cls._item_total(item) for item in items), Decimal("0"))

    @classmethod
    def compose_message(cls, items: list[CartItem], total: Decimal | None = None) -> str:
        lines = ["🛒 <b>سلتك ما زالت بانتظارك!</b>", ""]
        for item in items:
            name = item.product.name_ar if item.product else "منتج"
            lines.append(
                f"• {esc(str(name))} × {item.quantity} — <b>{cls._item_total(item):.2f}$</b>"
            )
        lines.append("")
        lines.append(f"💵 المجموع: <b>{cls._safe_total(items, total):.2f}$</b>")
        lines.append("")
        lines.append("⏳ أكمل الشراء قبل نفاد الكمية — أو فرّغ السلة إن غيّرت رأيك.")
        return "\n".join(lines)

    @staticmethod
    def markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛒 إتمام الشراء",
                        callback_data="menu:cart",
                        style="success",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 تفريغ السلة",
                        callback_data="cart:clear",
                        style="danger",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 القائمة الرئيسية",
                        callback_data="back_to_main",
                    )
                ],
            ]
        )

    @staticmethod
    async def due_carts(session, cutoff: datetime) -> dict[int, list[CartItem]]:
        """السلات التي انقضت مهلتها ولم تُذكَّر (مرتّبة حسب المستخدم)."""
        rows = list(
            (
                await session.execute(
                    select(CartItem)
                    .options(selectinload(CartItem.product))
                    .join(Product, CartItem.product_id == Product.id)
                    .where(
                        Product.status == ProductStatus.ACTIVE,
                        CartItem.updated_at <= cutoff,
                        or_(
                            CartItem.reminder_sent_at.is_(None),
                            CartItem.reminder_sent_at < CartItem.updated_at,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        grouped: dict[int, list[CartItem]] = {}
        for item in rows:
            grouped.setdefault(item.user_id, []).append(item)
        return grouped

    @classmethod
    async def cycle(cls, bot) -> int:
        """يرسل التذكيرات المستحقة ويعيد عددها."""
        if not await cls.enabled():
            return 0
        delay = await cls.delay_hours()
        cutoff = datetime.utcnow() - timedelta(hours=delay)

        async with async_session_maker() as session:
            grouped = await cls.due_carts(session, cutoff)
            if not grouped:
                return 0
            pending: list[tuple[int, int, str, Decimal]] = []
            item_ids: list[int] = []
            for user_id, items in grouped.items():
                user = await session.get(User, user_id)
                if user is None or getattr(user, "is_banned", False):
                    continue
                try:
                    total = CartService.total(items)
                except Exception:  # noqa: BLE001
                    total = None
                pending.append((user_id, user.telegram_id, cls.compose_message(items, total), total))
                item_ids.extend(item.id for item in items)

        if not pending:
            return 0

        from services.notification_service import NotificationService

        notifier = NotificationService(bot)
        sent = 0
        for user_id, telegram_id, text, _total in pending:
            try:
                ok = await notifier.notify_user(telegram_id, text, reply_markup=cls.markup())
            except Exception:  # noqa: BLE001 - مستخدم حاظر البوت أو محذوف
                ok = False
            if ok:
                sent += 1
            else:
                logger.debug("تعذّر تذكير السلة للمستخدم %s", user_id)

        if item_ids:
            async with async_session_maker() as session:
                # func.now() لا datetime.utcnow(): العمود updated_at يُحدَّث
                # بقاعدة البيانات نفسها (onupdate)، فلو كتبنا وقت بايثون قبله
                # لظهر reminder_sent_at أقدم بجزء من الثانية وظلّ التذكير
                # مستحقاً كل دورة.
                rows = (
                    await session.execute(
                        select(CartItem).where(CartItem.id.in_(item_ids))
                    )
                ).scalars().all()
                for row in rows:
                    row.reminder_sent_at = func.now()
                await session.commit()
        return sent

    @staticmethod
    async def stats(session) -> dict:
        """أرقام للوحة الأدمن: كم سلة متروكة وكم ذُكِّر."""
        try:
            total_carts = int(
                await session.scalar(
                    select(func.count(func.distinct(CartItem.user_id)))
                )
                or 0
            )
            reminded = int(
                await session.scalar(
                    select(func.count(CartItem.id)).where(
                        CartItem.reminder_sent_at.is_not(None)
                    )
                )
                or 0
            )
        except Exception:  # noqa: BLE001
            return {}
        return {"carts": total_carts, "reminded_items": reminded}
