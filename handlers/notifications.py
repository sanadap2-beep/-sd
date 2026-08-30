"""User notification inbox and preferences."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from services.notification_center_service import CATEGORIES, CRITICAL_CATEGORIES, NotificationCenterService

router = Router(name="notifications")

_LABELS = {
    "all": "الكل",
    "order": "📦 الطلبات",
    "payment": "💳 الدفع",
    "withdrawal": "💸 السحب",
    "market": "🏪 السوق",
    "support": "🆘 الدعم",
    "promotion": "🔥 العروض",
    "provider": "🔌 المزودين",
    "system": "⚙️ النظام",
    "security": "🛡 الأمان",
}


def _home_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔔 كل الإشعارات", callback_data="notif:list:all")],
        [InlineKeyboardButton(text="📦 الطلبات", callback_data="notif:list:order"), InlineKeyboardButton(text="💳 المالية", callback_data="notif:list:payment")],
        [InlineKeyboardButton(text="🏪 السوق", callback_data="notif:list:market"), InlineKeyboardButton(text="🔥 العروض", callback_data="notif:list:promotion")],
        [InlineKeyboardButton(text="⚙️ إعدادات الإشعارات", callback_data="notif:prefs")],
        [InlineKeyboardButton(text="✅ تعليم الكل كمقروء", callback_data="notif:read_all")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "notif:home")
async def notifications_home(callback: CallbackQuery, session, db_user):
    notifications = await NotificationCenterService.inbox(session, db_user.id, limit=100)
    unread = sum(1 for item in notifications if item.read_at is None)
    await callback.message.edit_text(
        "🔔 <b>مركز الإشعارات</b>\n\n"
        f"لديك <b>{unread}</b> إشعار غير مقروء.\n"
        "اختر نوع الإشعارات أو عدّل التفضيلات.",
        reply_markup=_home_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("notif:list:"))
async def notifications_list(callback: CallbackQuery, session, db_user):
    category = callback.data.rsplit(":", 1)[1]
    notifications = await NotificationCenterService.inbox(session, db_user.id, category=category, limit=20)
    if not notifications:
        await callback.message.edit_text(
            f"🔔 <b>{_LABELS.get(category, category)}</b>\n\nلا توجد إشعارات هنا.",
            reply_markup=_home_kb(),
        )
        await callback.answer()
        return
    lines = [f"🔔 <b>{_LABELS.get(category, category)}</b>", ""]
    for item in notifications:
        mark = "🟢" if item.read_at is None else "⚪"
        lines.append(
            f"{mark} <b>{item.title}</b>\n"
            f"   {item.body[:160]}\n"
            f"   <i>{item.created_at.strftime('%Y-%m-%d %H:%M')}</i>"
        )
    await callback.message.edit_text(
        "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ تعليم كمقروء", callback_data="notif:read_all")],
                [InlineKeyboardButton(text="⬅️ رجوع", callback_data="notif:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "notif:read_all")
async def notifications_read_all(callback: CallbackQuery, session, db_user):
    count = await NotificationCenterService.mark_read(session, db_user.id)
    await callback.answer(f"✅ تم تعليم {count} إشعار كمقروء.")
    await notifications_home(callback, session, db_user)


@router.callback_query(F.data == "notif:prefs")
async def notification_preferences(callback: CallbackQuery, session, db_user):
    prefs = await NotificationCenterService.preferences(session, db_user.id)
    rows = []
    for category in CATEGORIES:
        enabled = prefs.get(category, True)
        locked = category in CRITICAL_CATEGORIES
        rows.append([
            InlineKeyboardButton(
                text=f"{'🔒' if locked else ('🟢' if enabled else '⚪')} {_LABELS.get(category, category)}",
                callback_data=f"notif:pref:{category}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="notif:home")])
    await callback.message.edit_text(
        "⚙️ <b>إعدادات الإشعارات</b>\n\n"
        "يمكنك إيقاف الإشعارات غير الحرجة. المالية والأمنية تبقى مفعلة لحماية حسابك.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("notif:pref:"))
async def notification_preference_toggle(callback: CallbackQuery, session, db_user):
    category = callback.data.rsplit(":", 1)[1]
    if category in CRITICAL_CATEGORIES:
        await callback.answer("هذه الإشعارات لا يمكن تعطيلها لأنها مهمة لحماية حسابك.", show_alert=True)
        return
    prefs = await NotificationCenterService.preferences(session, db_user.id)
    await NotificationCenterService.set_preference(session, db_user.id, category, not prefs.get(category, True))
    await callback.answer("✅ تم تحديث التفضيل.")
    await notification_preferences(callback, session, db_user)
