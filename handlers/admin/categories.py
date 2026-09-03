"""
إدارة الأقسام الرئيسية والفرعية.

يشمل:
- عرض قائمة الأقسام
- Wizard إضافة قسم رئيسي (نوع → اسم → إيموجي)
- Wizard إضافة قسم فرعي (اسم → إيموجي → صورة)
- تفعيل/تعطيل
- تعديل الحقول (اسم، إيموجي، وصف، صورة، ترتيب)
- حذف مع تأكيد
- تسجيل Audit Log
"""

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import (
    Category,
    SubCategory,
    CategoryType,
)
from services.audit_service import AuditService
from services.dynamic_service import DynamicService
from states.states import (
    AdminCategoryStates,
    AdminSubCategoryStates,
)
from keyboards.admin_categories_v2 import (
    categories_list_kb,
    select_category_type_kb,
    select_emoji_kb,
    category_detail_kb,
    confirm_delete_category_kb,
    sub_categories_list_kb,
    sub_category_detail_kb,
    confirm_delete_sub_category_kb,
    image_options_kb,
    cancel_add_kb,
)
from keyboards.admin import admin_back_kb
from filters.admin_filter import IsAdmin

logger = logging.getLogger(__name__)

router = Router(name="admin_categories")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════════════════════════════════════
# ══════════════ قائمة الأقسام ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data == "admin:categories")
async def categories_list(callback: CallbackQuery, session):
    """يعرض قائمة كل الأقسام الرئيسية."""
    categories = await DynamicService.get_all_categories(session)

    if not categories:
        text = (
            "📂 <b>إدارة الأقسام</b>\n\n"
            "لا توجد أقسام مسجلة حتى الآن.\n"
            "اضغط الزر أدناه لإضافة قسم جديد."
        )
    else:
        text = (
            "📂 <b>إدارة الأقسام</b>\n\n"
            f"عدد الأقسام: <b>{len(categories)}</b>\n\n"
            "🟢 = مفعّل | 🔴 = معطّل\n"
            "الرقم بين قوسين = عدد الأقسام الفرعية\n\n"
            "اضغط على أي قسم لعرض التفاصيل."
        )

    await callback.message.edit_text(
        text,
        reply_markup=categories_list_kb(categories),
    )


# ══════════════════════════════════════════════
# ══════════════ إضافة قسم رئيسي - الخطوة 1 ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data == "admin:cat_add")
async def cat_add_start(callback: CallbackQuery, state: FSMContext):
    """يبدأ Wizard إضافة قسم رئيسي."""
    await state.clear()
    await callback.message.edit_text(
        "📂 <b>إضافة قسم رئيسي جديد</b>\n\n"
        "الخطوة 1️⃣ من 3️⃣\n\n"
        "اختر نوع القسم:\n\n"
        "📈 <b>رشق سوشيال</b>: منتجات رشق للمنصات "
        "(متابعين، لايكات، مشاهدات)\n\n"
        "🎮 <b>شحن ألعاب</b>: منتجات شحن الألعاب "
        "(UC ببجي، جواهر فري فاير، إلخ)\n\n"
        "📱 <b>تطبيقات دردشة</b>: منتجات تطبيقات مثل "
        "بيجو لايف، لايكي، تيك توك",
        reply_markup=select_category_type_kb(),
    )
    await state.set_state(AdminCategoryStates.waiting_type)


@router.callback_query(F.data.startswith("admin:cat_type:"))
async def cat_type_selected(callback: CallbackQuery, state: FSMContext):
    """اختيار نوع القسم."""
    type_key = callback.data.split(":")[2]

    try:
        cat_type = CategoryType(type_key)
    except ValueError:
        await callback.answer("❌ نوع غير صالح", show_alert=True)
        return

    await state.update_data(category_type=cat_type.value)
    await callback.answer()

    type_names = {
        "smm": "📈 قسم الرشق",
        "games": "🎮 قسم شحن الألعاب",
        "apps": "📱 قسم شحن التطبيقات",
        "balances": "💳 قسم الأرصدة",
        "cards": "💳 قسم البطاقات والفيز",
        "subscriptions": "🔐 قسم الاشتراكات الرقمية",
        "verification": "✅ قسم توثيق الحسابات",
        "codes": "🎟 قسم الأكواد الرقمية",
        "custom": "🧩 قسم مخصص",
    }
    type_display = type_names.get(cat_type.value, cat_type.value)

    await callback.message.edit_text(
        f"✅ النوع: <b>{type_display}</b>\n\n"
        "الخطوة 2️⃣ من 3️⃣\n\n"
        "📝 أرسل اسم القسم بالعربية:\n"
        "(مثال: <code>رشق سوشيال ميديا</code> أو "
        "<code>شحن ألعاب</code>)",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminCategoryStates.waiting_name)


@router.message(AdminCategoryStates.waiting_name)
async def cat_name_received(message: Message, state: FSMContext):
    """استقبال اسم القسم."""
    name = message.text.strip()

    if len(name) < 2:
        await message.answer("⚠️ الاسم قصير جداً.")
        return

    if len(name) > 64:
        await message.answer("⚠️ الاسم طويل جداً (الحد الأقصى 64 حرف).")
        return

    await state.update_data(name=name)

    data = await state.get_data()
    cat_type = data.get("category_type", "smm")

    await message.answer(
        f"✅ الاسم: <b>{name}</b>\n\nالخطوة 3️⃣ من 3️⃣\n\n🎨 اختر إيموجي للقسم:",
        reply_markup=select_emoji_kb(cat_type),
    )
    await state.set_state(AdminCategoryStates.waiting_emoji)


@router.callback_query(
    AdminCategoryStates.waiting_emoji,
    F.data.startswith("admin:cat_emoji:"),
)
async def cat_emoji_selected(
    callback: CallbackQuery,
    state: FSMContext,
    session,
    db_user,
):
    """استقبال الإيموجي وإنشاء القسم."""
    emoji_choice = callback.data.split(":", 2)[2]
    data = await state.get_data()

    if emoji_choice == "custom":
        await callback.message.edit_text("✏️ أرسل إيموجي مخصص:\n(إيموجي واحد فقط)")
        return

    if emoji_choice == "default":
        emoji_defaults = {
            "smm": "📈",
            "games": "🎮",
            "apps": "📱",
            "balances": "💳",
            "cards": "💳",
            "subscriptions": "🔐",
            "verification": "✅",
            "codes": "🎟",
            "custom": "🧩",
        }
        emoji = emoji_defaults.get(data.get("category_type", "custom"), "📦")
    else:
        emoji = emoji_choice

    try:
        category = await DynamicService.create_category(
            session=session,
            name_ar=data["name"],
            emoji=emoji,
            category_type=CategoryType(data["category_type"]),
        )
    except Exception as e:
        logger.error(f"فشل إنشاء قسم: {e}")
        await callback.answer(f"❌ فشل الإنشاء: {e}", show_alert=True)
        await state.clear()
        return

    await AuditService.log_create(
        admin_id=db_user.id,
        entity_type="category",
        entity_id=category.id,
        entity_name=category.name_ar,
        new_value={
            "name": category.name_ar,
            "emoji": category.emoji,
            "type": category.type.value,
        },
        session=session,
    )

    await callback.answer("✅ تم إنشاء القسم بنجاح")
    await callback.message.edit_text(
        f"✅ <b>تم إنشاء القسم بنجاح!</b>\n\n"
        f"{emoji} <b>{category.name_ar}</b>\n\n"
        "يمكنك الآن إضافة أقسام فرعية له."
    )
    await state.clear()

    await categories_list(callback, session)


@router.message(AdminCategoryStates.waiting_emoji)
async def cat_custom_emoji_received(
    message: Message,
    state: FSMContext,
    session,
    db_user,
):
    """استقبال إيموجي مخصص."""
    emoji = message.text.strip()

    if len(emoji) > 8:
        await message.answer("⚠️ إيموجي واحد فقط من فضلك.")
        return

    data = await state.get_data()

    try:
        category = await DynamicService.create_category(
            session=session,
            name_ar=data["name"],
            emoji=emoji,
            category_type=CategoryType(data["category_type"]),
        )
    except Exception as e:
        logger.error(f"فشل إنشاء قسم: {e}")
        await message.answer(f"❌ فشل الإنشاء: {e}")
        await state.clear()
        return

    await AuditService.log_create(
        admin_id=db_user.id,
        entity_type="category",
        entity_id=category.id,
        entity_name=category.name_ar,
        new_value={
            "name": category.name_ar,
            "emoji": category.emoji,
            "type": category.type.value,
        },
        session=session,
    )

    await message.answer(f"✅ <b>تم إنشاء القسم بنجاح!</b>\n\n{emoji} <b>{category.name_ar}</b>")
    await state.clear()


# ══════════════════════════════════════════════
# ══════════════ تفاصيل القسم ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:cat_view:"))
async def cat_view(callback: CallbackQuery, session):
    """يعرض تفاصيل قسم رئيسي."""
    cat_id = int(callback.data.split(":")[2])
    category = await DynamicService.get_category(session, cat_id)

    if not category:
        await callback.answer("⚠️ القسم غير موجود", show_alert=True)
        return

    await _show_category_details(callback.message, category, edit=True)


async def _show_category_details(
    message,
    category: Category,
    edit: bool = True,
):
    """يعرض تفاصيل قسم."""
    status = "🟢 مفعّل" if category.is_active else "🔴 معطّل"
    type_labels = {
        "smm": "📈 الرشق",
        "games": "🎮 شحن الألعاب",
        "apps": "📱 شحن التطبيقات",
        "balances": "💳 الأرصدة",
        "cards": "💳 البطاقات والفيز",
        "subscriptions": "🔐 الاشتراكات الرقمية",
        "verification": "✅ توثيق الحسابات",
        "codes": "🎟 الأكواد الرقمية",
        "custom": "🧩 مخصص",
        "numbers": "📞 أرقام",
    }
    type_label = type_labels.get(category.type.value, category.type.value)

    subs_count = len(category.sub_categories) if category.sub_categories else 0

    active_subs = 0
    if category.sub_categories:
        active_subs = sum(1 for s in category.sub_categories if s.is_active)

    margin_text = (
        f"{category.profit_margin_percent}% (خاص بالقسم)"
        if category.profit_margin_percent is not None
        else "غير مضبوط — المنتجات ترث الهامش العالمي"
    )
    text = (
        f"{category.emoji} <b>{category.name_ar}</b>\n"
        f"🆔 ID: <code>{category.id}</code> | ربط زر: <code>cat:{category.id}</code>\n\n"
        f"📁 النوع: {type_label}\n"
        f"📊 الحالة: {status}\n"
        f"🔢 الترتيب: {category.sort_order}\n"
        f"💵 هامش الربح: <b>{margin_text}</b>\n\n"
        f"📂 عدد الأقسام الفرعية: <b>{subs_count}</b>\n"
        f"🟢 نشطة منها: <b>{active_subs}</b>\n"
    )
    if category.description:
        text += f"\n📝 الشرح: <i>{category.description}</i>\n"
    text += f"\n📅 تاريخ الإنشاء: {category.created_at.strftime('%Y-%m-%d')}"

    kb = category_detail_kb(category)

    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
        except Exception:
            await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


# ══════════════════════════════════════════════
# ══════════════ تفعيل / تعطيل ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:cat_toggle:"))
async def cat_toggle(callback: CallbackQuery, session, db_user):
    """يبدل حالة قسم رئيسي."""
    cat_id = int(callback.data.split(":")[2])
    category = await session.get(Category, cat_id)

    if not category:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    category.is_active = not category.is_active
    await session.commit()

    await AuditService.log_toggle(
        admin_id=db_user.id,
        entity_type="category",
        entity_id=category.id,
        entity_name=category.name_ar,
        new_status=category.is_active,
        session=session,
    )

    status_text = "✅ تم تفعيل" if category.is_active else "❌ تم تعطيل"
    await callback.answer(f"{status_text} القسم")

    await session.refresh(category)
    await _show_category_details(callback.message, category, edit=True)


# ══════════════════════════════════════════════
# ══════════════ تعديل القسم ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:cat_edit:"))
async def cat_edit_start(callback: CallbackQuery, state: FSMContext):
    """يبدأ تعديل حقل في القسم."""
    parts = callback.data.split(":")
    field = parts[2]
    cat_id = int(parts[3])

    await state.update_data(
        edit_category_id=cat_id,
        edit_field=field,
    )

    field_prompts = {
        "name": "📝 أرسل الاسم الجديد للقسم:",
        "emoji": "🎨 أرسل الإيموجي الجديد:",
        "sort": ("🔢 أرسل رقم الترتيب الجديد (الأصغر يظهر أولاً):"),
        "desc": "📝 أرسل شرح القسم (يظهر للزبون عند فتح القسم):\nأرسل <b>مسح</b> لإزالة الشرح:",
    }

    prompt = field_prompts.get(field, "أرسل القيمة الجديدة:")

    await callback.message.edit_text(
        prompt,
        reply_markup=cancel_add_kb(f"admin:cat_view:{cat_id}"),
    )
    await state.set_state(AdminCategoryStates.waiting_edit_value)


@router.message(AdminCategoryStates.waiting_edit_value)
async def cat_edit_value_received(
    message: Message,
    state: FSMContext,
    session,
    db_user,
):
    """يستقبل القيمة الجديدة ويحدث القسم."""
    data = await state.get_data()
    cat_id = data.get("edit_category_id")
    field = data.get("edit_field")

    if not cat_id or not field:
        await message.answer("⚠️ جلسة منتهية.")
        await state.clear()
        return

    category = await session.get(Category, cat_id)
    if not category:
        await message.answer("⚠️ القسم غير موجود.")
        await state.clear()
        return

    value = message.text.strip()
    old_value = None

    if field == "name":
        if len(value) < 2 or len(value) > 64:
            await message.answer("⚠️ الاسم يجب أن يكون بين 2 و 64 حرف.")
            return
        old_value = category.name_ar
        category.name_ar = value

    elif field == "emoji":
        if len(value) > 8:
            await message.answer("⚠️ إيموجي واحد فقط.")
            return
        old_value = category.emoji
        category.emoji = value

    elif field == "sort":
        try:
            sort_val = int(value)
        except ValueError:
            await message.answer("⚠️ أرسل رقماً صحيحاً.")
            return
        old_value = category.sort_order
        category.sort_order = sort_val

    elif field == "desc":
        if value in ("مسح", "-", "", "0"):
            value = ""
        if len(value) > 500:
            await message.answer("⚠️ الشرح طويل جداً (الحد الأقصى 500 حرف).")
            return
        old_value = category.description
        category.description = value or None

    await session.commit()

    await AuditService.log_update(
        admin_id=db_user.id,
        entity_type="category",
        entity_id=category.id,
        entity_name=category.name_ar,
        field=field,
        old_value=old_value,
        new_value=value,
        session=session,
    )

    await message.answer(f"✅ تم تحديث {field}.")
    await state.clear()

    await session.refresh(category)
    await _show_category_details(message, category, edit=False)


# ══════════════════════════════════════════════
# ══════════════ حذف قسم رئيسي ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:cat_delete_confirm:"))
async def cat_delete_confirm(callback: CallbackQuery, session):
    """تأكيد حذف قسم رئيسي."""
    cat_id = int(callback.data.split(":")[2])
    category = await DynamicService.get_category(session, cat_id)

    if not category:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    subs_count = len(category.sub_categories) if category.sub_categories else 0

    text = f"⚠️ <b>تأكيد حذف القسم</b>\n\nسيتم حذف:\n• القسم: {category.emoji} {category.name_ar}\n"
    if subs_count > 0:
        text += f"• <b>{subs_count}</b> قسم فرعي\n• كل المنتجات داخل الأقسام الفرعية\n"
    text += "\n<b>هل أنت متأكد؟ هذا الإجراء لا يمكن التراجع عنه!</b>"

    await callback.message.edit_text(
        text,
        reply_markup=confirm_delete_category_kb(category.id, subs_count),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:cat_delete:"))
async def cat_delete(callback: CallbackQuery, session, db_user):
    """يحذف قسم رئيسي."""
    cat_id = int(callback.data.split(":")[2])
    category = await DynamicService.get_category(session, cat_id)

    if not category:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    cat_name = category.name_ar

    await AuditService.log_delete(
        admin_id=db_user.id,
        entity_type="category",
        entity_id=category.id,
        entity_name=cat_name,
        session=session,
    )

    await session.delete(category)
    await session.commit()

    await callback.answer(f"🗑 تم حذف: {cat_name}")
    logger.info(f"الأدمن {db_user.telegram_id} حذف القسم #{cat_id} - {cat_name}")

    await categories_list(callback, session)
    # ══════════════════════════════════════════════


# ══════════════ الأقسام الفرعية ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("admin:subcat_list:"))
async def subcat_list(callback: CallbackQuery, session):
    """يعرض قائمة الأقسام الفرعية (المستوى الأول) لقسم رئيسي."""
    cat_id = int(callback.data.split(":")[2])
    category = await DynamicService.get_category(session, cat_id)

    if not category:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    # الأقسام الداخلية (المستوى الثاني في الرشق) تُدار من صفحة التطبيق نفسه.
    subs = await DynamicService.get_all_root_sub_categories(session, cat_id)

    if not subs:
        text = (
            f"📂 <b>{category.emoji} "
            f"{category.name_ar}</b>\n\n"
            "لا توجد أقسام فرعية.\n"
            "اضغط الزر أدناه لإضافة قسم فرعي."
        )
    else:
        text = (
            f"📂 <b>{category.emoji} "
            f"{category.name_ar}</b>\n\n"
            f"عدد الأقسام الفرعية: <b>{len(subs)}</b>\n\n"
            "🟢 = مفعّل | 🔴 = معطّل\n"
            "الرقم بين قوسين = عدد المنتجات المباشرة\n\n"
            "💡 أقسام قسم الرشق الداخلية (متابعون/لايكات/...) "
            "تُدار من داخل صفحة التطبيق."
        )

    await callback.message.edit_text(
        text,
        reply_markup=sub_categories_list_kb(cat_id, subs),
    )


# ══════════════ إضافة قسم فرعي ══════════════


@router.callback_query(F.data.startswith("admin:subcat_add:"))
async def subcat_add_start(callback: CallbackQuery, state: FSMContext):
    """يبدأ Wizard إضافة قسم فرعي (مستوى أول)."""
    cat_id = int(callback.data.split(":")[2])
    await state.update_data(parent_category_id=cat_id, parent_sub_id=None)

    await callback.message.edit_text(
        "📂 <b>إضافة قسم فرعي جديد</b>\n\n"
        "الخطوة 1️⃣ من 4️⃣\n\n"
        "📝 أرسل اسم القسم الفرعي:\n"
        "(مثال: <code>إنستقرام</code> أو "
        "<code>ببجي موبايل</code>)",
        reply_markup=cancel_add_kb(f"admin:subcat_list:{cat_id}"),
    )
    await state.set_state(AdminSubCategoryStates.waiting_name)


@router.callback_query(F.data.startswith("admin:subcat_add_child:"))
async def subcat_add_child_start(callback: CallbackQuery, session, state: FSMContext):
    """يبدأ Wizard إضافة «قسم داخلي» داخل تطبيق (قسم فرعي من المستوى الأول).

    مثال: داخل إنستغرام قسم داخلي «لايكات». الأقسام الداخلية هي التي
    تستقبل المنتجات وتظهر للزبون عند فتح التطبيق.
    """
    parent_sub_id = int(callback.data.split(":")[2])
    parent = await DynamicService.get_sub_category(session, parent_sub_id)
    if parent is None:
        await callback.answer("⚠️ التطبيق غير موجود", show_alert=True)
        return
    await state.update_data(
        parent_category_id=parent.category_id,
        parent_sub_id=parent.id,
    )
    await callback.message.edit_text(
        f"📂 <b>إضافة قسم داخلي</b> داخل: "
        f"{parent.emoji or ''} {parent.name_ar}\n\n"
        "الخطوة 1️⃣ من 4️⃣\n\n"
        "📝 أرسل اسم القسم الداخلي:\n"
        "(مثال: <code>لايكات</code> أو <code>متابعون</code> أو "
        "<code>مشاهدات</code>)",
        reply_markup=cancel_add_kb(f"admin:subcat_view:{parent.id}"),
    )
    await state.set_state(AdminSubCategoryStates.waiting_name)


@router.message(AdminSubCategoryStates.waiting_name)
async def subcat_name_received(message: Message, state: FSMContext):
    """استقبال اسم القسم الفرعي."""
    name = message.text.strip()

    if len(name) < 2:
        await message.answer("⚠️ الاسم قصير جداً.")
        return

    if len(name) > 64:
        await message.answer("⚠️ الاسم طويل جداً (الحد الأقصى 64).")
        return

    await state.update_data(name=name)

    await message.answer(
        f"✅ الاسم: <b>{name}</b>\n\nالخطوة 2️⃣ من 4️⃣\n\n🎨 اختر إيموجي للقسم الفرعي:",
        reply_markup=select_emoji_kb("smm"),
    )
    await state.set_state(AdminSubCategoryStates.waiting_emoji)


async def _subcat_cancel_target(state: FSMContext) -> str:
    """زر الإلغاء المناسب أثناء wizard: صفحة التطبيق للقسم الداخلي."""
    data = await state.get_data()
    parent_sub_id = data.get("parent_sub_id")
    if parent_sub_id:
        return f"admin:subcat_view:{parent_sub_id}"
    cat_id = data.get("parent_category_id")
    if cat_id:
        return f"admin:subcat_list:{cat_id}"
    return "admin:categories"


@router.callback_query(
    AdminSubCategoryStates.waiting_emoji,
    F.data.startswith("admin:cat_emoji:"),
)
async def subcat_emoji_selected(callback: CallbackQuery, state: FSMContext):
    """استقبال الإيموجي."""
    emoji_choice = callback.data.split(":", 2)[2]

    if emoji_choice == "custom":
        await callback.message.edit_text("✏️ أرسل إيموجي مخصص:")
        return

    if emoji_choice == "default":
        emoji = "📱"
    else:
        emoji = emoji_choice

    await state.update_data(emoji=emoji)

    await callback.message.edit_text(
        f"✅ الإيموجي: {emoji}\n\nالخطوة 3️⃣ من 4️⃣\n\n📝 أرسل وصف القسم (اختياري):\nأو اضغط ⏭ للتخطي",
        reply_markup=cancel_add_kb(await _subcat_cancel_target(state)),
    )
    await state.set_state(AdminSubCategoryStates.waiting_description)


@router.message(AdminSubCategoryStates.waiting_emoji)
async def subcat_custom_emoji_received(message: Message, state: FSMContext):
    """استقبال إيموجي مخصص."""
    emoji = message.text.strip()

    if len(emoji) > 8:
        await message.answer("⚠️ إيموجي واحد فقط.")
        return

    await state.update_data(emoji=emoji)

    await message.answer(
        f"✅ الإيموجي: {emoji}\n\n"
        "الخطوة 3️⃣ من 4️⃣\n\n"
        "📝 أرسل وصف القسم (اختياري):\n"
        "أرسل نصاً أو - للتخطي"
    )
    await state.set_state(AdminSubCategoryStates.waiting_description)


@router.message(AdminSubCategoryStates.waiting_description)
async def subcat_description_received(message: Message, state: FSMContext):
    """استقبال الوصف."""
    desc = message.text.strip()
    if desc == "-" or desc == "تخطي":
        desc = None
    elif len(desc) > 255:
        await message.answer("⚠️ الوصف طويل جداً (255).")
        return

    await state.update_data(description=desc)
    data = await state.get_data()
    cat_id = data.get("parent_category_id")

    await message.answer(
        "الخطوة 4️⃣ من 4️⃣\n\n🖼 هل تريد إضافة صورة للقسم؟",
        reply_markup=image_options_kb(
            context="subcat_wizard",
            entity_id=cat_id,
        ),
    )
    await state.set_state(AdminSubCategoryStates.waiting_image)


@router.callback_query(
    AdminSubCategoryStates.waiting_image,
    F.data.startswith("admin:subcat_wizard_img:"),
)
async def subcat_image_option(
    callback: CallbackQuery,
    state: FSMContext,
    session,
    db_user,
):
    """اختيار خيار الصورة."""
    parts = callback.data.split(":")
    option = parts[2]

    if option == "skip":
        await _create_sub_category(callback.message, state, session, db_user)
        return

    if option == "upload":
        await callback.message.edit_text("📤 أرسل الصورة الآن:")
        return

    if option == "url":
        await callback.message.edit_text("🔗 أرسل رابط الصورة:")
        return


@router.message(
    AdminSubCategoryStates.waiting_image,
    F.photo,
)
async def subcat_image_uploaded(
    message: Message,
    state: FSMContext,
    session,
    db_user,
):
    """استقبال صورة مرفوعة."""
    file_id = message.photo[-1].file_id
    await state.update_data(image_file_id=file_id)
    await _create_sub_category(message, state, session, db_user)


@router.message(AdminSubCategoryStates.waiting_image)
async def subcat_image_url_received(
    message: Message,
    state: FSMContext,
    session,
    db_user,
):
    """استقبال رابط صورة."""
    url = message.text.strip()

    if url.lower() in ("-", "تخطي", "skip"):
        await _create_sub_category(message, state, session, db_user)
        return

    if not url.startswith(("http://", "https://")):
        await message.answer("⚠️ أرسل رابط صورة صحيح أو - للتخطي.")
        return

    if len(url) > 500:
        await message.answer("⚠️ الرابط طويل جداً.")
        return

    await state.update_data(image_url=url)
    await _create_sub_category(message, state, session, db_user)


async def _create_sub_category(
    message,
    state: FSMContext,
    session,
    db_user,
):
    """ينشئ القسم الفرعي في قاعدة البيانات (أو القسم الداخلي إذا وُجد أب)."""
    data = await state.get_data()
    parent_sub_id = data.get("parent_sub_id")

    try:
        sub_category = SubCategory(
            category_id=data["parent_category_id"],
            parent_sub_category_id=parent_sub_id,
            name_ar=data["name"],
            emoji=data.get("emoji", "📱"),
            description=data.get("description"),
            image_file_id=data.get("image_file_id"),
            image_url=data.get("image_url"),
            is_active=True,
        )
        session.add(sub_category)
        await session.commit()
        await session.refresh(sub_category)
    except Exception as e:
        logger.error(f"فشل إنشاء قسم فرعي: {e}")
        if hasattr(message, "answer"):
            await message.answer(f"❌ فشل الإنشاء: {e}")
        await state.clear()
        return

    await AuditService.log_create(
        admin_id=db_user.id,
        entity_type="sub_category",
        entity_id=sub_category.id,
        entity_name=sub_category.name_ar,
        new_value={
            "name": sub_category.name_ar,
            "emoji": sub_category.emoji,
            "parent_sub_category_id": parent_sub_id,
        },
        session=session,
    )

    if parent_sub_id:
        kind_text = "القسم الداخلي"
    else:
        kind_text = "القسم الفرعي"
    success_text = (
        f"✅ <b>تم إنشاء {kind_text} بنجاح!</b>\n\n"
        f"{sub_category.emoji} <b>{sub_category.name_ar}</b>\n\n"
        "يمكنك الآن إضافة منتجات له."
    )

    if hasattr(message, "answer"):
        await message.answer(success_text)
    else:
        try:
            await message.edit_text(success_text)
        except Exception:
            pass

    # عند إنشاء قسم داخلي نعيد الأدمن لصفحة التطبيق ليرى القسم الجديد.
    if parent_sub_id:
        parent = await session.get(SubCategory, parent_sub_id)
        if parent is not None:
            await _show_sub_category_details(message, parent, session, edit=False)

    await state.clear()


# ══════════════ تفاصيل قسم فرعي ══════════════


@router.callback_query(F.data.startswith("admin:subcat_view:"))
async def subcat_view(callback: CallbackQuery, session):
    """يعرض تفاصيل قسم فرعي."""
    sub_id = int(callback.data.split(":")[2])
    sub = await DynamicService.get_sub_category(session, sub_id)

    if not sub:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    await _show_sub_category_details(callback.message, sub, session, edit=True)


async def _show_sub_category_details(
    message,
    sub: SubCategory,
    session,
    edit: bool = True,
):
    """يعرض تفاصيل قسم فرعي.

    إذا كان القسم تطبيقاً يحوي أقساماً داخلية (قسم الرشق) تُعرض الأقسام
    الداخلية بأزرارها أعلى أزرار الإدارة.
    """
    status = "🟢 مفعّل" if sub.is_active else "🔴 معطّل"

    products_count = len(sub.products) if sub.products else 0
    active_products = 0
    if sub.products:
        from database.models import ProductStatus

        active_products = sum(1 for p in sub.products if p.status == ProductStatus.ACTIVE)

    children = await DynamicService.get_all_child_sections(session, sub.id)
    child_counts: dict[int, int] = {}
    if children:
        child_counts = await DynamicService.active_product_counts_by_sub(
            session, [child.id for child in children]
        )

    sub_margin_text = (
        f"{sub.profit_margin_percent}% (خاص بالقسم)"
        if sub.profit_margin_percent is not None
        else "غير مضبوط — يرث هامش القسم الرئيسي/العالمي"
    )
    text = (
        f"{sub.emoji} <b>{sub.name_ar}</b>\n"
        f"🆔 ID: <code>{sub.id}</code> | ربط زر: <code>subcat:{sub.id}</code>\n\n"
        f"📊 الحالة: {status}\n🔢 الترتيب: {sub.sort_order}\n"
        f"💵 هامش الربح: <b>{sub_margin_text}</b>\n\n"
    )

    if sub.description:
        text += f"📝 الوصف: <i>{sub.description}</i>\n\n"

    text += f"📦 عدد المنتجات: <b>{products_count}</b>\n🟢 نشطة منها: <b>{active_products}</b>\n\n"

    if children:
        text += (
            f"📂 <b>الأقسام الداخلية: {len(children)}</b> "
            "(متابعون/لايكات/مشاهدات...)\n"
            "المنتجات تنشر داخل الأقسام الداخلية وليس هنا مباشرةً، "
            "والرقم بين قوسين = عدد منتجات القسم.\n\n"
        )
    elif sub.kind_key:
        text += (
            "🏷 هذا القسم قسم داخلي ضمن تطبيق. منتجاته تظهر للزبون عند فتح التطبيق "
            "ثم اختيار هذا القسم.\n\n"
        )

    if sub.image_file_id or sub.image_url:
        text += "🖼 يحتوي صورة ✅\n\n"

    text += f"📅 تاريخ الإنشاء: {sub.created_at.strftime('%Y-%m-%d')}"

    kb = sub_category_detail_kb(
        sub,
        children=[(child, child_counts.get(child.id, 0)) for child in children],
    )

    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
        except Exception:
            await message.answer(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


# ══════════════ تفعيل/تعطيل قسم فرعي ══════════════


@router.callback_query(F.data.startswith("admin:subcat_toggle:"))
async def subcat_toggle(callback: CallbackQuery, session, db_user):
    """يبدل حالة قسم فرعي."""
    sub_id = int(callback.data.split(":")[2])
    sub = await session.get(SubCategory, sub_id)

    if not sub:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    sub.is_active = not sub.is_active
    await session.commit()

    await AuditService.log_toggle(
        admin_id=db_user.id,
        entity_type="sub_category",
        entity_id=sub.id,
        entity_name=sub.name_ar,
        new_status=sub.is_active,
        session=session,
    )

    status_text = "✅ تم تفعيل" if sub.is_active else "❌ تم تعطيل"
    await callback.answer(f"{status_text} القسم الفرعي")

    await session.refresh(sub)
    await _show_sub_category_details(callback.message, sub, session, edit=True)


# ══════════════ تعديل قسم فرعي ══════════════


@router.callback_query(F.data.startswith("admin:subcat_edit:"))
async def subcat_edit_start(callback: CallbackQuery, state: FSMContext):
    """يبدأ تعديل حقل في قسم فرعي."""
    parts = callback.data.split(":")
    field = parts[2]
    sub_id = int(parts[3])

    await state.update_data(
        edit_sub_id=sub_id,
        edit_field=field,
    )

    field_prompts = {
        "name": "📝 أرسل الاسم الجديد:",
        "emoji": "🎨 أرسل الإيموجي الجديد:",
        "desc": "📝 أرسل الوصف الجديد (أو - لحذفه):",
        "image": "🖼 أرسل الصورة الجديدة (صورة أو رابط أو - لحذفها):",
        "sort": "🔢 أرسل رقم الترتيب الجديد:",
    }

    prompt = field_prompts.get(field, "أرسل القيمة الجديدة:")

    await callback.message.edit_text(
        prompt,
        reply_markup=cancel_add_kb(f"admin:subcat_view:{sub_id}"),
    )
    await state.set_state(AdminSubCategoryStates.waiting_edit_value)


@router.message(
    AdminSubCategoryStates.waiting_edit_value,
    F.photo,
)
async def subcat_edit_image_photo(
    message: Message,
    state: FSMContext,
    session,
    db_user,
):
    """يستقبل صورة للتعديل."""
    data = await state.get_data()
    sub_id = data.get("edit_sub_id")
    field = data.get("edit_field")

    if field != "image":
        return

    sub = await session.get(SubCategory, sub_id)
    if not sub:
        await message.answer("⚠️ القسم غير موجود.")
        await state.clear()
        return

    old_value = "لديه صورة" if sub.image_file_id else "بدون"
    sub.image_file_id = message.photo[-1].file_id
    sub.image_url = None
    await session.commit()

    await AuditService.log_update(
        admin_id=db_user.id,
        entity_type="sub_category",
        entity_id=sub.id,
        entity_name=sub.name_ar,
        field="image",
        old_value=old_value,
        new_value="لديه صورة",
        session=session,
    )

    await message.answer("✅ تم تحديث الصورة.")
    await state.clear()

    await session.refresh(sub)
    await _show_sub_category_details(message, sub, session, edit=False)


@router.message(AdminSubCategoryStates.waiting_edit_value)
async def subcat_edit_value_received(
    message: Message,
    state: FSMContext,
    session,
    db_user,
):
    """يستقبل القيمة الجديدة."""
    data = await state.get_data()
    sub_id = data.get("edit_sub_id")
    field = data.get("edit_field")

    if not sub_id or not field:
        await message.answer("⚠️ جلسة منتهية.")
        await state.clear()
        return

    sub = await session.get(SubCategory, sub_id)
    if not sub:
        await message.answer("⚠️ القسم غير موجود.")
        await state.clear()
        return

    value = message.text.strip()
    old_value = None

    if field == "name":
        if len(value) < 2 or len(value) > 64:
            await message.answer("⚠️ الاسم يجب أن يكون بين 2 و 64.")
            return
        old_value = sub.name_ar
        sub.name_ar = value

    elif field == "emoji":
        if len(value) > 8:
            await message.answer("⚠️ إيموجي واحد فقط.")
            return
        old_value = sub.emoji
        sub.emoji = value

    elif field == "desc":
        if value == "-":
            old_value = sub.description
            sub.description = None
        else:
            if len(value) > 255:
                await message.answer("⚠️ الوصف طويل.")
                return
            old_value = sub.description
            sub.description = value

    elif field == "image":
        if value == "-":
            old_value = "لديه صورة" if sub.image_file_id or sub.image_url else "بدون"
            sub.image_file_id = None
            sub.image_url = None
        elif value.startswith(("http://", "https://")):
            if len(value) > 500:
                await message.answer("⚠️ الرابط طويل.")
                return
            old_value = "قديمة"
            sub.image_url = value
            sub.image_file_id = None
        else:
            await message.answer("⚠️ أرسل رابط أو صورة أو - للحذف.")
            return

    elif field == "sort":
        try:
            sort_val = int(value)
        except ValueError:
            await message.answer("⚠️ أرسل رقماً.")
            return
        old_value = sub.sort_order
        sub.sort_order = sort_val

    await session.commit()

    await AuditService.log_update(
        admin_id=db_user.id,
        entity_type="sub_category",
        entity_id=sub.id,
        entity_name=sub.name_ar,
        field=field,
        old_value=old_value,
        new_value=value,
        session=session,
    )

    await message.answer(f"✅ تم تحديث {field}.")
    await state.clear()

    await session.refresh(sub)
    await _show_sub_category_details(message, sub, session, edit=False)


# ══════════════ حذف قسم فرعي ══════════════


@router.callback_query(F.data.startswith("admin:subcat_delete_confirm:"))
async def subcat_delete_confirm(callback: CallbackQuery, session):
    """تأكيد حذف قسم فرعي."""
    sub_id = int(callback.data.split(":")[2])
    sub = await DynamicService.get_sub_category(session, sub_id)

    if not sub:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    products_count = len(sub.products) if sub.products else 0
    children = await DynamicService.get_all_child_sections(session, sub.id)

    text = f"⚠️ <b>تأكيد حذف القسم الفرعي</b>\n\nسيتم حذف:\n• القسم: {sub.emoji} {sub.name_ar}\n"
    if products_count > 0:
        text += f"• <b>{products_count}</b> منتج مباشر\n"
    if children:
        text += f"• <b>{len(children)}</b> قسم داخلي وكل منتجاتها\n"
    text += "\n<b>هل أنت متأكد؟ لا يمكن التراجع!</b>"

    await callback.message.edit_text(
        text,
        reply_markup=confirm_delete_sub_category_kb(sub.id, sub.category_id, products_count),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:subcat_delete:"))
async def subcat_delete(callback: CallbackQuery, session, db_user):
    """يحذف قسم فرعي."""
    sub_id = int(callback.data.split(":")[2])
    sub = await DynamicService.get_sub_category(session, sub_id)

    if not sub:
        await callback.answer("⚠️ غير موجود", show_alert=True)
        return

    sub_name = sub.name_ar
    parent_category_id = sub.category_id
    parent_sub_id = sub.parent_sub_category_id

    await AuditService.log_delete(
        admin_id=db_user.id,
        entity_type="sub_category",
        entity_id=sub.id,
        entity_name=sub_name,
        session=session,
    )

    await session.delete(sub)
    await session.commit()

    await callback.answer(f"🗑 تم حذف: {sub_name}")
    logger.info(f"الأدمن {db_user.telegram_id} حذف القسم الفرعي #{sub_id}")

    # حذف قسم داخلي؟ نعيد الأدمن لصفحة التطبيق الذي كان يحويه.
    if parent_sub_id is not None:
        parent = await session.get(SubCategory, parent_sub_id)
        if parent is not None:
            await _show_sub_category_details(callback.message, parent, session, edit=False)
            return

    from types import SimpleNamespace

    fake_callback = SimpleNamespace(
        data=f"admin:subcat_list:{parent_category_id}",
        message=callback.message,
        answer=callback.answer,
        from_user=callback.from_user,
    )
    await subcat_list(fake_callback, session)
