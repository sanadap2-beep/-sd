"""
إدارة المستخدمين.
"""

from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select, or_

from database.models import User, TransactionType
from services.balance_service import BalanceService, InsufficientBalanceError
from services.notification_service import NotificationService
from states.states import (
    AdminUserSearchStates,
    AdminSendMessageStates,
)
from keyboards.admin import user_manage_kb, admin_back_kb
from filters.admin_filter import IsAdmin

router = Router(name="admin_users")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:users")
async def users_search_start(callback: CallbackQuery, state: FSMContext, session):
    total = (await session.execute(select(func.count(User.id)))).scalar_one()
    banned = (await session.execute(select(func.count(User.id)).where(User.is_banned.is_(True)))).scalar_one()
    admins = (await session.execute(select(func.count(User.id)).where(User.is_admin.is_(True)))).scalar_one()
    await callback.message.edit_text(
        "👥 <b>إدارة المستخدمين</b>\n\n"
        f"عدد المستخدمين: <b>{total}</b>\n"
        f"المجمدين/المحظورين: <b>{banned}</b>\n"
        f"الأدمنز: <b>{admins}</b>\n\n"
        "أرسل آيدي المستخدم، يوزر (مع @ أو بدونه)، أو اسم للبحث.\n"
        "أو اختر من القائمة:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 قائمة المستخدمين", callback_data="admin:users_list:0")],
            [InlineKeyboardButton(text="🚫 المحظورين فقط", callback_data="admin:users_filter:banned:0", style="danger")],
            [InlineKeyboardButton(text="👑 الأدمنز فقط", callback_data="admin:users_filter:admin:0")],
            [InlineKeyboardButton(text="📥 تصدير المستخدمين", callback_data="admin:users_export")],
            [InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:main")],
        ]),
    )
    await state.set_state(AdminUserSearchStates.waiting_user_id)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:users_filter:"))
async def users_filtered(callback: CallbackQuery, session):
    parts = callback.data.split(":")
    filt, page = parts[2], int(parts[3])
    per_page = 10
    base = select(User)
    if filt == "banned":
        base = base.where(User.is_banned.is_(True))
    elif filt == "admin":
        base = base.where(User.is_admin.is_(True))
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    users = list((await session.execute(
        base.order_by(User.joined_at.desc()).limit(per_page).offset(page * per_page)
    )).scalars().all())
    rows = [
        [InlineKeyboardButton(
            text=f"{'🚫' if user.is_banned else '👤'} {user.telegram_id} · {user.balance:.2f}$",
            callback_data=f"admin:user_open:{user.id}",
        )]
        for user in users
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ السابق", callback_data=f"admin:users_filter:{filt}:{page - 1}"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton(text="التالي ▶️", callback_data=f"admin:users_filter:{filt}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:users")])
    await callback.message.edit_text(
        f"📋 <b>قائمة المستخدمين ({filt})</b>\n"
        f"الإجمالي: {total}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:users_list:"))
async def users_list(callback: CallbackQuery, session):
    page = int(callback.data.rsplit(":", 1)[1])
    per_page = 10
    total = (await session.execute(select(func.count(User.id)))).scalar_one()
    users = list((await session.execute(
        select(User).order_by(User.joined_at.desc()).limit(per_page).offset(page * per_page)
    )).scalars().all())
    rows = [
        [InlineKeyboardButton(
            text=f"{'🚫' if user.is_banned else '👤'} {user.telegram_id} · {user.balance:.2f}$",
            callback_data=f"admin:user_open:{user.id}",
        )]
        for user in users
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ السابق", callback_data=f"admin:users_list:{page - 1}"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton(text="التالي ▶️", callback_data=f"admin:users_list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:users")])
    await callback.message.edit_text(
        f"📋 <b>قائمة المستخدمين</b>\n\nعدد المستخدمين: <b>{total}</b>\nالصفحة: {page + 1}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:user_open:"))
async def user_open(callback: CallbackQuery, session):
    user_id = int(callback.data.rsplit(":", 1)[1])
    user = await session.get(User, user_id)
    if user is None:
        await callback.answer("المستخدم غير موجود.", show_alert=True)
        return
    await callback.message.edit_text(
        "👤 <b>معلومات المستخدم</b>\n\n"
        f"🆔 آيدي: {user.telegram_id}\n"
        f"👤 يوزر: @{user.username or '-'}\n"
        f"📛 الاسم: {user.full_name or '-'}\n"
        f"💰 الرصيد: <b>{user.balance:.2f}$</b>\n"
        f"🛒 إجمالي الشراء: {user.total_spent_usd:.2f}$\n"
        f"📦 عدد الطلبات: {user.total_orders}\n"
        f"💎 نقاط الولاء: {user.loyalty_points}\n"
        f"🚫 محظور: {'نعم' if user.is_banned else 'لا'}\n"
        f"👑 أدمن: {'نعم' if user.is_admin else 'لا'}\n"
        f"📅 الانضمام: {user.joined_at.strftime('%Y-%m-%d')}",
        reply_markup=user_manage_kb(user.id, user.is_banned),
    )
    await callback.answer()


@router.message(AdminUserSearchStates.waiting_user_id)
async def user_search_result(message: Message, state: FSMContext, session):
    text = (message.text or "").strip()
    # Remove @ prefix for username search
    search_term = text.lstrip("@")
    
    # Try Telegram ID first
    try:
        tg_id = int(search_term)
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if user:
            await _show_user(message, user)
            await state.clear()
            return
    except ValueError:
        pass

    # Try username
    result = await session.execute(select(User).where(User.username.ilike(search_term)))
    user = result.scalar_one_or_none()
    if user:
        await _show_user(message, user)
        await state.clear()
        return

    # Try partial name match
    like = f"%{search_term}%"
    result = await session.execute(
        select(User).where(User.full_name.ilike(like)).limit(5)
    )
    users = list(result.scalars().all())
    if len(users) == 1:
        await _show_user(message, users[0])
        await state.clear()
        return
    elif len(users) > 1:
        rows = [
            [InlineKeyboardButton(
                text=f"{'🚫' if u.is_banned else '👤'} {u.telegram_id} · {u.full_name or '-'}",
                callback_data=f"admin:user_open:{u.id}",
            )]
            for u in users
        ]
        rows.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:users")])
        await message.answer(
            f"🔍 تم العثور على {len(users)} مستخدمين بالاسم \"{search_term}\":",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await state.clear()
        return

    await message.answer(f"⚠️ لا يوجد مستخدم بهذا الآيدي أو الاسم: {search_term}")


async def _show_user(message: Message, user: User):
    await message.answer(
        "👤 <b>معلومات المستخدم</b>\n\n"
        f"🆔 آيدي: {user.telegram_id}\n"
        f"👤 يوزر: @{user.username or '-'}\n"
        f"📛 الاسم: {user.full_name or '-'}\n"
        f"💰 الرصيد: <b>{user.balance:.2f}$</b>\n"
        f"🛒 إجمالي الشراء: {user.total_spent_usd:.2f}$\n"
        f"📦 عدد الطلبات: {user.total_orders}\n"
        f"🎁 كاشباك: {user.cashback_earned_usd:.4f}$\n"
        f"💎 نقاط الولاء: {user.loyalty_points}\n"
        f"🚫 محظور: {'نعم' if user.is_banned else 'لا'}\n"
        f"👑 أدمن: {'نعم' if user.is_admin else 'لا'}\n"
        f"📅 الانضمام: {user.joined_at.strftime('%Y-%m-%d')}",
        reply_markup=user_manage_kb(user.id, user.is_banned),
    )


# ── إضافة/خصم رصيد ──


@router.callback_query(F.data.startswith("admin:user_add_balance:"))
async def user_add_balance_start(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split(":")[2])
    await state.update_data(target_user_id=user_id, action="add")
    await callback.message.answer("💰 أرسل المبلغ المراد إضافته بالدولار:")
    await state.set_state(AdminUserSearchStates.waiting_balance_amount)


@router.callback_query(F.data.startswith("admin:user_deduct_balance:"))
async def user_deduct_balance_start(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split(":")[2])
    await state.update_data(target_user_id=user_id, action="deduct")
    await callback.message.answer("💰 أرسل المبلغ المراد خصمه بالدولار:")
    await state.set_state(AdminUserSearchStates.waiting_balance_amount)


@router.message(AdminUserSearchStates.waiting_balance_amount)
async def balance_amount_received(
    message: Message,
    state: FSMContext,
    session,
    bot,
):
    data = await state.get_data()
    try:
        amount = Decimal((message.text or "").strip())
        if not amount.is_finite() or amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("⚠️ أرسل رقماً صحيحاً أكبر من صفر.")
        return

    target_user = await session.get(User, data["target_user_id"])
    if not target_user:
        await message.answer("⚠️ المستخدم غير موجود.")
        await state.clear()
        return

    notifier = NotificationService(bot)

    if data["action"] == "add":
        await BalanceService.add_balance(
            session,
            target_user.id,
            amount,
            TransactionType.ADMIN_ADD,
            description="إضافة رصيد يدوية من الأدمن",
        )
        await notifier.notify_user(
            target_user.telegram_id, f"💰 تمت إضافة <b>{amount:.2f}$</b> إلى رصيدك من الإدارة."
        )
        await message.answer(f"✅ تمت إضافة {amount:.2f}$ لرصيد المستخدم.")
    else:
        try:
            await BalanceService.deduct_balance(
                session,
                target_user.id,
                amount,
                TransactionType.ADMIN_DEDUCT,
                description="خصم رصيد يدوي من الأدمن",
            )
        except InsufficientBalanceError:
            await message.answer("⚠️ رصيد المستخدم أقل من المبلغ المطلوب.")
            await state.clear()
            return
        await notifier.notify_user(
            target_user.telegram_id, f"⚠️ تم خصم <b>{amount:.2f}$</b> من رصيدك من قبل الإدارة."
        )
        await message.answer(f"✅ تم خصم {amount:.2f}$ من رصيد المستخدم.")
    await state.clear()


# ── الحظر ──


@router.callback_query(F.data.startswith("admin:user_ban:"))
async def user_ban(callback: CallbackQuery, session, bot):
    user_id = int(callback.data.split(":")[2])
    user = await session.get(User, user_id)
    if not user:
        await callback.answer("⚠️ المستخدم غير موجود.", show_alert=True)
        return
    user.is_banned = True
    await session.commit()
    await NotificationService(bot).notify_user(
        user.telegram_id, "🚫 تم حظرك من استخدام البوت من قبل الإدارة."
    )
    await callback.answer("✅ تم الحظر.")
    await callback.message.edit_reply_markup(reply_markup=user_manage_kb(user.id, True))


@router.callback_query(F.data.startswith("admin:user_unban:"))
async def user_unban(callback: CallbackQuery, session, bot):
    user_id = int(callback.data.split(":")[2])
    user = await session.get(User, user_id)
    if not user:
        await callback.answer("⚠️ المستخدم غير موجود.", show_alert=True)
        return
    user.is_banned = False
    await session.commit()
    await NotificationService(bot).notify_user(
        user.telegram_id, "✅ تم فك حظرك. يمكنك استخدام البوت الآن."
    )
    await callback.answer("✅ تم فك الحظر.")
    await callback.message.edit_reply_markup(reply_markup=user_manage_kb(user.id, False))


# ── سجل معاملات المستخدم (للأدمن) ──


@router.callback_query(F.data.startswith("admin:user_transactions:"))
async def user_transactions(callback: CallbackQuery, session):
    user_id = int(callback.data.split(":")[2])
    user = await session.get(User, user_id)
    if not user:
        await callback.answer("⚠️ المستخدم غير موجود.", show_alert=True)
        return

    transactions = await BalanceService.get_transactions(session, user_id, limit=10)

    if not transactions:
        await callback.message.answer("📊 لا يوجد معاملات لهذا المستخدم.")
        await callback.answer()
        return

    lines = [f"📊 <b>آخر 10 معاملات للمستخدم {user.telegram_id}</b>\n"]
    for tx in transactions:
        sign = "+" if tx.amount > 0 else ""
        lines.append(
            f"\n{tx.type.value}: {sign}{tx.amount:.4f}$\n"
            f"الرصيد بعدها: {tx.balance_after:.2f}$\n"
            f"{tx.created_at.strftime('%Y-%m-%d %H:%M')}"
        )

    await callback.message.answer("\n".join(lines))
    await callback.answer()


# ── إرسال رسالة لمستخدم ──


@router.callback_query(F.data.startswith("admin:user_send_msg:"))
async def user_send_msg_start(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split(":")[2])
    await state.update_data(send_to_user_id=user_id)
    await callback.message.answer("📩 أرسل الرسالة التي تريد إرسالها لهذا المستخدم:")
    await state.set_state(AdminSendMessageStates.waiting_message)
    await callback.answer()


@router.message(AdminSendMessageStates.waiting_message)
async def user_send_msg_received(message: Message, state: FSMContext, session, bot):
    data = await state.get_data()
    user_id = data["send_to_user_id"]
    user = await session.get(User, user_id)
    if not user:
        await message.answer("⚠️ المستخدم غير موجود.")
        await state.clear()
        return

    notifier = NotificationService(bot)
    success = await notifier.notify_user(
        user.telegram_id, f"📩 <b>رسالة من الإدارة:</b>\n\n{message.text}"
    )
    if success:
        await message.answer("✅ تم إرسال الرسالة بنجاح.")
    else:
        await message.answer("⚠️ فشل إرسال الرسالة. ربما المستخدم حظر البوت.")
    await state.clear()


# ── تصدير المستخدمين ──


@router.callback_query(F.data == "admin:users_export")
async def users_export(callback: CallbackQuery, session):
    """تصدير المستخدمين بصيغة CSV."""
    users = list((await session.execute(
        select(User).order_by(User.joined_at.desc()).limit(5000)
    )).scalars().all())
    lines = ["ID,TelegramID,Username,FullName,Balance,TotalSpent,Orders,Banned,JoinedAt"]
    for u in users:
        ban = "1" if u.is_banned else "0"
        lines.append(f"{u.id},{u.telegram_id},{u.username or ''},{u.full_name or ''},{u.balance},{u.total_spent_usd},{u.total_orders},{ban},{u.joined_at}")
    csv_text = "\n".join(lines)
    # Split into chunks of max 4096 chars if too large
    await callback.message.answer(f"📥 <b>تصدير المستخدمين</b>\nالإجمالي: {len(users)}")
    for i in range(0, len(csv_text), 3500):
        chunk = csv_text[i:i + 3500]
        if chunk.strip():
            await callback.message.answer(f"<pre>{chunk}</pre>", parse_mode="HTML")
    await callback.answer()
