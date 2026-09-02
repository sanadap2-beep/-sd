"""
يضمن وجود المستخدم بقاعدة البيانات قبل أي معالجة.
يربط الإحالة إذا دخل عبر رابط ref_<telegram_id>.
يمنع المستخدم المحظور.
يمنع أي تفاعل أثناء وضع الصيانة (ما عدا الأدمن).
يحدّث last_activity_at.
"""

import logging
import re
from datetime import datetime

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from database.models import User
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

# /start ref_123  |  /start@BotName ref_123  |  /start ref_123 extra
_REFERRAL_START_RE = re.compile(
    r"^/start(?:@[A-Za-z0-9_]+)?(?:\s+|_)ref_(\d+)",
    re.IGNORECASE,
)


def extract_referrer_telegram_id(text: str | None) -> int | None:
    """Parse a deep-link payload such as ``/start ref_6707747395``."""
    if not text:
        return None
    match = _REFERRAL_START_RE.match(text.strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        session = data["session"]
        tg_user = data.get("event_from_user")

        if tg_user is None:
            return await handler(event, data)

        result = await session.execute(select(User).where(User.telegram_id == tg_user.id))
        user = result.scalar_one_or_none()

        # ── إنشاء مستخدم جديد ──
        if user is None:
            referrer_id = None
            referral_guard_pending = False
            if isinstance(event, Message):
                ref_tg_id = extract_referrer_telegram_id(event.text)
                if ref_tg_id and ref_tg_id != tg_user.id:
                    ref_result = await session.execute(
                        select(User).where(User.telegram_id == ref_tg_id)
                    )
                    ref_user = ref_result.scalar_one_or_none()
                    if ref_user is not None:
                        referrer_id = ref_user.id
                        # القادمون عبر روابط الإحالة يمرون بفحص بشري قبل
                        # التفعيل ومكافأة المحيل (حماية الإحالة من البوتات).
                        from services.referral_guard_service import ReferralGuardService

                        referral_guard_pending = await ReferralGuardService.enabled()

            user = User(
                telegram_id=tg_user.id,
                username=tg_user.username,
                full_name=tg_user.full_name,
                language_code=(tg_user.language_code or "ar")[:8],
                referrer_id=referrer_id,
                referral_check_pending=referral_guard_pending,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            await _notify_new_user_join(
                data.get("bot"),
                session,
                tg_user,
                referrer_id,
                defer_referrer_notice=referral_guard_pending,
            )

        # ── تحديث بيانات المستخدم ──
        updated = False
        if user.username != tg_user.username:
            user.username = tg_user.username
            updated = True
        if user.full_name != tg_user.full_name:
            user.full_name = tg_user.full_name
            updated = True
        language_code = (tg_user.language_code or "ar")[:8]
        if user.language_code != language_code:
            user.language_code = language_code
            updated = True
        user.last_activity_at = datetime.utcnow()
        updated = True
        if updated:
            await session.commit()

        # ── فحص الحظر ──
        if user.is_banned:
            if isinstance(event, Message):
                await event.answer(
                    "🚫 تم حظرك من استخدام البوت.\nتواصل مع الدعم الفني إذا كنت تعتقد أن هذا خطأ."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "🚫 تم حظرك من استخدام البوت.",
                    show_alert=True,
                )
            return

        # ── فحص وضع الصيانة ──
        if not user.is_admin:
            maintenance_mode = await SettingsService.get_bool("maintenance_mode", False)
            if maintenance_mode:
                maintenance_msg = await SettingsService.get(
                    "maintenance_message", "⚙️ البوت تحت الصيانة حالياً، سيعود قريباً..."
                )
                if isinstance(event, Message):
                    await event.answer(maintenance_msg)
                elif isinstance(event, CallbackQuery):
                    await event.answer(
                        maintenance_msg,
                        show_alert=True,
                    )
                return

        # ── حماية الإحالة: انتظار التحقق البشري ──
        # المسموح فقط: /start وأزرار الاختبار نفسها (rg:ans:). أي تفاعل آخر
        # محجوب حتى يجتاز المستخدم الفحص (يمنع البوتات من استعمال المتجر
        # قبل اكتشافها، ويؤخر مكافأة/إشعار المحيل حتى التأكد).
        if user.referral_check_pending and not user.is_admin:
            from services.referral_guard_service import ReferralGuardService

            if await ReferralGuardService.enabled():
                if isinstance(event, Message):
                    text = event.text or ""
                    if not text.startswith("/start"):
                        await event.answer(
                            "🛡 <b>أكمل التحقق البشري أولاً</b>\n\n"
                            "أرسل /start واختر الرقم المطلوب لإثبات أنك إنسان."
                        )
                        return
                elif isinstance(event, CallbackQuery):
                    if not (event.data or "").startswith("rg:ans:"):
                        await event.answer(
                            "🛡 أكمل التحقق البشري أولاً — أرسل /start",
                            show_alert=True,
                        )
                        return

        data["db_user"] = user
        return await handler(event, data)


async def _notify_new_user_join(
    bot,
    session,
    tg_user,
    referrer_id: int | None,
    defer_referrer_notice: bool = False,
) -> None:
    """Tell the admin channel (and the referrer) about a first-time join. Fail-open.

    When the joiner came through a referral and must pass the human check, the
    referrer's "new join" notice is deferred until verification succeeds, so
    bot traffic never reaches the referrer.
    """
    if bot is None:
        return
    try:
        from services.notification_service import NotificationService

        referrer = await session.get(User, referrer_id) if referrer_id else None
        notifier = NotificationService(bot)
        await notifier.notify_admin_new_user(
            telegram_id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name,
            via_referral=referrer is not None,
            referrer_telegram_id=referrer.telegram_id if referrer else None,
            referrer_username=referrer.username if referrer else None,
        )
        if referrer is not None and not defer_referrer_notice:
            await notifier.notify_referrer_new_join(
                referrer.telegram_id,
                referrer.language_code,
                tg_user.id,
                tg_user.username,
                tg_user.full_name,
            )
    except Exception:
        logger.exception("Failed to notify about new user %s", getattr(tg_user, "id", None))
