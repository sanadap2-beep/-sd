"""
كل أزرار إدارة الأقسام الرئيسية والفرعية.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import Category, SubCategory, CategoryType


# ══════════════ قائمة الأقسام الرئيسية ══════════════


def categories_list_kb(
    categories: list[Category],
) -> InlineKeyboardMarkup:
    """قائمة كل الأقسام الرئيسية."""
    b = InlineKeyboardBuilder()

    for cat in categories:
        status_icon = "🟢" if cat.is_active else "🔴"
        subs_count = len(cat.sub_categories) if cat.sub_categories else 0
        b.button(
            text=(f"{status_icon} {cat.emoji} {cat.name_ar} ({subs_count})"),
            callback_data=f"admin:cat_view:{cat.id}",
        )

    b.button(
        text="➕ إضافة قسم رئيسي",
        callback_data="admin:cat_add",
    )
    b.button(
        text="🔙 رجوع",
        callback_data="admin:main",
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ اختيار نوع القسم ══════════════


def select_category_type_kb() -> InlineKeyboardMarkup:
    """اختيار نوع القسم للمتجر الكامل."""
    b = InlineKeyboardBuilder()

    labels = {
        CategoryType.SMM: "📈 قسم الرشق",
        CategoryType.GAMES: "🎮 قسم شحن الألعاب",
        CategoryType.APPS: "📱 قسم شحن التطبيقات",
        CategoryType.BALANCES: "💳 قسم الأرصدة",
        CategoryType.CARDS: "💳 قسم البطاقات والفيز",
        CategoryType.SUBSCRIPTIONS: "🔐 قسم الاشتراكات الرقمية",
        CategoryType.VERIFICATION: "✅ قسم توثيق الحسابات",
        CategoryType.CODES: "🎟 قسم الأكواد الرقمية",
        CategoryType.CUSTOM: "🧩 قسم مخصص",
    }
    for category_type, label in labels.items():
        b.button(text=label, callback_data=f"admin:cat_type:{category_type.value}")
    b.button(
        text="🔙 رجوع",
        callback_data="admin:categories",
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ اختيار الإيموجي ══════════════

COMMON_EMOJIS_SMM = [
    "📸",
    "📷",
    "📱",
    "🎵",
    "▶️",
    "👤",
    "🐦",
    "💬",
    "👻",
    "🔵",
    "🎮",
    "📈",
    "❤️",
    "👁",
    "👥",
]

COMMON_EMOJIS_GAMES = [
    "🎮",
    "🔫",
    "🔥",
    "⚔️",
    "🧱",
    "👑",
    "🏰",
    "🌟",
    "🏎",
    "⚽",
    "🎯",
    "🎲",
    "🎰",
    "🕹",
    "🎳",
]

COMMON_EMOJIS_APPS = [
    "📱",
    "💬",
    "🎵",
    "💎",
    "💜",
    "💛",
    "🌐",
    "💚",
    "🎁",
    "☎️",
    "📞",
    "📢",
    "🔔",
    "🎤",
    "🎧",
]


def select_emoji_kb(
    category_type: str = "smm",
) -> InlineKeyboardMarkup:
    """اختيار إيموجي للقسم."""
    b = InlineKeyboardBuilder()

    emojis_map = {
        "smm": COMMON_EMOJIS_SMM,
        "games": COMMON_EMOJIS_GAMES,
        "apps": COMMON_EMOJIS_APPS,
        "balances": ["💳", "💰", "🏦", "📲", "💵", "🪙", "⚡", "✅"],
        "cards": ["💳", "🎁", "🏷", "🛒", "💎", "🌐", "🔐", "✅"],
        "subscriptions": ["🔐", "🤖", "🎬", "🎧", "☁️", "🧰", "📦", "✅"],
        "verification": ["✅", "☑️", "🔵", "🛡", "📛", "👤", "🏢", "⭐"],
        "codes": ["🎟", "🔑", "🧾", "💌", "🎁", "📦", "⚡", "✅"],
        "custom": ["🧩", "📦", "🛍", "⭐", "🔥", "💎", "🚀", "✅"],
    }
    emojis = emojis_map.get(category_type, COMMON_EMOJIS_APPS)

    for emoji in emojis:
        b.button(
            text=emoji,
            callback_data=f"admin:cat_emoji:{emoji}",
        )

    b.button(
        text="✏️ إيموجي مخصص",
        callback_data="admin:cat_emoji:custom",
    )
    b.button(
        text="⏭ تخطي (استخدام افتراضي)",
        callback_data="admin:cat_emoji:default",
    )
    b.button(
        text="❌ إلغاء",
        callback_data="admin:categories",
    )

    b.adjust(5, 5, 5, 1, 1, 1)
    return b.as_markup()


# ══════════════ تفاصيل القسم الرئيسي ══════════════


def category_detail_kb(
    category: Category,
) -> InlineKeyboardMarkup:
    """أزرار تفاصيل القسم الرئيسي."""
    b = InlineKeyboardBuilder()

    if category.is_active:
        b.button(
            text="🔴 تعطيل القسم",
            callback_data=f"admin:cat_toggle:{category.id}",
        )
    else:
        b.button(
            text="🟢 تفعيل القسم",
            callback_data=f"admin:cat_toggle:{category.id}",
        )

    b.button(
        text="➕ أضفه كزر رئيسي",
        callback_data=f"mb:add_cat:{category.id}",
    )
    b.button(
        text="📂 عرض الأقسام الفرعية",
        callback_data=f"admin:subcat_list:{category.id}",
    )
    b.button(
        text="➕ إضافة قسم فرعي",
        callback_data=f"admin:subcat_add:{category.id}",
    )

    b.button(
        text="✏️ تعديل الاسم",
        callback_data=f"admin:cat_edit:name:{category.id}",
    )
    b.button(
        text="🎨 تعديل الإيموجي",
        callback_data=f"admin:cat_edit:emoji:{category.id}",
    )
    b.button(
        text="🔢 تعديل الترتيب",
        callback_data=f"admin:cat_edit:sort:{category.id}",
    )

    b.button(
        text="🗑 حذف القسم",
        callback_data=f"admin:cat_delete_confirm:{category.id}",
    )
    b.button(
        text="🔙 رجوع لقائمة الأقسام",
        callback_data="admin:categories",
    )

    b.adjust(1, 1, 2, 3, 1, 1)
    return b.as_markup()


# ══════════════ تأكيد حذف قسم ══════════════


def confirm_delete_category_kb(
    category_id: int,
    subs_count: int,
) -> InlineKeyboardMarkup:
    """تأكيد حذف قسم رئيسي."""
    b = InlineKeyboardBuilder()

    b.button(
        text="⚠️ نعم، احذف نهائياً",
        callback_data=f"admin:cat_delete:{category_id}",
    )
    b.button(
        text="🔙 لا، إلغاء",
        callback_data=f"admin:cat_view:{category_id}",
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ قائمة الأقسام الفرعية ══════════════


def sub_categories_list_kb(
    category_id: int,
    sub_categories: list[SubCategory],
) -> InlineKeyboardMarkup:
    """قائمة الأقسام الفرعية لقسم رئيسي."""
    b = InlineKeyboardBuilder()

    for sub in sub_categories:
        status_icon = "🟢" if sub.is_active else "🔴"
        products_count = len(sub.products) if sub.products else 0
        b.button(
            text=(f"{status_icon} {sub.emoji} {sub.name_ar} ({products_count} منتج)"),
            callback_data=f"admin:subcat_view:{sub.id}",
        )

    b.button(
        text="➕ إضافة قسم فرعي",
        callback_data=f"admin:subcat_add:{category_id}",
    )
    b.button(
        text="🔙 رجوع للقسم الرئيسي",
        callback_data=f"admin:cat_view:{category_id}",
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ تفاصيل القسم الفرعي ══════════════


def sub_category_detail_kb(
    sub_category: SubCategory,
    children: list[tuple[SubCategory, int]] | None = None,
) -> InlineKeyboardMarkup:
    """أزرار تفاصيل القسم الفرعي.

    ``children``: الأقسام الداخلية ``[(القسم، عدد منتجاته المفعلة), ...]``
    لتطبيق يحوي أقساماً داخلية (متابعون/لايكات/مشاهدات في قسم الرشق).
    """
    b = InlineKeyboardBuilder()

    if sub_category.is_active:
        b.button(
            text="🔴 تعطيل القسم الفرعي",
            callback_data=(f"admin:subcat_toggle:{sub_category.id}"),
        )
    else:
        b.button(
            text="🟢 تفعيل القسم الفرعي",
            callback_data=(f"admin:subcat_toggle:{sub_category.id}"),
        )

    for child, product_count in children or []:
        status_icon = "🟢" if child.is_active else "🔴"
        b.button(
            text=f"└ {status_icon} {child.emoji or ''} {child.name_ar} ({product_count})",
            callback_data=f"admin:subcat_view:{child.id}",
        )
    is_smm_app = (
        getattr(sub_category, "category", None) is not None
        and getattr(sub_category.category, "type", None) == CategoryType.SMM
    )
    has_children = bool(children)
    if is_smm_app and sub_category.parent_sub_category_id is None:
        # أزرار «عرض المنتجات / إضافة منتج» تظهر فقط للتطبيق بلا أقسام داخلية؛
        # وزر إضافة قسم داخلي متاح دائماً (حتى لإنشاء أول قسم داخل تطبيق فارغ).
        b.button(
            text="➕ إضافة قسم داخلي",
            callback_data=f"admin:subcat_add_child:{sub_category.id}",
        )

    b.button(
        text="➕ أضفه كزر رئيسي",
        callback_data=(f"mb:add_subcat:{sub_category.id}"),
    )
    if not has_children:
        b.button(
            text="📦 عرض المنتجات",
            callback_data=(f"admin:prod_list:{sub_category.id}"),
        )
        b.button(
            text="➕ إضافة منتج جديد",
            callback_data=(f"admin:prod_wizard_start:{sub_category.id}"),
        )

    b.button(
        text="✏️ تعديل الاسم",
        callback_data=(f"admin:subcat_edit:name:{sub_category.id}"),
    )
    b.button(
        text="🎨 تعديل الإيموجي",
        callback_data=(f"admin:subcat_edit:emoji:{sub_category.id}"),
    )
    b.button(
        text="📝 تعديل الوصف",
        callback_data=(f"admin:subcat_edit:desc:{sub_category.id}"),
    )
    b.button(
        text="🖼 تعديل الصورة",
        callback_data=(f"admin:subcat_edit:image:{sub_category.id}"),
    )
    b.button(
        text="🔢 تعديل الترتيب",
        callback_data=(f"admin:subcat_edit:sort:{sub_category.id}"),
    )

    b.button(
        text="🗑 حذف القسم الفرعي",
        callback_data=(f"admin:subcat_delete_confirm:{sub_category.id}"),
    )
    if sub_category.parent_sub_category_id is not None:
        b.button(
            text="🔙 رجوع للتطبيق",
            callback_data=(f"admin:subcat_view:{sub_category.parent_sub_category_id}"),
        )
    else:
        b.button(
            text="🔙 رجوع لقائمة الأقسام الفرعية",
            callback_data=(f"admin:subcat_list:{sub_category.category_id}"),
        )

    b.adjust(1)
    return b.as_markup()


# ══════════════ تأكيد حذف قسم فرعي ══════════════


def confirm_delete_sub_category_kb(
    sub_category_id: int,
    category_id: int,
    products_count: int,
) -> InlineKeyboardMarkup:
    """تأكيد حذف قسم فرعي."""
    b = InlineKeyboardBuilder()

    b.button(
        text="⚠️ نعم، احذف نهائياً",
        callback_data=(f"admin:subcat_delete:{sub_category_id}"),
    )
    b.button(
        text="🔙 لا، إلغاء",
        callback_data=(f"admin:subcat_view:{sub_category_id}"),
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ خيارات إضافة صورة ══════════════


def image_options_kb(
    context: str = "subcat",
    entity_id: int = 0,
) -> InlineKeyboardMarkup:
    """خيارات إضافة صورة (رفع / رابط / تخطي)."""
    b = InlineKeyboardBuilder()

    b.button(
        text="📤 رفع صورة",
        callback_data=f"admin:{context}_img:upload:{entity_id}",
    )
    b.button(
        text="🔗 إدخال رابط صورة",
        callback_data=f"admin:{context}_img:url:{entity_id}",
    )
    b.button(
        text="⏭ تخطي (بدون صورة)",
        callback_data=f"admin:{context}_img:skip:{entity_id}",
    )
    b.button(
        text="❌ إلغاء",
        callback_data=f"admin:subcat_list:{entity_id}",
    )
    b.adjust(1)
    return b.as_markup()


# ══════════════ إلغاء عمليات ══════════════


def cancel_add_kb(back_to: str) -> InlineKeyboardMarkup:
    """زر إلغاء عام أثناء الإدخال."""
    b = InlineKeyboardBuilder()
    b.button(
        text="❌ إلغاء",
        callback_data=back_to,
    )
    return b.as_markup()
