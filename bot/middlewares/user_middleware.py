"""
يضمن وجود المستخدم بقاعدة البيانات قبل أي معالجة.
يربط الإحالة إذا دخل عبر رابط ref_<telegram_id>.
يمنع المستخدم المحظور.
يمنع أي تفاعل أثناء وضع الصيانة (ما عدا الأدمن).
يحدّث last_activity_at.

تحسين الأداء (إصلاح):
كان الملف يعمل session.commit() عند كل رسالة أو ضغطة زر
لمجرد تحديث last_activity_at، حتى لو لم يتغير شيء آخر.
على SQLite هذا يعني كتابة + قفل لقاعدة البيانات مع كل تفاعل،
فيتأخر البوت ويظهر "الرجاء المحاولة لاحقاً" تحت الضغط.
الآن:
- لا يُكتب last_activity_at إلا إذا مرّت 60 ثانية على آخر تحديث.
- لا يُنفَّذ commit إلا إذا تغيّرت قيمة فعلية.
"""

from datetime import datetime, timedelta

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from database.models import User
from services.settings_service import SettingsService

# لا نكتب في قاعدة البيانات أكثر من مرة كل دقيقة للمستخدم الواحد.
_LAST_ACTIVITY_THROTTLE = timedelta(seconds=60)


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

        # ── تحديث بيانات المستخدم (فقط عند تغيّر فعلي) ──
        changed = False
        if user.username != tg_user.username:
            user.username = tg_user.username
            changed = True
        if user.full_name != tg_user.full_name:
            user.full_name = tg_user.full_name
            changed = True
        language_code = (tg_user.language_code or "ar")[:8]
        if user.language_code != language_code:
            user.language_code = language_code
            changed = True

        # ── النشاط الأخير: مكتوب مرة كل 60 ثانية كحد أقصى ──
        now = datetime.utcnow()
        last_activity = user.last_activity_at
        if last_activity is None or (now - last_activity) >= _LAST_ACTIVITY_THROTTLE:
            user.last_activity_at = now
            changed = True

        if changed:
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
