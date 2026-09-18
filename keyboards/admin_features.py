"""
لوحات مفاتيح مركز التحكم بالإضافات.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.feature_registry import FeatureSpec, by_category, categories_ordered


def feature_categories_kb() -> InlineKeyboardMarkup:
    rows = []
    grouped = by_category()
    for name in categories_ordered():
        specs = grouped.get(name, [])
        if not specs:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{specs[0].emoji_category} {name} ({len(specs)})",
                    callback_data=f"feat_cat:{name}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🔍 بحث عن إضافة", callback_data="feat_search")])
    rows.append([InlineKeyboardButton(text="📊 الأكثر استعمالاً", callback_data="feat_usage")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def features_in_category_kb(category: str, page: int = 0) -> InlineKeyboardMarkup:
    per_page = 8
    specs = by_category().get(category, [])
    start = max(0, page) * per_page
    chunk = specs[start : start + per_page]
    rows = []
    for spec in chunk:
        mark = "🟢" if _enabled_cache.get(spec.key, spec.default_enabled) else "⚪"
        label = spec.name_ar if len(spec.name_ar) <= 30 else spec.name_ar[:29] + "…"
        rows.append(
            [InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"feat_item:{spec.key}")]
        )
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="◀️ السابق", callback_data=f"feat_catp:{category}:{page - 1}")
        )
    if start + per_page < len(specs):
        nav.append(
            InlineKeyboardButton(text="التالي ▶️", callback_data=f"feat_catp:{category}:{page + 1}")
        )
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ الفئات", callback_data="feat_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def feature_detail_kb(spec: FeatureSpec, enabled: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 إيقاف الإضافة" if enabled else "🟢 تفعيل الإضافة"
    rows = [[InlineKeyboardButton(text=toggle_text, callback_data=f"feat_toggle:{spec.key}")]]
    if spec.defaults:
        rows.append(
            [InlineKeyboardButton(text="🎛 تعديل الإعدادات", callback_data=f"feat_opts:{spec.key}")]
        )
    if spec.key == "bulk_numbers":
        rows.append(
            [InlineKeyboardButton(text="📦 إعداد خصومات الجملة", callback_data="feat_bulk_discounts")]
        )
    if spec.key == "daily_spin":
        rows.append(
            [InlineKeyboardButton(text="🎰 إدارة جوائز العجلة", callback_data="admin_engage:prizes")]
        )
    if spec.key == "deposit_bonuses":
        rows.append(
            [InlineKeyboardButton(text="🎁 إدارة قواعد المكافآت", callback_data="admin_engage:deposit_rules", style="primary")]
        )
    if spec.key == "weekly_challenges":
        rows.append(
            [InlineKeyboardButton(text="🏅 إنشاء تحدٍّ أسبوعي", callback_data="admin_engage:new_challenge")]
        )
    if spec.key == "number_resale_market":
        rows.append(
            [InlineKeyboardButton(text="🔄 إعدادات السوق", callback_data="admin_engage:resale_settings")]
        )
    rows.append(
        [InlineKeyboardButton(text="♻️ إعادة للقيم الافتراضية", callback_data=f"feat_reset:{spec.key}", style="danger")]
    )
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="feat_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def feature_options_kb(spec: FeatureSpec) -> InlineKeyboardMarkup:
    rows = []
    for option in spec.defaults:
        label = option if len(option) <= 28 else option[:27] + "…"
        rows.append(
            [InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"feat_opt:{spec.key}:{option}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"feat_item:{spec.key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_features_kb(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"feat_item:{key}")]]
    )


# كاش خفيف لحالة التفعيل حتى تُرسم الأيقونات بدون استعلام لكل زر.
_enabled_cache: dict[str, bool] = {}


def set_enabled_cache(values: dict[str, bool]) -> None:
    _enabled_cache.clear()
    _enabled_cache.update(values)
