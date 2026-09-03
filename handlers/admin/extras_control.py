"""لوحة «🧩 التحكم بخدمات الأخرى»: تفعيل/تعطيل كل عنصر في صفحة الخدمات الأخرى."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from filters.admin_filter import IsAdmin
from keyboards.store_control import extras_control_kb
from services.audit_service import AuditAction, AuditService
from services.extras_section_service import ExtrasSectionService
from services.feature_service import FeatureService

router = Router(name="admin_extras_control")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:extras_control")
async def extras_control_home(callback: CallbackQuery):
    entries = await ExtrasSectionService.list_entries()
    active = sum(1 for entry in entries if entry.is_active)
    lines = [
        "🧩 <b>التحكم بخدمات الأخرى</b>",
        "",
        f"🟢 مفعّل: <b>{active}</b> من <b>{len(entries)}</b> عنصر",
        "",
        "اضغط على أي عنصر لتفعيله/تعطيله في صفحة «🧩 خدمات البوت الأخرى».",
        "الميزات المختومة بـ (الميزة موقوفة) مرتبطة بـ «مركز الإضافات» أيضاً.",
    ]
    await callback.message.edit_text("\n".join(lines), reply_markup=extras_control_kb(entries))
    await callback.answer()


@router.callback_query(F.data.startswith("xtc:toggle:"))
async def extras_entry_toggle(callback: CallbackQuery, session, db_user):
    key = callback.data.split(":", 2)[2]
    entry = await ExtrasSectionService.toggle(session, key)
    if entry is None:
        await callback.answer("العنصر غير موجود.", show_alert=True)
        return
    feature_off = entry.feature_key and not await FeatureService.enabled(entry.feature_key)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="extras_section",
        entity_name=entry.label,
        new_value={"key": entry.key, "is_active": entry.is_active},
        description="تفعيل/تعطيل عنصر في خدمات الأخرى",
        session=session,
    )
    if feature_off:
        await callback.answer("حُفظ، لكن الميزة المرتبطة موقوفة من مركز الإضافات.", show_alert=True)
    else:
        state = "مفعّل" if entry.is_active else "معطّل"
        await callback.answer(f"تم: العنصر الآن {state}.")
    await extras_control_home(callback)
