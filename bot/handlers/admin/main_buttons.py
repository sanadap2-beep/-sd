"""Admin panel for dynamic main-menu buttons.

This is the piece that makes the storefront truly dynamic: main buttons are not
hardcoded in the keyboard; the admin creates shortcuts that point to categories,
products, internal pages, or URLs.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import Category, Product, ProductStatus, SubCategory
from filters.admin_filter import IsAdmin
from keyboards.admin_main_buttons import (
    main_button_action_help_kb,
    main_button_detail_kb,
    main_button_presets_kb,
    main_button_target_types_kb,
    main_buttons_kb,
    target_categories_kb,
    target_products_kb,
    target_subcategories_kb,
)
from services.audit_service import AuditAction, AuditService
from services.main_button_service import MainButtonService
from states.states import AdminMainButtonStates

router = Router(name="admin_main_buttons")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:main_buttons")
async def main_buttons_home(callback: CallbackQuery):
    buttons = await MainButtonService.list_buttons(include_inactive=True)
    if buttons:
        lines = ["🎛 <b>أزرار الواجهة الرئيسية</b>", ""]
        for button in buttons:
            mark = "🟢" if button.is_active else "⚪"
            kind = "رابط" if button.is_url else "أمر داخلي"
            lines.append(
                f"{mark} <b>{button.label}</b>\n"
                f"   {kind}: <code>{button.action}</code>"
            )
    else:
        lines = [
            "🎛 <b>أزرار الواجهة الرئيسية</b>",
            "",
            "لا توجد أزرار ديناميكية حالياً.",
        ]
    lines.append(
        "\nأي زر تضيفه هنا يظهر في القائمة الرئيسية بدون تعديل الكود."
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=main_buttons_kb(buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("mb:view:"))
async def main_button_view(callback: CallbackQuery):
    button_id = callback.data.rsplit(":", 1)[1]
    buttons = await MainButtonService.list_buttons(include_inactive=True)
    button = next((item for item in buttons if item.id == button_id), None)
    if button is None:
        await callback.answer("الزر غير موجود.", show_alert=True)
        return
    text = (
        "🎛 <b>تفاصيل زر رئيسي</b>\n\n"
        f"النص: <b>{button.label}</b>\n"
        f"الإجراء: <code>{button.action}</code>\n"
        f"الحالة: {'🟢 مفعّل' if button.is_active else '⚪ معطّل'}\n"
        f"الترتيب: <b>{button.sort_order}</b>"
    )
    await callback.message.edit_text(text, reply_markup=main_button_detail_kb(button.id, button.is_active))
    await callback.answer()


@router.callback_query(F.data == "mb:add")
async def main_button_add_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(AdminMainButtonStates.waiting_label)
    await callback.message.answer(
        "➕ <b>إضافة زر رئيسي</b>\n\n"
        "أرسل نص الزر كما سيظهر للمستخدم.\n"
        "مثال: <code>🔥 عروض اليوم</code>"
    )
    await callback.answer()


@router.message(AdminMainButtonStates.waiting_label)
async def main_button_label_received(message: Message, state: FSMContext):
    label = (message.text or "").strip()
    if len(label) < 2 or len(label) > 48:
        await message.answer("⚠️ نص الزر يجب أن يكون بين 2 و48 حرفاً.")
        return
    await state.update_data(label=label)
    await state.set_state(AdminMainButtonStates.choosing_target_type)
    await message.answer(
        "تمام. الآن اختر ماذا يفتح هذا الزر.\n\n"
        "تقدر تختار قسم/قسم فرعي/منتج بدون كتابة أي ID، أو تختار صفحة داخلية جاهزة.",
        reply_markup=main_button_target_types_kb(),
    )


@router.callback_query(AdminMainButtonStates.choosing_target_type, F.data.startswith("mb:type:"))
async def main_button_target_type(callback: CallbackQuery, state: FSMContext, session):
    target_type = callback.data.rsplit(":", 1)[1]
    if target_type == "category":
        result = await session.execute(select(Category).order_by(Category.sort_order, Category.id))
        categories = list(result.scalars().all())
        await state.set_state(AdminMainButtonStates.choosing_category)
        await callback.message.edit_text(
            "📂 اختر القسم الرئيسي الذي سيفتحه الزر:",
            reply_markup=target_categories_kb(categories),
        )
    elif target_type == "subcategory":
        result = await session.execute(select(SubCategory).order_by(SubCategory.sort_order, SubCategory.id))
        subcategories = list(result.scalars().all())
        await state.set_state(AdminMainButtonStates.choosing_subcategory)
        await callback.message.edit_text(
            "📁 اختر القسم الفرعي الذي سيفتحه الزر:",
            reply_markup=target_subcategories_kb(subcategories),
        )
    elif target_type == "product":
        result = await session.execute(
            select(Product).where(Product.status == ProductStatus.ACTIVE).order_by(Product.sort_order, Product.id)
        )
        products = list(result.scalars().all())
        await state.set_state(AdminMainButtonStates.choosing_product)
        await callback.message.edit_text(
            "📦 اختر المنتج الذي سيفتحه الزر:",
            reply_markup=target_products_kb(products),
        )
    elif target_type == "internal":
        await state.set_state(AdminMainButtonStates.waiting_action)
        await callback.message.edit_text(
            "⚡ اختر صفحة داخلية جاهزة:",
            reply_markup=main_button_action_help_kb(),
        )
    elif target_type == "url":
        await state.set_state(AdminMainButtonStates.waiting_action)
        await callback.message.answer("🌐 أرسل الرابط الخارجي ويجب أن يبدأ بـ https://")
    else:
        await state.set_state(AdminMainButtonStates.waiting_action)
        await callback.message.answer(
            "✍️ أرسل الإجراء يدوياً مثل <code>cat:12</code> أو <code>prod:77</code> أو <code>store:home</code>."
        )
    await callback.answer()


@router.callback_query(
    AdminMainButtonStates.choosing_category,
    F.data.startswith("mb:pick:cat:"),
)
async def main_button_pick_category(callback: CallbackQuery, state: FSMContext, session, db_user):
    cat_id = int(callback.data.rsplit(":", 1)[1])
    await _save_new_button(callback.message, state, session, db_user, f"cat:{cat_id}")
    await callback.answer("✅ تم حفظ الزر.")


@router.callback_query(
    AdminMainButtonStates.choosing_subcategory,
    F.data.startswith("mb:pick:subcat:"),
)
async def main_button_pick_subcategory(callback: CallbackQuery, state: FSMContext, session, db_user):
    sub_id = int(callback.data.rsplit(":", 1)[1])
    await _save_new_button(callback.message, state, session, db_user, f"subcat:{sub_id}")
    await callback.answer("✅ تم حفظ الزر.")


@router.callback_query(
    AdminMainButtonStates.choosing_product,
    F.data.startswith("mb:pick:prod:"),
)
async def main_button_pick_product(callback: CallbackQuery, state: FSMContext, session, db_user):
    product_id = int(callback.data.rsplit(":", 1)[1])
    await _save_new_button(callback.message, state, session, db_user, f"prod:{product_id}")
    await callback.answer("✅ تم حفظ الزر.")


@router.callback_query(AdminMainButtonStates.waiting_action, F.data.startswith("mb:action:"))
async def main_button_action_picked(callback: CallbackQuery, state: FSMContext, session, db_user):
    action = callback.data.removeprefix("mb:action:")
    await _save_new_button(callback.message, state, session, db_user, action)
    await callback.answer("✅ تم حفظ الزر.")


@router.message(AdminMainButtonStates.waiting_action)
async def main_button_action_received(message: Message, state: FSMContext, session, db_user):
    action = (message.text or "").strip()
    if not _valid_action(action):
        await message.answer(
            "⚠️ الإجراء غير صالح. استخدم cat:ID أو subcat:ID أو prod:ID أو store:section:name أو رابط https://"
        )
        return
    await _save_new_button(message, state, session, db_user, action)


def _valid_action(action: str) -> bool:
    if action.startswith("https://") or action.startswith("http://"):
        return True
    if action in {"store:home", "menu:cart", "menu:search", "menu:product_request", "num_packages"}:
        return True
    prefixes = ("store:section:", "cat:", "subcat:", "prod:", "menu:", "market:", "tasks:", "points:", "extras:")
    return any(action.startswith(prefix) for prefix in prefixes)


async def _save_new_button(target, state: FSMContext, session, db_user, action: str):
    data = await state.get_data()
    label = data.get("label", "زر")
    button = await MainButtonService.add(session, label, action)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.CREATE,
        entity_type="main_menu_button",
        entity_id=None,
        entity_name=button.label,
        new_value={"label": button.label, "action": button.action},
        description="إنشاء زر رئيسي ديناميكي",
        session=session,
    )
    await state.clear()
    await target.answer(
        f"✅ تم إنشاء الزر: <b>{button.label}</b>\nالإجراء: <code>{button.action}</code>"
    )


@router.callback_query(F.data.startswith("mb:add_cat:"))
async def main_button_add_category(callback: CallbackQuery, session, db_user):
    category_id = int(callback.data.rsplit(":", 1)[1])
    category = await session.get(Category, category_id)
    if category is None:
        await callback.answer("القسم غير موجود.", show_alert=True)
        return
    button = await MainButtonService.add(session, f"{category.emoji} {category.name_ar}", f"cat:{category.id}")
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.CREATE,
        entity_type="main_menu_button",
        entity_name=button.label,
        new_value={"label": button.label, "action": button.action},
        description="إضافة قسم كزر رئيسي",
        session=session,
    )
    await callback.answer("✅ أُضيف القسم كزر رئيسي.", show_alert=True)


@router.callback_query(F.data.startswith("mb:add_subcat:"))
async def main_button_add_subcategory(callback: CallbackQuery, session, db_user):
    subcategory_id = int(callback.data.rsplit(":", 1)[1])
    subcategory = await session.get(SubCategory, subcategory_id)
    if subcategory is None:
        await callback.answer("القسم الفرعي غير موجود.", show_alert=True)
        return
    button = await MainButtonService.add(
        session, f"{subcategory.emoji} {subcategory.name_ar}", f"subcat:{subcategory.id}"
    )
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.CREATE,
        entity_type="main_menu_button",
        entity_name=button.label,
        new_value={"label": button.label, "action": button.action},
        description="إضافة قسم فرعي كزر رئيسي",
        session=session,
    )
    await callback.answer("✅ أُضيف القسم الفرعي كزر رئيسي.", show_alert=True)


@router.callback_query(F.data.startswith("mb:add_prod:"))
async def main_button_add_product(callback: CallbackQuery, session, db_user):
    product_id = int(callback.data.rsplit(":", 1)[1])
    product = await session.get(Product, product_id)
    if product is None:
        await callback.answer("المنتج غير موجود.", show_alert=True)
        return
    button = await MainButtonService.add(session, f"📦 {product.name_ar}", f"prod:{product.id}")
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.CREATE,
        entity_type="main_menu_button",
        entity_name=button.label,
        new_value={"label": button.label, "action": button.action},
        description="إضافة منتج كزر رئيسي",
        session=session,
    )
    await callback.answer("✅ أُضيف المنتج كزر رئيسي.", show_alert=True)


@router.callback_query(F.data == "mb:presets")
async def main_button_presets(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚡ <b>أزرار جاهزة</b>\n\nاختر زر جاهز لإضافته للقائمة الرئيسية:",
        reply_markup=main_button_presets_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mb:preset:"))
async def main_button_preset_add(callback: CallbackQuery, session, db_user):
    preset = callback.data.rsplit(":", 1)[1]
    button = await MainButtonService.add_preset(session, preset)
    if button is None:
        await callback.answer("زر جاهز غير معروف.", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.CREATE,
        entity_type="main_menu_button",
        entity_name=button.label,
        new_value={"label": button.label, "action": button.action},
        description="إضافة زر رئيسي جاهز",
        session=session,
    )
    await callback.answer("✅ تمت الإضافة.")
    await main_buttons_home(callback)


@router.callback_query(F.data.startswith("mb:toggle:"))
async def main_button_toggle(callback: CallbackQuery, session, db_user):
    button_id = callback.data.rsplit(":", 1)[1]
    button = await MainButtonService.toggle(session, button_id)
    if button is None:
        await callback.answer("الزر غير موجود.", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="main_menu_button",
        entity_name=button.label,
        new_value={"is_active": button.is_active},
        description="تفعيل/تعطيل زر رئيسي",
        session=session,
    )
    await callback.answer("تم التحديث.")
    await main_buttons_home(callback)


@router.callback_query(F.data.startswith("mb:delete:"))
async def main_button_delete(callback: CallbackQuery, session, db_user):
    button_id = callback.data.rsplit(":", 1)[1]
    buttons = await MainButtonService.list_buttons(include_inactive=True)
    button = next((item for item in buttons if item.id == button_id), None)
    ok = await MainButtonService.delete(session, button_id)
    if not ok:
        await callback.answer("الزر غير موجود.", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.DELETE,
        entity_type="main_menu_button",
        entity_name=button.label if button else button_id,
        description="حذف زر رئيسي ديناميكي",
        session=session,
    )
    await callback.answer("🗑 تم حذف الزر.")
    await main_buttons_home(callback)


@router.callback_query(F.data.startswith("mb:move:"))
async def main_button_move(callback: CallbackQuery, session, db_user):
    _, _, button_id, direction = callback.data.split(":", 3)
    moved = await MainButtonService.move(session, button_id, -1 if direction == "up" else 1)
    if moved is None:
        await callback.answer("الزر غير موجود.", show_alert=True)
        return
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="main_menu_button",
        entity_name=moved.label,
        description="تغيير ترتيب زر رئيسي",
        session=session,
    )
    await callback.answer("✅ تم تغيير الترتيب.")
    await main_buttons_home(callback)


@router.callback_query(F.data == "mb:check")
async def main_button_check(callback: CallbackQuery, session):
    broken = await MainButtonService.broken_report(session)
    if not broken:
        text = "🧪 <b>فحص الأزرار</b>\n\n✅ كل الأزرار سليمة ولا توجد روابط مكسورة."
    else:
        lines = ["🧪 <b>فحص الأزرار</b>", "", f"⚠️ يوجد {len(broken)} زر يحتاج مراجعة:"]
        for item in broken[:20]:
            state = "مفعّل" if item["is_active"] else "معطّل"
            lines.append(
                f"• <b>{item['label']}</b> ({state})\n"
                f"  <code>{item['action']}</code> — {item['reason']}"
            )
        text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=main_buttons_kb(await MainButtonService.list_buttons()))
    await callback.answer()


@router.callback_query(F.data == "mb:reset")
async def main_button_reset(callback: CallbackQuery, session, db_user):
    await MainButtonService.reset_defaults(session)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="main_menu_button",
        entity_name="defaults",
        description="استعادة أزرار الواجهة الافتراضية",
        session=session,
    )
    await callback.answer("♻️ تمت الاستعادة.")
    await main_buttons_home(callback)
