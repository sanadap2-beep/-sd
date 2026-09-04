"""Admin keyboards for dynamic main-menu buttons."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.main_button_service import MainButtonService, MainMenuButton


def main_buttons_kb(buttons: list[MainMenuButton]) -> InlineKeyboardMarkup:
    rows = []
    for button in buttons:
        mark = "🟢" if button.is_active else "⚪"
        label = button.label if len(button.label) <= 32 else button.label[:31] + "…"
        rows.append([
            InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"mb:view:{button.id}")
        ])
    rows.append([InlineKeyboardButton(text="➕ إضافة زر", callback_data="mb:add", style="success")])
    rows.append([InlineKeyboardButton(text="⚡ إضافة زر جاهز", callback_data="mb:presets", style="success")])
    rows.append([InlineKeyboardButton(text="🧪 فحص الأزرار المكسورة", callback_data="mb:check")])
    rows.append([InlineKeyboardButton(text="♻️ استعادة الافتراضي", callback_data="mb:reset", style="danger")])
    rows.append([InlineKeyboardButton(text="⬅️ لوحة الإدارة", callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_button_detail_kb(button_id: str, enabled: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="⚪ تعطيل" if enabled else "🟢 تفعيل",
                callback_data=f"mb:toggle:{button_id}",
            )
        ],
        [
            InlineKeyboardButton(text="⬆️ رفع", callback_data=f"mb:move:{button_id}:up"),
            InlineKeyboardButton(text="⬇️ تنزيل", callback_data=f"mb:move:{button_id}:down"),
        ],
        [InlineKeyboardButton(text="🗑 حذف", callback_data=f"mb:delete:{button_id}", style="danger")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main_buttons")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_button_presets_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, (label, _action) in MainButtonService.PRESETS.items():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"mb:preset:{key}", style="success")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main_buttons")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_button_target_types_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📂 قسم رئيسي", callback_data="mb:type:category")],
            [InlineKeyboardButton(text="📁 قسم فرعي", callback_data="mb:type:subcategory")],
            [InlineKeyboardButton(text="📦 منتج", callback_data="mb:type:product")],
            [InlineKeyboardButton(text="⚡ صفحة داخلية جاهزة", callback_data="mb:type:internal")],
            [InlineKeyboardButton(text="🌐 رابط خارجي", callback_data="mb:type:url")],
            [InlineKeyboardButton(text="✍️ كتابة الإجراء يدوياً", callback_data="mb:type:manual")],
            [InlineKeyboardButton(text="⬅️ إلغاء", callback_data="admin:main_buttons")],
        ]
    )


def main_button_action_help_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 المتجر الشامل", callback_data="mb:action:store:home")],
            [InlineKeyboardButton(text="🔥 عروض اليوم", callback_data="mb:action:store:section:deals")],
            [InlineKeyboardButton(text="🎮 ألعاب", callback_data="mb:action:store:section:games")],
            [InlineKeyboardButton(text="📈 سوشيال", callback_data="mb:action:store:section:smm")],
            [InlineKeyboardButton(text="📦 تطبيقات", callback_data="mb:action:store:section:apps")],
            [InlineKeyboardButton(text="⬅️ إلغاء", callback_data="admin:main_buttons")],
        ]
    )


def target_categories_kb(categories) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{cat.emoji} {cat.name_ar} · ID {cat.id}", callback_data=f"mb:pick:cat:{cat.id}")]
        for cat in categories[:40]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="mb:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def target_subcategories_kb(subcategories) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{sub.emoji} {sub.name_ar} · ID {sub.id}", callback_data=f"mb:pick:subcat:{sub.id}")]
        for sub in subcategories[:40]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="mb:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def target_products_kb(products) -> InlineKeyboardMarkup:
    rows = []
    for product in products[:40]:
        name = product.name_ar if len(product.name_ar) <= 34 else product.name_ar[:33] + "…"
        rows.append([
            InlineKeyboardButton(text=f"📦 {name} · ID {product.id}", callback_data=f"mb:pick:prod:{product.id}")
        ])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="mb:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
