"""العملاء النائمون: تذكير «اشتقنا لك» لمن غاب عن المتجر.

الزبون الذي لم يطلب منذ مدة (افتراضياً ٣٠ يوماً) يستحق رسالة واحدة
تعيدة — لا أكثر. الرسالة تُرسل مرة واحدة ثم تنتظر فترة تهدئة
(افتراضياً ٣٠ يوماً) قبل أن تُعاد، حتى لا يتحوّل التذكير إلى إزعاج.

المستخدم الجديد الذي سجّل ولم يشترِ يُعتبر نائماً after المهلة نفسها.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, or_, select

from database.engine import async_session_maker
from database.models import NumberOrder, UnifiedOrder, User
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)

FEATURE_KEY = "dormant_users"


class DormantUserService:
    """اختيار النائمين وإرسال تذكير واحد لكل واحد."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY, default=True)

    @staticmethod
    async def inactive_days() -> int:
        try:
            value = int(await FeatureService.config_int(FEATURE_KEY, "inactive_days", 30))
        except (TypeError, ValueError):
            value = 30
        return max(7, min(value, 365))

    @staticmethod
    async def cooldown_days() -> int:
        try:
            value = int(await FeatureService.config_int(FEATURE_KEY, "cooldown_days", 30))
        except (TypeError, ValueError):
            value = 30
        return max(1, min(value, 365))

    @staticmethod
    def compose_message(language: str | None = None) -> str:
        ar = not str(language or "").startswith("en")
        if ar:
            return (
                "👋 <b>اشتقنا لك!</b>\n\n"
                "مرّ وقت منذ آخر طلب لك من المتجر.\n"
                "ما زالت أرقامنا وشحن الألعاب والاشتراكات جاهزة — "
                "والتسليم فوري في كثير من الخدمات.\n\n"
                "🛍 ادخل الآن وشوف الجديد."
            )
        return (
            "👋 <b>We missed you!</b>\n\n"
            "It has been a while since your last order.\n"
            "Numbers, game top-ups and subscriptions are ready — "
            "many of them delivered instantly.\n\n"
            "🛍 Tap below to browse what's new."
        )

    @staticmethod
    def markup(language: str | None = None) -> InlineKeyboardMarkup:
        ar = not str(language or "").startswith("en")
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛍 تصفّح المتجر" if ar else "🛍 Browse store",
                        callback_data="store:home",
                        style="success",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📞 اطلب رقم" if ar else "📞 Buy a number",
                        callback_data="num_hub",
                        style="success",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 القائمة الرئيسية" if ar else "🔙 Main menu",
                        callback_data="back_to_main",
                    )
                ],
            ]
        )

    @classmethod
    async def due_users(cls, session, cutoff: datetime, cooldown_cutoff: datetime) -> list[User]:
        """المستخدمون الذين انقطعوا قبل ``cutoff`` ولم يُذكَّروا مؤخراً."""
        last_unified = (
            select(UnifiedOrder.user_id, func.max(UnifiedOrder.created_at).label("last_at"))
            .group_by(UnifiedOrder.user_id)
            .subquery()
        )
        last_number = (
            select(NumberOrder.user_id, func.max(NumberOrder.purchased_at).label("last_at"))
            .group_by(NumberOrder.user_id)
            .subquery()
        )
        rows = list(
            (
                await session.execute(
                    select(User)
                    .outerjoin(last_unified, last_unified.c.user_id == User.id)
                    .outerjoin(last_number, last_number.c.user_id == User.id)
                    .where(
                        User.is_banned.is_(False),
                        User.joined_at <= cutoff,
                        # لا طلب متجر حديث… (الاستعلام الفرعي مجمّع: صف واحد لكل مستخدم)
                        func.coalesce(last_unified.c.last_at, User.joined_at) <= cutoff,
                        # …ولا طلب رقم حديث…
                        func.coalesce(last_number.c.last_at, User.joined_at) <= cutoff,
                        # …ولا حتى فتح البوت مؤخراً.
                        or_(
                            User.last_activity_at.is_(None),
                            User.last_activity_at <= cutoff,
                        ),
                        or_(
                            User.dormant_reminder_at.is_(None),
                            User.dormant_reminder_at <= cooldown_cutoff,
                        ),
                    )
                    .order_by(User.id)
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )
        return rows

    @classmethod
    async def cycle(cls, bot, limit: int | None = None) -> int:
        """يرسل التذكيرات المستحقة ويعيد عددها."""
        if not await cls.enabled():
            return 0
        days = await cls.inactive_days()
        cooldown = await cls.cooldown_days()
        now = datetime.utcnow()
        cutoff = now - timedelta(days=days)
        cooldown_cutoff = now - timedelta(days=cooldown)
        if limit is None:
            try:
                limit = int(await FeatureService.config_int(FEATURE_KEY, "max_per_run", 40))
            except (TypeError, ValueError):
                limit = 40

        async with async_session_maker() as session:
            users = await cls.due_users(session, cutoff, cooldown_cutoff)
            if not users:
                return 0
            targets = [(user.id, user.telegram_id, user.language_code) for user in users[:limit]]

        from services.notification_service import NotificationService

        notifier = NotificationService(bot)
        sent_ids: list[int] = []
        for user_id, telegram_id, language in targets:
            try:
                ok = await notifier.notify_user(
                    telegram_id,
                    cls.compose_message(language),
                    reply_markup=cls.markup(language),
                )
            except Exception:  # noqa: BLE001
                ok = False
            if ok:
                sent_ids.append(user_id)

        if sent_ids:
            async with async_session_maker() as session:
                now2 = datetime.utcnow()
                rows = (
                    await session.execute(select(User).where(User.id.in_(sent_ids)))
                ).scalars().all()
                for row in rows:
                    row.dormant_reminder_at = now2
                await session.commit()
        return len(sent_ids)
