"""
إدارة الأدمنية المتعددة من لوحة الأدمن.
الأدمن الرئيسي (من ADMIN_IDS في .env) محمي ولا يمكن إزالته.
"""

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from config import settings
from database.models import User
from states.states import AdminMultiAdminStates
from keyboards.admin import (
    admin_multi_admin_kb,
    admin_madmin_detail_kb,
    admin_back_kb,
)
from filters.admin_filter import IsAdmin

logger = logging.getLogger(__name__)

router = Router(name="admin_multi_admin")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════ قائمة الأدمنية ══════════════


@router.callback_query(F.data == "admin:multi_admin")
async def multi_admin_list(callback: CallbackQuery, session):
    result = await session.execute(select(User).where(User.is_admin.is_(True)))
    admins = result.scalars().all()

    await callback.message.edit_text(
        "👨‍💼 <b>إدارة الأدمنية</b>\n\n"
        f"عدد الأدمنية الحاليين: {len(admins)}\n"
        "⭐ = أدمن رئيسي (محمي من الحذف)",
        reply_markup=admin_multi_admin_kb(admins),
    )


# ══════════════ تفاصيل أدمن ══════════════


@router.callback_query(F.data.startswith("admin:madmin_view:"))
async def madmin_view(callback: CallbackQuery, session):
    user_id = int(callback.data.split(":")[2])
    user = await session.get(User, user_id)
    if not user:
        await callback.answer("⚠️ المستخدم غير موجود.", show_alert=True)
        return

    is_primary = user.telegram_id in settings.admin_ids_list
    primary_label = "⭐ أدمن رئيسي (محمي)" if is_primary else "👤 أدمن عادي"

    await callback.message.edit_text(
        f"👨‍💼 <b>تفاصيل الأدمن</b>\n\n"
        f"🆔 آيدي: {user.telegram_id}\n"
        f"👤 يوزر: @{user.username or '-'}\n"
        f"📛 الاسم: {user.full_name or '-'}\n"
        f"💰 الرصيد: {user.balance:.2f}$\n"
        f"🏅 النوع: {primary_label}\n"
        f"📅 الانضمام: {user.joined_at.strftime('%Y-%m-%d')}",
        reply_markup=admin_madmin_detail_kb(user, is_primary),
    )


# ══════════════ إضافة أدمن ══════════════


@router.callback_query(F.data == "admin:madmin_add")
async def madmin_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "➕ <b>إضافة أدمن جديد</b>\n\n"
        "أرسل آيدي المستخدم (Telegram ID) "
        "الذي تريد منحه صلاحيات الأدمن:\n\n"
        "⚠️ تأكد أن المستخدم قد تفاعل مع البوت مسبقاً.",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminMultiAdminStates.waiting_admin_id)


@router.message(AdminMultiAdminStates.waiting_admin_id)
async def madmin_add_received(message: Message, state: FSMContext, session, db_user: User):
    try:
        tg_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ أرسل آيدي صحيح (أرقام فقط).")
        return

    if tg_id == db_user.telegram_id:
        await message.answer("⚠️ أنت أدمن بالفعل!")
        await state.clear()
        return

    result = await session.execute(select(User).where(User.telegram_id == tg_id))
    target_user = result.scalar_one_or_none()

    if target_user is None:
        await message.answer(
            "⚠️ لا يوجد مستخدم بهذا الآيدي في البوت.\nيجب أن يكون المستخدم قد تفاعل مع البوت أولاً."
        )
        await state.clear()
        return

    if target_user.is_admin:
        await message.answer(f"⚠️ المستخدم {tg_id} هو أدمن بالفعل.")
        await state.clear()
        return

    target_user.is_admin = True
    await session.commit()

    logger.info(f"الأدمن {db_user.telegram_id} منح صلاحيات أدمن للمستخدم {tg_id}")

    await message.answer(
        f"✅ تم منح صلاحيات الأدمن للمستخدم "
        f"{target_user.full_name or tg_id} "
        f"(@{target_user.username or '-'}) بنجاح.\n\n"
        "يمكنه الآن الوصول للوحة التحكم عبر /admin"
    )
    await state.clear()


# ══════════════ إزالة أدمن ══════════════


@router.callback_query(F.data.startswith("admin:madmin_remove:"))
async def madmin_remove(callback: CallbackQuery, session, db_user: User):
    user_id = int(callback.data.split(":")[2])
    target_user = await session.get(User, user_id)

    if not target_user:
        await callback.answer("⚠️ المستخدم غير موجود.", show_alert=True)
        return

    if target_user.telegram_id in settings.admin_ids_list:
        await callback.answer(
            "⛔ لا يمكن إزالة الأدمن الرئيسي.",
            show_alert=True,
        )
        return

    if target_user.id == db_user.id:
        await callback.answer(
            "⛔ لا يمكنك إزالة نفسك.",
            show_alert=True,
        )
        return

    target_user.is_admin = False
    await session.commit()

    logger.info(
        f"الأدمن {db_user.telegram_id} أزال صلاحيات أدمن من المستخدم {target_user.telegram_id}"
    )

    await callback.answer(f"✅ تم إزالة صلاحيات الأدمن من {target_user.telegram_id}.")
    await multi_admin_list(callback, session)
