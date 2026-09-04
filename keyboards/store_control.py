"""Keyboards for the admin «control the store» and «control extras» panels."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.store_section_service import StoreEntry


def store_control_home_kb(entries: list[StoreEntry]) -> InlineKeyboardMarkup:
    rows = []
    for entry in entries:
        mark = "🟢" if entry.is_active else "⚪"
        kind = "مخصص" if not entry.is_builtin else "ثابت"
        label = entry.label if len(entry.label) <= 30 else entry.label[:29] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {label} · {kind}",
                    callback_data=f"stc:view:{entry.key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ إضافة قسم للمتجر", callback_data="stc:add", style="success")])
    rows.append([InlineKeyboardButton(text="🗂 الأقسام الرئيسية (إدارة)", callback_data="admin:categories")])
    rows.append([InlineKeyboardButton(text="📱 خدمات الأرقام (إدارة)", callback_data="admin:number_services")])
    rows.append([InlineKeyboardButton(text="🧪 فحص الأزرار المكسورة", callback_data="stc:check")])
    rows.append([InlineKeyboardButton(text="♻️ استعادة الافتراضي", callback_data="stc:reset", style="danger")])
    rows.append([InlineKeyboardButton(text="⬅️ لوحة الإدارة", callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def store_entry_detail_kb(entry: StoreEntry) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="⚪ تعطيل من المتجر" if entry.is_active else "🟢 تفعيل في المتجر",
                callback_data=f"stc:toggle:{entry.key}",
            )
        ],
        [
            InlineKeyboardButton(text="⬆️ رفع", callback_data=f"stc:move:{entry.key}:up"),
            InlineKeyboardButton(text="⬇️ تنزيل", callback_data=f"stc:move:{entry.key}:down"),
        ],
    ]
    if not entry.is_builtin:
        rows.append([InlineKeyboardButton(text="🗑 حذف القسم", callback_data=f"stc:delete:{entry.key}", style="danger")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:store_control")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def store_section_target_types_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📂 قسم رئيسي", callback_data="stc:type:category")],
            [InlineKeyboardButton(text="📁 قسم فرعي", callback_data="stc:type:subcategory")],
            [InlineKeyboardButton(text="📦 منتج", callback_data="stc:type:product")],
            [InlineKeyboardButton(text="⚡ صفحة داخلية جاهزة", callback_data="stc:type:internal")],
            [InlineKeyboardButton(text="🌐 رابط خارجي", callback_data="stc:type:url")],
            [InlineKeyboardButton(text="✍️ كتابة الإجراء يدوياً", callback_data="stc:type:manual")],
            [InlineKeyboardButton(text="⬅️ إلغاء", callback_data="admin:store_control")],
        ]
    )


def store_section_action_help_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, label, action, _order in (
        ("numbers", "📱 الأرقام (القسم الموحد)", "num_hub", 0),
        ("offers", "🔥 العروض الخاصة 24", "special:home", 0),
        ("smart_featured", "⭐ مختارات المتجر", "store:section:featured", 0),
        ("smart_deals", "🔥 عروض اليوم", "store:section:deals", 0),
        ("smart_bestsellers", "🏆 الأكثر مبيعاً", "store:section:bestsellers", 0),
        ("smart_instant", "⚡ تسليم فوري", "store:section:instant", 0),
        ("smart_cheap", "💸 أقل من 2$", "store:section:cheap", 0),
        ("smart_games", "🎮 ألعاب", "store:section:games", 0),
        ("smart_smm", "📈 سوشيال ميديا", "store:section:smm", 0),
        ("smart_apps", "📦 تطبيقات واشتراكات", "store:section:apps", 0),
        ("search", "🔎 البحث عن خدمة", "menu:search", 0),
        ("cart", "🛒 السلة", "menu:cart", 0),
        ("request", "➕ اطلب منتج غير موجود", "menu:product_request", 0),
    ):
        rows.append([InlineKeyboardButton(text=label, callback_data=f"stc:action:{action}")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:store_control")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def target_categories_kb(categories) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{cat.emoji} {cat.name_ar} · ID {cat.id}", callback_data=f"stc:pick:cat:{cat.id}")]
        for cat in categories[:40]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="stc:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def target_subcategories_kb(subcategories) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{sub.emoji} {sub.name_ar} · ID {sub.id}", callback_data=f"stc:pick:subcat:{sub.id}")]
        for sub in subcategories[:40]
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="stc:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def target_products_kb(products) -> InlineKeyboardMarkup:
    rows = []
    for product in products[:40]:
        name = product.name_ar if len(product.name_ar) <= 34 else product.name_ar[:33] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📦 {name} · ID {product.id}",
                    callback_data=f"stc:pick:prod:{product.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="stc:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def extras_control_kb(entries) -> InlineKeyboardMarkup:
    rows = []
    for entry in entries:
        mark = "🟢" if entry.is_active else "⚪"
        note = " (الميزة موقوفة)" if entry.feature_key else ""
        label = entry.label if len(entry.label) <= 32 else entry.label[:31] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {label}{note}",
                    callback_data=f"xtc:toggle:{entry.key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🧩 مركز الإضافات", callback_data="admin:features")])
    rows.append([InlineKeyboardButton(text="⬅️ لوحة الإدارة", callback_data="admin:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
