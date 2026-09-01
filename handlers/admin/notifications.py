"""Admin notification logs and editable templates."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from filters.admin_filter import IsAdmin
from services.notification_center_service import NotificationCenterService
from states.states import AdminNotificationStates

router = Router(name="admin_notifications")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _admin_notif_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 سجل الإرسال", callback_data="admin:notif_log")],
        [InlineKeyboardButton(text="📝 قوالب الإشعارات", callback_data="admin:notif_templates")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")],
    ])


@router.callback_query(F.data == "admin:notifications")
async def admin_notifications_home(callback: CallbackQuery, session):
    stats = await NotificationCenterService.admin_stats(session)
    by_status = stats["by_status"]
    await callback.message.edit_text(
        "🔔 <b>إدارة الإشعارات</b>\n\n"
        f"✅ مرسلة: {by_status.get('sent', 0)}\n"
        f"⚪ متخطاة بتفضيلات المستخدم: {by_status.get('skipped', 0)}\n"
        f"❌ فاشلة: {by_status.get('failed', 0)}\n\n"
        "هنا تراقب الإرسال وتعدل قوالب الرسائل المهمة.",
        reply_markup=_admin_notif_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:notif_log")
async def admin_notification_log(callback: CallbackQuery, session):
    stats = await NotificationCenterService.admin_stats(session)
    failed = stats["failed"]
    if not failed:
        text = "📊 <b>سجل الإشعارات</b>\n\n✅ لا توجد إشعارات فاشلة حديثة."
    else:
        lines = ["📊 <b>آخر الإشعارات الفاشلة</b>", ""]
        for item in failed:
            lines.append(
                f"• #{item.id} {item.channel}/{item.category}\n"
                f"  {item.title}\n"
                f"  الخطأ: <code>{item.error or '—'}</code>"
            )
        text = "\n".join(lines)
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:notifications")]]),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:notif_templates")
async def admin_notification_templates(callback: CallbackQuery, session):
    templates = await NotificationCenterService.templates(session)
    rows = [
        [InlineKeyboardButton(text=f"{tpl.key} · {tpl.category}", callback_data=f"admin:notif_tpl:{tpl.key}")]
        for tpl in templates
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:notifications")])
    await callback.message.edit_text(
        "📝 <b>قوالب الإشعارات</b>\n\nاختر قالباً لتعديل العنوان والنص:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:notif_tpl:"))
async def admin_notification_template_view(callback: CallbackQuery, session, state: FSMContext):
    key = callback.data.rsplit(":", 1)[1]
    template = await session.get(__import__('database.models', fromlist=['NotificationTemplate']).NotificationTemplate, key)
    if template is None:
        await callback.answer("القالب غير موجود.", show_alert=True)
        return
    await state.update_data(template_key=key)
    await callback.message.edit_text(
        f"📝 <b>{template.key}</b>\n\n"
        f"العنوان الحالي:\n<code>{template.title}</code>\n\n"
        f"النص الحالي:\n<code>{template.body}</code>\n\n"
        "أرسل العنوان الجديد أو - لإبقائه كما هو.",
    )
    await state.set_state(AdminNotificationStates.waiting_template_title)
    await callback.answer()


@router.message(AdminNotificationStates.waiting_template_title)
async def admin_notification_template_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    await state.update_data(template_title=None if title == "-" else title)
    await state.set_state(AdminNotificationStates.waiting_template_body)
    await message.answer("أرسل نص القالب الجديد أو - لإبقائه كما هو. يمكنك استخدام متغيرات مثل {amount} و {product}.")


@router.message(AdminNotificationStates.waiting_template_body)
async def admin_notification_template_body(message: Message, state: FSMContext, session):
    from database.models import NotificationTemplate

    data = await state.get_data()
    key = data.get("template_key")
    template = await session.get(NotificationTemplate, key)
    if template is None:
        await state.clear()
        await message.answer("القالب غير موجود.")
        return
    title = data.get("template_title") or template.title
    body_text = (message.text or "").strip()
    body = template.body if body_text == "-" else body_text
    await NotificationCenterService.update_template(session, key, title, body)
    await state.clear()
    await message.answer("✅ تم تحديث قالب الإشعار.")
