"""
مركز التحكم بالإضافات — لوحة الأدمن.

كل إضافة في البوت تُدار من هنا: تفعيل/إيقاف فوري، تعديل إعداداتها،
إعادتها للقيم الافتراضية، ومعرفة هل هي مستعملة فعلاً.

لا توجد إضافة في البوت خارج هذا المركز؛ السجل المرجعي في
services/feature_registry.py ويُزامَن مع قاعدة البيانات عند الإقلاع.
"""

import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from filters.admin_filter import IsAdmin
from keyboards.admin import admin_main_kb
from keyboards.admin_features import (
    back_to_features_kb,
    feature_categories_kb,
    feature_detail_kb,
    feature_options_kb,
    features_in_category_kb,
    set_enabled_cache,
)
from services.audit_service import AuditAction, AuditService
from services.feature_registry import BY_KEY, by_category, categories_ordered
from services.feature_service import FeatureService
from states.states import AdminBulkDiscountStates, AdminFeatureStates

router = Router(name="admin_features")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

MAX_RESULTS = 12


# ══════════════ الدخول ══════════════


@router.callback_query(F.data == "admin:features")
async def features_home(callback: CallbackQuery):
    counts = {spec.key: await FeatureService.enabled(spec.key) for spec in BY_KEY.values()}
    set_enabled_cache(counts)
    active = sum(1 for value in counts.values() if value)
    grouped = by_category()
    await callback.message.edit_text(
        "🧩 <b>مركز التحكم بالإضافات</b>\n\n"
        f"عدد الإضافات: <b>{len(counts)}</b>\n"
        f"المفعّلة الآن: <b>{active}</b>\n"
        f"الموقوفة: <b>{len(counts) - active}</b>\n\n"
        "أي تعديل هنا يسري فوراً على البوت بدون إعادة تشغيل.\n\n"
        "اختر فئة:",
        reply_markup=feature_categories_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "feat_home")
async def features_home_back(callback: CallbackQuery):
    await features_home(callback)


# ══════════════ الفئات ══════════════


@router.callback_query(F.data.startswith("feat_cat:"))
async def category_open(callback: CallbackQuery):
    category = callback.data.split(":", 1)[1]
    await _show_category(callback, category, 0)


@router.callback_query(F.data.startswith("feat_catp:"))
async def category_page(callback: CallbackQuery):
    _, category, page = callback.data.split(":", 2)
    await _show_category(callback, category, int(page))


async def _show_category(callback: CallbackQuery, category: str, page: int) -> None:
    specs = by_category().get(category, [])
    if not specs:
        await callback.answer("الفئة غير موجودة.", show_alert=True)
        return
    counts = {spec.key: await FeatureService.enabled(spec.key) for spec in specs}
    set_enabled_cache(counts)
    emoji = specs[0].emoji_category
    active = sum(1 for value in counts.values() if value)
    await callback.message.edit_text(
        f"{emoji} <b>{category}</b>\n\n"
        f"المفعّلة: {active} من {len(specs)}",
        reply_markup=features_in_category_kb(category, page),
    )
    await callback.answer()


# ══════════════ تفاصيل الإضافة ══════════════


@router.callback_query(F.data.startswith("feat_item:"))
async def feature_detail(callback: CallbackQuery):
    key = callback.data.split(":", 1)[1]
    spec = BY_KEY.get(key)
    if spec is None:
        await callback.answer("الإضافة غير مسجلة.", show_alert=True)
        return
    enabled = await FeatureService.enabled(key)
    config = await FeatureService.full_config(key)
    usage = (await FeatureService.usage_stats()).get(key, 0)

    config_text = "\n".join(
        f"• <code>{name}</code>: <b>{_short(value)}</b>" for name, value in config.items()
    )
    if not config_text:
        config_text = "لا إعدادات قابلة للتعديل لهذه الإضافة."

    await callback.message.edit_text(
        f"{spec.emoji_category} <b>{spec.name_ar}</b>\n"
        f"<code>{spec.key}</code>\n\n"
        f"{spec.desc_ar}\n\n"
        f"الحالة: {'🟢 مفعّلة' if enabled else '⚪ موقوفة'}\n"
        f"الاستخدام (30 يوم): <b>{usage}</b>\n"
        f"الفئة: {spec.category_ar}\n\n"
        "<b>الإعدادات:</b>\n"
        f"{config_text}",
        reply_markup=feature_detail_kb(spec, enabled),
    )
    await callback.answer()


def _short(value, limit: int = 60) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ══════════════ تفعيل / إيقاف ══════════════


@router.callback_query(F.data.startswith("feat_toggle:"))
async def feature_toggle(callback: CallbackQuery, session, db_user):
    key = callback.data.split(":", 1)[1]
    spec = BY_KEY.get(key)
    if spec is None:
        await callback.answer("الإضافة غير مسجلة.", show_alert=True)
        return
    current = await FeatureService.enabled(key)
    new_state = not current
    await FeatureService.set_enabled(session, key, new_state)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.ACTIVATE if new_state else AuditAction.DEACTIVATE,
        entity_type="feature",
        entity_name=spec.name_ar,
        old_value=str(current),
        new_value=str(new_state),
        description=f"{'تفعيل' if new_state else 'إيقاف'} الإضافة {key}",
        session=session,
    )
    await FeatureService.track(key, "toggled", user_id=db_user.id, value=str(new_state))
    await callback.answer("🟢 تم التفعيل." if new_state else "⚪ تم الإيقاف.")
    await feature_detail(callback)


@router.callback_query(F.data.startswith("feat_reset:"))
async def feature_reset(callback: CallbackQuery, session, db_user):
    key = callback.data.split(":", 1)[1]
    spec = BY_KEY.get(key)
    if spec is None:
        await callback.answer("الإضافة غير مسجلة.", show_alert=True)
        return
    await FeatureService.reset(session, key)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="feature",
        entity_name=spec.name_ar,
        description=f"إعادة الإضافة {key} للقيم الافتراضية",
        session=session,
    )
    await callback.answer("♻️ أُعيدت للقيم الافتراضية.")
    await feature_detail(callback)


# ══════════════ تعديل الإعدادات ══════════════


@router.callback_query(F.data.startswith("feat_opts:"))
async def feature_options(callback: CallbackQuery):
    key = callback.data.split(":", 1)[1]
    spec = BY_KEY.get(key)
    if spec is None:
        await callback.answer("الإضافة غير مسجلة.", show_alert=True)
        return
    if not spec.defaults:
        await callback.answer("لا إعدادات لهذه الإضافة.", show_alert=True)
        return
    config = await FeatureService.full_config(key)
    lines = "\n".join(f"• <code>{name}</code>: <b>{_short(value)}</b>" for name, value in config.items())
    await callback.message.edit_text(
        f"🎛 <b>إعدادات {spec.name_ar}</b>\n\n{lines}\n\nاختر إعداداً لتعديله:",
        reply_markup=feature_options_kb(spec),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("feat_opt:"))
async def feature_option_ask(callback: CallbackQuery, state: FSMContext):
    _, key, option = callback.data.split(":", 2)
    spec = BY_KEY.get(key)
    if spec is None or option not in spec.defaults:
        await callback.answer("الإعداد غير موجود.", show_alert=True)
        return
    current = await FeatureService.config(key, option)
    await state.set_state(AdminFeatureStates.waiting_option_value)
    await state.update_data(feature_key=key, option=option)
    await callback.message.answer(
        f"✏️ تعديل <code>{option}</code> في <b>{spec.name_ar}</b>\n\n"
        f"القيمة الحالية: <b>{_short(current)}</b>\n\n"
        "أرسل القيمة الجديدة (أو «إلغاء»):",
        reply_markup=back_to_features_kb(key),
    )
    await callback.answer()


@router.message(AdminFeatureStates.waiting_option_value)
async def feature_option_save(message: Message, state: FSMContext, session, db_user):
    data = await state.get_data()
    key = data.get("feature_key")
    option = data.get("option")
    spec = BY_KEY.get(key) if key else None

    text = (message.text or "").strip()
    if text in ("إلغاء", "cancel", "❌"):
        await state.clear()
        await message.answer("تم الإلغاء.")
        return

    if spec is None or option is None:
        await state.clear()
        await message.answer("⚠️ انتهت الجلسة، أعد المحاولة.")
        return

    old = await FeatureService.config(key, option)
    ok = await FeatureService.set_option(session, key, option, text)
    if not ok:
        await state.clear()
        await message.answer("⚠️ تعذّر حفظ القيمة.")
        return

    new = await FeatureService.config(key, option)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="feature_config",
        entity_name=f"{spec.name_ar} / {option}",
        old_value=str(old)[:500],
        new_value=str(new)[:500],
        description=f"تعديل إعداد {option} في {key}",
        session=session,
    )
    await state.clear()
    await FeatureService.track(key, "configured", user_id=db_user.id, value=option)
    await message.answer(f"✅ حُفظ: <code>{option}</code> = <b>{_short(new)}</b>")


# ══════════════ إعداد خصومات الجملة السريع ══════════════


def _bulk_discounts_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟢 خصومات خفيفة", callback_data="feat_bulk_preset:light")],
            [InlineKeyboardButton(text="🔥 خصومات تسويقية", callback_data="feat_bulk_preset:growth")],
            [InlineKeyboardButton(text="👑 خصومات تجار", callback_data="feat_bulk_preset:wholesale")],
            [InlineKeyboardButton(text="✍️ تعديل يدوي سهل", callback_data="feat_bulk_manual")],
            [InlineKeyboardButton(text="⬅️ رجوع", callback_data="feat_item:bulk_numbers")],
        ]
    )


async def _bulk_tiers_text() -> str:
    raw = await FeatureService.config("bulk_numbers", "discount_tiers_json", "[]")
    try:
        tiers = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        tiers = []
    lines = []
    for item in tiers or []:
        try:
            quantity, percent = int(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        lines.append(f"• من <b>{quantity}</b> رقم = خصم <b>{percent:g}%</b>")
    return "\n".join(lines) or "لا توجد شرائح خصم صالحة حالياً."


@router.callback_query(F.data == "feat_bulk_discounts")
async def bulk_discounts_panel(callback: CallbackQuery):
    text = (
        "📦 <b>إعداد خصومات الشراء بالجملة</b>\n\n"
        f"الشرائح الحالية:\n{await _bulk_tiers_text()}\n\n"
        "اختر قالباً جاهزاً أو عدّل الشرائح يدوياً."
    )
    await callback.message.edit_text(text, reply_markup=_bulk_discounts_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("feat_bulk_preset:"))
async def bulk_discounts_preset(callback: CallbackQuery, session, db_user):
    preset = callback.data.split(":", 1)[1]
    presets = {
        "light": [[10, 1], [50, 3], [100, 5], [500, 8]],
        "growth": [[5, 1], [10, 3], [25, 5], [50, 7], [100, 10]],
        "wholesale": [[10, 2], [50, 5], [100, 8], [250, 11], [500, 15]],
    }
    tiers = presets.get(preset)
    if tiers is None:
        await callback.answer("القالب غير معروف.", show_alert=True)
        return
    old = await FeatureService.config("bulk_numbers", "discount_tiers_json", "[]")
    value = json.dumps(tiers, separators=(",", ":"))
    await FeatureService.set_option(session, "bulk_numbers", "discount_tiers_json", value)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="feature_config",
        entity_name="خصومات الشراء بالجملة",
        old_value=str(old),
        new_value=value,
        description="تطبيق قالب خصومات جملة جاهز",
        session=session,
    )
    await callback.answer("✅ تم تطبيق القالب.")
    await bulk_discounts_panel(callback)


@router.callback_query(F.data == "feat_bulk_manual")
async def bulk_discounts_manual(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminBulkDiscountStates.waiting_tiers)
    await callback.message.answer(
        "✍️ أرسل الشرائح بصيغة سهلة، كل شريحة بسطر:\n\n"
        "<code>10=1\n50=3\n100=5</code>\n\n"
        "يعني: من 10 أرقام خصم 1%، من 50 خصم 3%.\n"
        "اكتب «إلغاء» للتراجع.",
        reply_markup=back_to_features_kb("bulk_numbers"),
    )
    await callback.answer()


@router.message(AdminBulkDiscountStates.waiting_tiers)
async def bulk_discounts_manual_save(message: Message, state: FSMContext, session, db_user):
    text = (message.text or "").strip()
    if text in ("إلغاء", "cancel", "❌"):
        await state.clear()
        await message.answer("تم الإلغاء.")
        return

    tiers: list[list[float | int]] = []
    errors = []
    for line in text.replace(",", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" in line:
            left, right = line.split("=", 1)
        elif ":" in line:
            left, right = line.split(":", 1)
        else:
            errors.append(line)
            continue
        try:
            quantity = int(left.strip())
            percent = float(right.strip().replace("%", ""))
        except ValueError:
            errors.append(line)
            continue
        if quantity <= 0 or percent < 0 or percent > 80:
            errors.append(line)
            continue
        tiers.append([quantity, percent])

    if not tiers or errors:
        await message.answer(
            "⚠️ توجد صيغة غير صحيحة. مثال صحيح:\n<code>10=1\n50=3\n100=5</code>"
        )
        return

    tiers.sort(key=lambda item: int(item[0]))
    old = await FeatureService.config("bulk_numbers", "discount_tiers_json", "[]")
    value = json.dumps(tiers, separators=(",", ":"))
    await FeatureService.set_option(session, "bulk_numbers", "discount_tiers_json", value)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="feature_config",
        entity_name="خصومات الشراء بالجملة",
        old_value=str(old),
        new_value=value,
        description="تعديل يدوي سهل لشرائح خصومات الجملة",
        session=session,
    )
    await state.clear()
    await message.answer("✅ تم حفظ شرائح خصومات الجملة.\n\n" + await _bulk_tiers_text())


# ══════════════ البحث ══════════════


@router.callback_query(F.data == "feat_search")
async def feature_search_ask(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminFeatureStates.waiting_search)
    await callback.message.answer(
        "🔍 أرسل اسم الإضافة أو مفتاحها (عربي أو إنجليزي):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="feat_home")]]
        ),
    )
    await callback.answer()


@router.message(AdminFeatureStates.waiting_search)
async def feature_search_run(message: Message, state: FSMContext):
    query = (message.text or "").strip().lower()
    await state.clear()
    if not query:
        await message.answer("لم تُرسل شيئاً.")
        return
    matches = [
        spec
        for spec in BY_KEY.values()
        if query in spec.name_ar.lower()
        or query in spec.name_en.lower()
        or query in spec.key.lower()
        or query in spec.desc_ar.lower()
    ]
    if not matches:
        await message.answer("لا نتائج مطابقة.")
        return
    rows = []
    for spec in matches[:MAX_RESULTS]:
        mark = "🟢" if await FeatureService.enabled(spec.key) else "⚪"
        rows.append(
            [InlineKeyboardButton(text=f"{mark} {spec.name_ar}", callback_data=f"feat_item:{spec.key}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="feat_home")])
    await message.answer(
        f"🔍 نتائج «{message.text.strip()}» — {len(matches)} إضافة:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ══════════════ الأكثر استعمالاً ══════════════


@router.callback_query(F.data == "feat_usage")
async def feature_usage(callback: CallbackQuery):
    usage = await FeatureService.usage_stats(30)
    ranked = sorted(usage.items(), key=lambda item: item[1], reverse=True)[:MAX_RESULTS]
    if not ranked:
        text = "لا استخدامات مسجلة بعد (آخر 30 يوماً)."
        rows: list[list[InlineKeyboardButton]] = []
    else:
        lines = []
        rows = []
        for key, count in ranked:
            spec = BY_KEY.get(key)
            name = spec.name_ar if spec else key
            mark = "🟢" if spec and await FeatureService.enabled(key) else "⚪"
            lines.append(f"{mark} <b>{name}</b> — {count} استخدام")
            if spec:
                rows.append(
                    [InlineKeyboardButton(text=name, callback_data=f"feat_item:{spec.key}")]
                )
        text = "📊 <b>الأكثر استعمالاً (30 يوماً)</b>\n\n" + "\n".join(lines)
        text += "\n\n💡 الإضافة صفر استخدام مرشّحة للإيقاف."
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="feat_home")])
    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await callback.answer()


# ══════════════ نظرة عامة على كل الفئات ══════════════


@router.message(F.text == "/features")
async def features_command(message: Message):
    counts = {spec.key: await FeatureService.enabled(spec.key) for spec in BY_KEY.values()}
    lines = []
    grouped = by_category()
    for name in categories_ordered():
        specs = grouped.get(name, [])
        if not specs:
            continue
        active = sum(1 for spec in specs if counts.get(spec.key))
        lines.append(f"{specs[0].emoji_category} {name}: {active}/{len(specs)}")
    await message.answer(
        "🧩 <b>حالة الإضافات</b>\n\n" + "\n".join(lines),
        reply_markup=admin_main_kb(),
    )
