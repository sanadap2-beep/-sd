"""
يضمن وجود المستخدم بقاعدة البيانات قبل أي معالجة.
يربط الإحالة إذا دخل عبر رابط ref_<telegram_id>.
يمنع المستخدم المحظور.
يمنع أي تفاعل أثناء وضع الصيانة (ما عدا الأدمن).
يحدّث last_activity_at.
"""

from datetime import datetime

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from database.models import User
from services.settings_service import SettingsService


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
            if isinstance(event, Message) and event.text and event.text.startswith("/start ref_"):
                try:
                    ref_tg_id = int(event.text.split("ref_")[1])
                    ref_result = await session.execute(
                        select(User).where(User.telegram_id == ref_tg_id)
                    )
                    ref_user = ref_result.scalar_one_or_none()
                    if ref_user and ref_user.telegram_id != tg_user.id:
                        referrer_id = ref_user.id
                except (ValueError, IndexError):
                    pass

            user = User(
                telegram_id=tg_user.id,
                username=tg_user.username,
                full_name=tg_user.full_name,
                language_code=(tg_user.language_code or "ar")[:8],
                referrer_id=referrer_id,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

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

        data["db_user"] = user
        return await handler(event, data)
