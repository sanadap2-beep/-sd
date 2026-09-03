"""لوحة «🛍 التحكم بالمتجر»: تحكم كامل بأزرار صفحة المتجر.

- تفعيل/تعطيل أي زر (الأرقام، العروض، الأقسام الذكية، البحث، السلة...).
- إنشاء قسم/زر جديد داخل المتجر (قسم/قسم فرعي/منتج/صفحة/رابط).
- تغيير الترتيب، حذف الأقسام المخصصة، فحص المكسور، استعادة الافتراضي.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from database.models import Category, Product, ProductStatus, SubCategory
from filters.admin_filter import IsAdmin
from keyboards.store_control import (
    store_control_home_kb,
    store_entry_detail_kb,
    store_section_action_help_kb,
    store_section_target_types_kb,
    target_categories_kb,
    target_products_kb,
    target_subcategories_kb,
)
from services.audit_service import AuditAction, AuditService
from services.main_button_service import MainButtonService
from services.store_section_service import StoreEntry, StoreSectionService
from states.states import AdminStoreSectionStates

router = Router(name="admin_store_control")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _home(callback: CallbackQuery):
    entries = await StoreSectionService.list_entries(include_inactive=True)
    active = sum(1 for entry in entries if entry.is_active)
    lines = [
        "🛍 <b>التحكم بالمتجر</b>",
        "",
        f"🟢 مفعّل: <b>{active}</b> من <b>{len(entries)}</b> زر",
        "",
        "اضغط على أي زر لعرض تفاصيله وتفعيله/تعطيله/ترتيبه.",
        "الأقسام الرئيسية وخدمات الأرقام تُدار من لوحاتها الخاصة",
        "(تعطيل قسم من هناك يعطّله في المتجر تلقائياً).",
    ]
    await callback.message.edit_text("\n".join(lines), reply_markup=store_control_home_kb(entries))
    await callback.answer()


@router.callback_query(F.data == "admin:store_control")
async def store_control_home(callback: CallbackQuery):
    await _home(callback)


@router.callback_query(F.data.startswith("stc:view:"))
async def store_entry_view(callback: CallbackQuery):
    key = callback.data.split(":", 2)[2]
    entry = await StoreSectionService.get(key)
    if entry is None:
        await callback.answer("الزر غير موجود.", show_alert=True)
        return
    kind = "ثابت" if entry.is_builtin else "مخصص"
    text = (
        "🛍 <b>تفاصيل زر المتجر</b>\n\n"
        f"النص: <b>{entry.label}</b>\n"
        f"يفتح: <code>{entry.action}</code>\n"
        f"النوع: {kind}\n"
        f"الحالة: {'🟢 مفعّل' if entry.is_active else '⚪ معطّل'}\n"
        f"الترتيب: <b>{entry.sort_order}</b>"
    )
    await callback.message.edit_text(text, reply_markup=store_entry_detail_kb(entry))
    await callback.answer()


@router.callback_query(F.data.startswith("stc:toggle:"))
async def store_entry_toggle(callback: CallbackQuery, session, db_user):
    key = callback.data.split(":", 2)[2]
    entry = await StoreSectionService.toggle(session, key)
    if entry is None:
        await callback.answer("الزر غير موجود.", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="store_section",
        entity_name=entry.label,
        new_value={"key": entry.key, "is_active": entry.is_active},
        description="تفعيل/تعطيل زر في صفحة المتجر",
        session=session,
    )
    state = "مفعّل" if entry.is_active else "معطّل"
    await callback.answer(f"تم: الزر الآن {state}.")
    await _home(callback)


@router.callback_query(F.data.startswith("stc:move:"))
async def store_entry_move(callback: CallbackQuery, session, db_user):
    parts = callback.data.split(":", 3)
    key, direction = parts[2], parts[3]
    moved = await StoreSectionService.move(session, key, -1 if direction == "up" else 1)
    if moved is None:
        await callback.answer("الزر غير موجود.", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="store_section",
        entity_name=moved.label,
        description="تغيير ترتيب زر في المتجر",
        session=session,
    )
    await callback.answer("✅ تم تغيير الترتيب.")
    await _home(callback)


@router.callback_query(F.data.startswith("stc:delete:"))
async def store_entry_delete(callback: CallbackQuery, session, db_user):
    key = callback.data.split(":", 2)[2]
    entry = await StoreSectionService.get(key)
    ok = await StoreSectionService.delete(session, key)
    if not ok:
        await callback.answer("هذا الزر ثابت ولا يمكن حذفه (يمكن تعطيله).", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.DELETE,
        entity_type="store_section",
        entity_name=entry.label if entry else key,
        description="حذف قسم مخصص من المتجر",
        session=session,
    )
    await callback.answer("🗑 تم حذف القسم.")
    await _home(callback)


@router.callback_query(F.data == "stc:reset")
async def store_entry_reset(callback: CallbackQuery, session, db_user):
    await StoreSectionService.reset_defaults(session)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="store_section",
        entity_name="defaults",
        description="استعادة أزرار المتجر الافتراضية",
        session=session,
    )
    await callback.answer("♻️ تمت الاستعادة.")
    await _home(callback)


@router.callback_query(F.data == "stc:check")
async def store_entry_check(callback: CallbackQuery, session):
    entries = await StoreSectionService.list_entries(include_inactive=True)
    broken = []
    for entry in entries:
        ok, reason = await MainButtonService.validate_action(session, entry.action)
        if not ok:
            broken.append(f"• <b>{entry.label}</b>\n  <code>{entry.action}</code> — {reason}")
    if not broken:
        text = "🧪 <b>فحص أزرار المتجر</b>\n\n✅ كل الأزرار سليمة."
    else:
        text = (
            "🧪 <b>فحص أزرار المتجر</b>\n\n"
            f"⚠️ يوجد {len(broken)} زر مكسور:\n\n" + "\n".join(broken[:15])
        )
    await callback.message.edit_text(text, reply_markup=store_control_home_kb(entries))
    await callback.answer()


# ══════════════ إضافة قسم/زر جديد للمتجر ══════════════


@router.callback_query(F.data == "stc:add")
async def store_section_add_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AdminStoreSectionStates.waiting_label)
    await callback.message.answer(
        "➕ <b>إضافة قسم جديد للمتجر</b>\n\n"
        "أرسل نص الزر كما سيظهر للمستخدم.\n"
        "مثال: <code>🎯 صالة الألعاب</code>"
    )
    await callback.answer()


@router.message(AdminStoreSectionStates.waiting_label)
async def store_section_label_received(message: Message, state: FSMContext):
    label = (message.text or "").strip()
    if len(label) < 2 or len(label) > 48:
        await message.answer("⚠️ نص الزر يجب أن يكون بين 2 و48 حرفاً.")
        return
    await state.update_data(label=label)
    await state.set_state(AdminStoreSectionStates.choosing_target_type)
    await message.answer(
        "تمام. الآن اختر ماذا يفتح هذا الزر.",
        reply_markup=store_section_target_types_kb(),
    )


@router.callback_query(AdminStoreSectionStates.choosing_target_type, F.data.startswith("stc:type:"))
async def store_section_target_type(callback: CallbackQuery, state: FSMContext, session):
    target_type = callback.data.rsplit(":", 1)[1]
    if target_type == "category":
        result = await session.execute(select(Category).order_by(Category.sort_order, Category.id))
        categories = list(result.scalars().all())
        await state.set_state(AdminStoreSectionStates.choosing_category)
        await callback.message.edit_text(
            "📂 اختر القسم الرئيسي الذي سيفتحه الزر:",
            reply_markup=target_categories_kb(categories),
        )
    elif target_type == "subcategory":
        result = await session.execute(select(SubCategory).order_by(SubCategory.sort_order, SubCategory.id))
        subcategories = list(result.scalars().all())
        await state.set_state(AdminStoreSectionStates.choosing_subcategory)
        await callback.message.edit_text(
            "📁 اختر القسم الفرعي الذي سيفتحه الزر:",
            reply_markup=target_subcategories_kb(subcategories),
        )
    elif target_type == "product":
        result = await session.execute(
            select(Product).where(Product.status == ProductStatus.ACTIVE).order_by(Product.sort_order, Product.id)
        )
        products = list(result.scalars().all())
        await state.set_state(AdminStoreSectionStates.choosing_product)
        await callback.message.edit_text(
            "📦 اختر المنتج الذي سيفتحه الزر:",
            reply_markup=target_products_kb(products),
        )
    elif target_type == "internal":
        await state.set_state(AdminStoreSectionStates.waiting_action)
        await callback.message.edit_text(
            "⚡ اختر صفحة داخلية جاهزة:",
            reply_markup=store_section_action_help_kb(),
        )
    elif target_type == "url":
        await state.set_state(AdminStoreSectionStates.waiting_action)
        await callback.message.answer("🌐 أرسل الرابط الخارجي ويجب أن يبدأ بـ https://")
    else:
        await state.set_state(AdminStoreSectionStates.waiting_action)
        await callback.message.answer(
            "✍️ أرسل الإجراء يدوياً مثل <code>cat:12</code> أو <code>prod:77</code> "
            "أو <code>store:section:games</code>."
        )
    await callback.answer()


@router.callback_query(AdminStoreSectionStates.choosing_category, F.data.startswith("stc:pick:cat:"))
async def store_section_pick_category(callback: CallbackQuery, state: FSMContext, session, db_user):
    cat_id = int(callback.data.rsplit(":", 1)[1])
    category = await session.get(Category, cat_id)
    if category is None:
        await callback.answer("القسم غير موجود.", show_alert=True)
        return
    await _save_new_section(callback.message, state, session, db_user, f"cat:{cat_id}")
    await callback.answer("✅ تم إضافة القسم للمتجر.")


@router.callback_query(AdminStoreSectionStates.choosing_subcategory, F.data.startswith("stc:pick:subcat:"))
async def store_section_pick_subcategory(callback: CallbackQuery, state: FSMContext, session, db_user):
    sub_id = int(callback.data.rsplit(":", 1)[1])
    sub = await session.get(SubCategory, sub_id)
    if sub is None:
        await callback.answer("القسم الفرعي غير موجود.", show_alert=True)
        return
    await _save_new_section(callback.message, state, session, db_user, f"subcat:{sub_id}")
    await callback.answer("✅ تم إضافة القسم الفرعي للمتجر.")


@router.callback_query(AdminStoreSectionStates.choosing_product, F.data.startswith("stc:pick:prod:"))
async def store_section_pick_product(callback: CallbackQuery, state: FSMContext, session, db_user):
    prod_id = int(callback.data.rsplit(":", 1)[1])
    product = await session.get(Product, prod_id)
    if product is None:
        await callback.answer("المنتج غير موجود.", show_alert=True)
        return
    await _save_new_section(callback.message, state, session, db_user, f"prod:{prod_id}")
    await callback.answer("✅ تم إضافة المنتج للمتجر.")


@router.callback_query(AdminStoreSectionStates.waiting_action, F.data.startswith("stc:action:"))
async def store_section_action_picked(callback: CallbackQuery, state: FSMContext, session, db_user):
    action = callback.data.removeprefix("stc:action:")
    await _save_new_section(callback.message, state, session, db_user, action)
    await callback.answer("✅ تم إضافة الزر للمتجر.")


@router.message(AdminStoreSectionStates.waiting_action)
async def store_section_action_received(message: Message, state: FSMContext, session, db_user):
    action = (message.text or "").strip()
    ok, _reason = await MainButtonService.validate_action(session, action)
    if not ok:
        await message.answer(
            "⚠️ الإجراء غير صالح. استخدم cat:ID أو subcat:ID أو prod:ID "
            "أو store:section:name أو رابط https://"
        )
        return
    await _save_new_section(message, state, session, db_user, action)


async def _save_new_section(target, state: FSMContext, session, db_user, action: str):
    data = await state.get_data()
    label = data.get("label", "قسم جديد")
    entry: StoreEntry = await StoreSectionService.add_custom(session, label, action)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.CREATE,
        entity_type="store_section",
        entity_name=entry.label,
        new_value={"label": entry.label, "action": entry.action},
        description="إنشاء قسم جديد في صفحة المتجر",
        session=session,
    )
    await state.clear()
    await target.answer(
        f"✅ تم إنشاء القسم في المتجر: <b>{entry.label}</b>\nيفتح: <code>{entry.action}</code>"
    )
