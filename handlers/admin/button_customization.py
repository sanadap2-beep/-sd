"""Admin panel: manage labels, custom emojis, and colors of every bot button."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from filters.admin_filter import IsAdmin
from services.button_customization_service import (
    STYLES,
    ButtonCustomizationService,
    button_tree,
    find_section,
    find_spec,
)
from states.states import AdminButtonCustomStates

router = Router(name="admin_button_customization")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _section_list_kb() -> InlineKeyboardMarkup:
    overrides = await ButtonCustomizationService.overrides()
    rows = []
    for section in button_tree():
        customized = sum(1 for b in section.buttons if b.key in overrides)
        mark = f" ✏️{customized}" if customized else ""
        rows.append([InlineKeyboardButton(
            text=f"{section.title}{mark}",
            callback_data=f"bc:sec:{section.key}",
        )])
    rows.append([InlineKeyboardButton(text="🔙 لوحة الإدارة", callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _sections_text() -> str:
    return (
        "🎨 <b>إدارة الأزرار والإيموجي</b>\n\n"
        "اختر قسماً لعرض أزراره، ثم عدّل اسم أي زر أو إيموجه "
        "(بما فيه الإيموجي المخصص من Telegram Premium) أو لونه."
    )


@router.callback_query(F.data == "admin:buttons_custom")
async def buttons_custom_home(callback: CallbackQuery):
    await callback.message.edit_text(
        await _sections_text(), reply_markup=await _section_list_kb()
    )
    await callback.answer()


@router.callback_query(F.data == "bc:home")
async def bc_home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        await _sections_text(), reply_markup=await _section_list_kb()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bc:sec:"))
async def bc_section(callback: CallbackQuery):
    section_key = callback.data[len("bc:sec:"):]
    section = find_section(section_key)
    if not section:
        await callback.answer("قسم غير معروف", show_alert=True)
        return
    overrides = await ButtonCustomizationService.overrides()
    rows = []
    for spec in section.buttons:
        entry = overrides.get(spec.key, {})
        label = entry.get("label") or spec.label
        mark = " ✏️" if entry else ""
        rows.append([InlineKeyboardButton(
            text=f"{label}{mark}", callback_data=f"bc:btn:{spec.key}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 رجوع", callback_data="bc:home")])
    await callback.message.edit_text(
        f"{section.title}\n\nاضغط على الزر لتعديله:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


def _detail_text(spec, entry: dict) -> str:
    label = entry.get("label") or spec.label
    style = entry.get("style", spec.style) or "رمادي/افتراضي"
    custom = entry.get("custom_emoji_id") or "—"
    return (
        f"🎨 <b>تعديل الزر</b>\n\n"
        f"الاسم الحالي: <b>{label}</b>\n"
        f"اللون: <code>{style}</code>\n"
        f"إيموجي مخصص: <code>{custom}</code>\n"
        f"المعرّف: <code>{spec.key}</code>"
    )


@router.callback_query(F.data.startswith("bc:btn:"))
async def bc_button(callback: CallbackQuery):
    key = callback.data[len("bc:btn:"):]
    spec = find_spec(key)
    if not spec:
        await callback.answer("زر غير معروف", show_alert=True)
        return
    overrides = await ButtonCustomizationService.overrides()
    entry = overrides.get(key, {})
    section = next((s for s in button_tree() if any(b.key == key for b in s.buttons)), None)
    back_cb = f"bc:sec:{section.key}" if section else "bc:home"
    rows = [
        [InlineKeyboardButton(text="✏️ تغيير الاسم", callback_data=f"bc:rename:{key}")],
        [InlineKeyboardButton(text="😀 تعيين إيموجي", callback_data=f"bc:emoji:{key}")],
        [InlineKeyboardButton(text="🎨 تدوير اللون", callback_data=f"bc:style:{key}")],
        [InlineKeyboardButton(text="↩️ استعادة الافتراضي", callback_data=f"bc:reset:{key}", style="danger")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data=back_cb)],
    ]
    await callback.message.edit_text(
        _detail_text(spec, entry),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bc:rename:"))
async def bc_rename(callback: CallbackQuery, state: FSMContext):
    key = callback.data[len("bc:rename:"):]
    await state.update_data(bc_key=key)
    await state.set_state(AdminButtonCustomStates.waiting_label)
    await callback.message.edit_text(
        "✏️ أرسل الاسم الجديد للزر (مع الإيموجي الذي تريده):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="إلغاء", callback_data=f"bc:btn:{key}")
        ]]),
    )
    await callback.answer()


@router.message(AdminButtonCustomStates.waiting_label)
async def bc_label_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    key = data.get("bc_key")
    label = (message.text or "").strip()
    if not label:
        await message.answer("⚠️ الاسم فارغ، أعد المحاولة.")
        return
    if spec := find_spec(key):
        await ButtonCustomizationService.set_field(session, key, "label", label)
        await message.answer(f"✅ تم تحديث الاسم إلى: {label}")
    await state.clear()
    await message.answer(
        "ارجع من لوحة الأدمن لمعاينة الشكل الجديد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎨 إدارة الأزرار", callback_data="admin:buttons_custom")
        ]]),
    )


@router.callback_query(F.data.startswith("bc:emoji:"))
async def bc_emoji(callback: CallbackQuery, state: FSMContext):
    key = callback.data[len("bc:emoji:"):]
    await state.update_data(bc_key=key)
    await state.set_state(AdminButtonCustomStates.waiting_emoji)
    await callback.message.edit_text(
        "😀 أرسل الإيموجي الذي تريده للزر:\n\n"
        "• إيموجي عادي: يحل محل الإيموجي الأول في الاسم.\n"
        "• إيموجي مخصص من Telegram Premium: يظهر على الزر مباشرة.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="إلغاء", callback_data=f"bc:btn:{key}")
        ]]),
    )
    await callback.answer()


@router.message(AdminButtonCustomStates.waiting_emoji)
async def bc_emoji_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    key = data.get("bc_key")
    spec = find_spec(key)
    if not spec:
        await state.clear()
        return

    custom_id = None
    for entity in message.entities or []:
        if entity.type == "custom_emoji" and getattr(entity, "custom_emoji_id", None):
            custom_id = entity.custom_emoji_id
            break

    if custom_id:
        await ButtonCustomizationService.set_field(session, key, "custom_emoji_id", custom_id)
        await message.answer("✅ تم تعيين الإيموجي المخصص للزر.")
    else:
        emoji_text = (message.text or "").strip()
        if not emoji_text:
            await message.answer("⚠️ أرسل إيموجي فقط من فضلك.")
            return
        overrides = await ButtonCustomizationService.overrides()
        current = overrides.get(key, {}).get("label") or spec.label
        # استبدال أول إيموجي (أول محرف غير نصي) بالمُرسل
        parts = current.strip().split(maxsplit=1)
        rest = parts[1] if len(parts) > 1 else current
        new_label = f"{emoji_text} {rest}".strip()
        await ButtonCustomizationService.set_field(session, key, "label", new_label)
        await message.answer(f"✅ أصبح اسم الزر: {new_label}")

    await state.clear()
    await message.answer(
        "ارجع من لوحة الأدمن لمعاينة الشكل الجديد.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🎨 إدارة الأزرار", callback_data="admin:buttons_custom")
        ]]),
    )


@router.callback_query(F.data.startswith("bc:style:"))
async def bc_style(callback: CallbackQuery, session):
    key = callback.data[len("bc:style:"):]
    spec = find_spec(key)
    if not spec:
        await callback.answer("زر غير معروف", show_alert=True)
        return
    overrides = await ButtonCustomizationService.overrides()
    entry = overrides.get(key, {})
    current = entry.get("style", spec.style)
    try:
        idx = STYLES.index(current) if current in STYLES else -1
    except ValueError:
        idx = -1
    next_style = STYLES[(idx + 1) % len(STYLES)]
    await ButtonCustomizationService.set_field(session, key, "style", next_style)
    await callback.answer(f"اللون الآن: {next_style}")
    # إعادة عرض التفاصيل
    entry = (await ButtonCustomizationService.overrides()).get(key, {})
    section = next((s for s in button_tree() if any(b.key == key for b in s.buttons)), None)
    back_cb = f"bc:sec:{section.key}" if section else "bc:home"
    rows = [
        [InlineKeyboardButton(text="✏️ تغيير الاسم", callback_data=f"bc:rename:{key}")],
        [InlineKeyboardButton(text="😀 تعيين إيموجي", callback_data=f"bc:emoji:{key}")],
        [InlineKeyboardButton(text="🎨 تدوير اللون", callback_data=f"bc:style:{key}")],
        [InlineKeyboardButton(text="↩️ استعادة الافتراضي", callback_data=f"bc:reset:{key}", style="danger")],
        [InlineKeyboardButton(text="🔙 رجوع", callback_data=back_cb)],
    ]
    await callback.message.edit_text(
        _detail_text(spec, entry),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("bc:reset:"))
async def bc_reset(callback: CallbackQuery, session):
    key = callback.data[len("bc:reset:"):]
    await ButtonCustomizationService.reset(session, key)
    await callback.answer("↩️ تمت الاستعادة للافتراضي")
    spec = find_spec(key)
    if spec:
        section = next((s for s in button_tree() if any(b.key == key for b in s.buttons)), None)
        back_cb = f"bc:sec:{section.key}" if section else "bc:home"
        rows = [
            [InlineKeyboardButton(text="✏️ تغيير الاسم", callback_data=f"bc:rename:{key}")],
            [InlineKeyboardButton(text="😀 تعيين إيموجي", callback_data=f"bc:emoji:{key}")],
            [InlineKeyboardButton(text="🎨 تدوير اللون", callback_data=f"bc:style:{key}")],
            [InlineKeyboardButton(text="↩️ استعادة الافتراضي", callback_data=f"bc:reset:{key}", style="danger")],
            [InlineKeyboardButton(text="🔙 رجوع", callback_data=back_cb)],
        ]
        await callback.message.edit_text(
            _detail_text(spec, {}),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
