"""
كل أزرار لوحة الأدمن.
"""

from aiogram.types import InlineKeyboardMarkup, WebAppInfo, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings


# ══════════════ اللوحة الرئيسية ══════════════


ADMIN_TABS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "finance": (
        "📊 المالية والطلبات",
        [
            ("📦 إدارة الطلبات", "admin:orders"),
            ("📞 طلبات الأرقام", "admin:number_orders"),
            ("💳 طلبات الشحن", "admin:deposits"),
            ("💸 طلبات السحب", "admin:withdrawals"),
            ("📒 جرد الحسابات", "admin:ledger"),
            ("📊 إحصائيات البوت", "admin:stats"),
        ],
    ),
    "store": (
        "🛍 المتجر والخدمات والرشق",
        [
            ("📥 الخدمات المسحوبة (مزود/بحث)", "admin:pulled_services"),
            ("🔌 مزودو المتجر", "admin:api_providers"),
            ("📂 إدارة الأقسام", "admin:categories"),
            ("🚀 منتجات قسم الرشق", "admin:smm_products"),
            ("📦 إدارة المنتجات", "admin:products_menu"),
            ("💵 تعديل الأسعار والهوامش", "admin:pricing"),
            ("📦 المخزون الرقمي", "admin:inventory"),
            ("⭐ إدارة باقات النجوم", "admin:stars"),
            ("📞 إدارة خدمات الأرقام", "admin:number_services"),
            ("🖥 السيرفرات العامة", "admin:store_servers"),
            ("🌐 مزودو الأرقام", "admin:providers"),
            ("🌍 إدارة الدول", "admin:countries"),
        ],
    ),
    "users": (
        "👥 المستخدمون والتسويق",
        [
            ("👥 إدارة المستخدمين", "admin:users"),
            ("💼 إدارة الوكلاء", "admin:agents"),
            ("🎟 إدارة الكوبونات", "admin:coupons"),
            ("📢 إدارة الإعلانات", "admin:ads"),
            ("📢 إذاعة جماعية", "admin:broadcast"),
            ("📌 الاشتراك الإجباري", "admin:channels"),
            ("📈 طلبات السوق", "admin:market_requests"),
            ("🎁 بطاقات الهدايا", "admin:gift_codes"),
            ("🔥 إدارة العروض", "admin:promotions"),
            ("🔥 قسم العروض", "admin:special_offers"),
            ("🔔 إدارة الإشعارات", "admin:notifications"),
            ("🎁 برنامج الولاء", "admin:loyalty"),
        ],
    ),
    "system": (
        "⚙️ النظام والعمليات",
        [
            ("🎛 مركز العمليات", "admin:ops"),
            ("🎯 مركز المهام", "admin:tasks_center"),
            ("🩺 صحة النظام", "admin:health"),
            ("🧩 مركز الإضافات", "admin:features"),
            ("⚙️ الإعدادات العامة", "admin:settings"),
            ("🔧 وضع الصيانة", "admin:maintenance"),
            ("🛍 التحكم بالمتجر", "admin:store_control"),
            ("🧩 التحكم بخدمات الأخرى", "admin:extras_control"),
            ("🎛 أزرار الواجهة", "admin:main_buttons"),
            ("📊 جودة مزودي الأرقام", "admin:number_provider_quality"),
            ("📡 مباشر البوت", "admin:live_feed"),
            ("👨‍💼 إدارة الأدمنية", "admin:multi_admin"),
            ("📜 سجل الإدارة", "admin:audit"),
            ("🎫 تذاكر الدعم", "admin:tickets"),
        ],
    ),
}


def admin_main_kb() -> InlineKeyboardMarkup:
    """Compact admin home: four tabs instead of a 40-button wall."""
    b = InlineKeyboardBuilder()
    b.button(text="🆕 آخر التحديثات والإضافات", callback_data="admin:changelog")
    b.button(text="📘 شرح البوت", callback_data="admin:guide")
    for key, (title, _items) in ADMIN_TABS.items():
        b.button(text=title, callback_data=f"admin:tab:{key}")
    if settings.ADMIN_WEBAPP_URL:
        b.button(
            text="🌐 لوحة الويب",
            web_app=WebAppInfo(url=settings.ADMIN_WEBAPP_URL),
        )
    # اختصارات مراقبة لا تعيد ازدحام اللوحة، لكنها تحفظ الوصول السريع
    # لأكثر شاشتين يحتاجهما الأدمن يومياً.
    b.button(text="📒 جرد الحسابات", callback_data="admin:ledger")
    b.button(text="📥 خدمات مسحوبة", callback_data="admin:pulled_services")
    b.adjust(2)
    return b.as_markup()


def admin_tab_kb(tab: str) -> InlineKeyboardMarkup:
    """Keyboard for one of the four admin tabs."""
    b = InlineKeyboardBuilder()
    _title, items = ADMIN_TABS.get(tab, ADMIN_TABS["finance"])
    for label, callback_data in items:
        b.button(text=label, callback_data=callback_data)
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(2, 2, 2, 2, 2, 2, 2, 1)
    return b.as_markup()


def admin_orders_kb(orders, page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    """قائمة الطلبات الموحدة للأدمن."""
    b = InlineKeyboardBuilder()
    for order in orders:
        product_name = order.product.name_ar[:24] if order.product else "منتج"
        status = order.status.value
        b.button(
            text=f"#{order.id} {product_name} · {status}",
            callback_data=f"admin:order_view:{order.id}", style="primary",
        )
    if page > 0:
        b.button(
            text="◀️ السابق",
            callback_data=f"admin:orders:{page - 1}",
        )
    if page < total_pages - 1:
        b.button(
            text="التالي ▶️",
            callback_data=f"admin:orders:{page + 1}",
        )
    b.button(text="🔙 اللوحة الرئيسية", callback_data="admin:main")
    b.adjust(1, 2, 1)
    return b.as_markup()


def admin_order_detail_kb(order) -> InlineKeyboardMarkup:
    """أزرار إدارة طلب موحد واحد."""
    b = InlineKeyboardBuilder()
    status = getattr(order.status, "value", order.status)
    if status in ("pending", "processing"):
        b.button(
            text="✅ تعليم كمكتمل",
            callback_data=f"admin:order_complete:{order.id}", style="primary",
        )
        b.button(
            text="↩️ استرجاع الرصيد",
            callback_data=f"admin:order_refund_ask:{order.id}", style="danger",
        )
    b.button(text="🔙 الطلبات", callback_data="admin:orders")
    b.adjust(2, 1)
    return b.as_markup()


def admin_deposits_kb(
    deposits,
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    """قائمة طلبات الشحن للأدمن."""
    b = InlineKeyboardBuilder()
    for deposit in deposits:
        status = getattr(deposit.status, "value", deposit.status)
        b.button(
            text=f"#{deposit.id} {deposit.amount_usd}$ · {status}",
            callback_data=f"admin:deposit_view:{deposit.id}", style="primary",
        )
    if page > 0:
        b.button(
            text="◀️ السابق",
            callback_data=f"admin:deposits:{page - 1}",
        )
    if page < total_pages - 1:
        b.button(
            text="التالي ▶️",
            callback_data=f"admin:deposits:{page + 1}",
        )
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(1, 2, 1)
    return b.as_markup()


def admin_deposit_view_kb(deposit_id: int, pending: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if pending:
        b.button(text="✅ قبول وإضافة الرصيد", callback_data=f"deposit_accept:{deposit_id}", style="primary")
        b.button(text="❌ رفض الطلب", callback_data=f"deposit_reject:{deposit_id}", style="danger")
    b.button(text="🔙 طلبات الشحن", callback_data="admin:deposits")
    b.adjust(2, 1)
    return b.as_markup()


def admin_number_orders_kb(
    orders,
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    """قائمة طلبات أرقام SMS للأدمن."""
    b = InlineKeyboardBuilder()
    for order in orders:
        b.button(
            text=f"#{order.id} {order.phone_number} · {order.status.value}",
            callback_data=f"admin:num_order_view:{order.id}", style="primary",
        )
    if page > 0:
        b.button(
            text="◀️ السابق",
            callback_data=f"admin:number_orders:{page - 1}",
        )
    if page < total_pages - 1:
        b.button(
            text="التالي ▶️",
            callback_data=f"admin:number_orders:{page + 1}",
        )
    b.button(text="🔙 اللوحة الرئيسية", callback_data="admin:main")
    b.adjust(1, 2, 1)
    return b.as_markup()


def admin_order_refund_confirm_kb(order_id: int) -> InlineKeyboardMarkup:
    """تأكيد استرجاع طلب موحد."""
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ نعم، استرجع الرصيد",
        callback_data=f"admin:order_refund:{order_id}", style="danger",
    )
    b.button(
        text="❌ إلغاء",
        callback_data=f"admin:order_view:{order_id}", style="primary",
    )
    b.adjust(1)
    return b.as_markup()


def admin_number_order_detail_kb(order) -> InlineKeyboardMarkup:
    """أزرار إدارة طلب رقم واحد."""
    b = InlineKeyboardBuilder()
    status = getattr(order.status, "value", order.status)
    if status == "pending":
        b.button(
            text="❌ إلغاء واسترجاع الرصيد",
            callback_data=f"admin:num_order_refund_ask:{order.id}", style="danger",
        )
    b.button(text="🔙 طلبات الأرقام", callback_data="admin:number_orders")
    b.button(text="🏠 اللوحة الرئيسية", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_number_order_refund_confirm_kb(order_id: int) -> InlineKeyboardMarkup:
    """تأكيد استرجاع طلب رقم."""
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ نعم، استرجع الرصيد",
        callback_data=f"admin:num_order_refund:{order_id}", style="danger",
    )
    b.button(
        text="❌ إلغاء",
        callback_data=f"admin:num_order_view:{order_id}", style="primary",
    )
    b.adjust(1)
    return b.as_markup()


def admin_inventory_kb(products) -> InlineKeyboardMarkup:
    """قائمة المنتجات التي تعتمد على المخزون الرقمي."""
    b = InlineKeyboardBuilder()
    for product, count in products:
        b.button(
            text=f"📦 {product.name_ar[:30]} · متاح: {count}",
            callback_data=f"admin:inv_product:{product.id}",
        )
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_inventory_detail_kb(product_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ إضافة كود/ترخيص", callback_data=f"admin:inv_add:{product_id}", style="success")
    b.button(text="📋 العناصر المتاحة", callback_data=f"admin:inv_items:{product_id}")
    b.button(text="🔙 المخزون", callback_data="admin:inventory")
    b.adjust(1)
    return b.as_markup()


def admin_inventory_items_kb(items, product_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for item in items:
        b.button(
            text=f"🗑 إلغاء العنصر #{item.id}",
            callback_data=f"admin:inv_void:{item.id}:{product_id}", style="danger",
        )
    b.button(
        text="➕ إضافة عنصر",
        callback_data=f"admin:inv_add:{product_id}", style="success",
    )
    b.button(text="🔙 تفاصيل المنتج", callback_data=f"admin:inv_product:{product_id}")
    b.adjust(1)
    return b.as_markup()


def admin_health_kb() -> InlineKeyboardMarkup:
    """أزرار مراقبة صحة النظام."""
    b = InlineKeyboardBuilder()
    b.button(text="🔄 تحديث الفحص", callback_data="admin:health:refresh")
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_audit_kb(page: int = 0, has_next: bool = False) -> InlineKeyboardMarkup:
    """أزرار سجل تعديلات الإدارة."""
    b = InlineKeyboardBuilder()
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"admin:audit:{page - 1}")
    if has_next:
        b.button(text="التالي ▶️", callback_data=f"admin:audit:{page + 1}")
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(2, 1)
    return b.as_markup()


def admin_back_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 رجوع للوحة الرئيسية", callback_data="admin:main")
    return b.as_markup()


# ══════════════ الصيانة ══════════════


def admin_maintenance_kb(is_active: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if is_active:
        b.button(text="🟢 إيقاف الصيانة", callback_data="admin:maintenance_off")
    else:
        b.button(text="🔴 تفعيل الصيانة", callback_data="admin:maintenance_on", style="danger")
    b.button(text="📝 تعديل رسالة الصيانة", callback_data="admin:maintenance_msg")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الأقسام ══════════════


def admin_categories_list_kb(categories) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cat in categories:
        status = "🟢" if cat.is_active else "⚪"
        b.button(
            text=f"{status} {cat.emoji} {cat.name_ar}",
            callback_data=f"admin:cat_view:{cat.id}",
        )
    b.button(text="➕ إضافة قسم جديد", callback_data="admin:cat_add", style="success")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_category_detail_kb(category) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if category.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:cat_toggle:{category.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:cat_toggle:{category.id}")
    b.button(text="📝 تعديل الاسم", callback_data=f"admin:cat_edit_name:{category.id}")
    b.button(text="🔢 تعديل الترتيب", callback_data=f"admin:cat_edit_sort:{category.id}")
    b.button(text="📂 الأقسام الفرعية", callback_data=f"admin:subcats:{category.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:cat_delete:{category.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data="admin:categories")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الأقسام الفرعية ══════════════


def admin_subcats_list_kb(category_id: int, sub_categories) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for sub in sub_categories:
        status = "🟢" if sub.is_active else "⚪"
        b.button(
            text=f"{status} {sub.emoji} {sub.name_ar}",
            callback_data=f"admin:subcat_view:{sub.id}",
        )
    b.button(text="➕ إضافة قسم فرعي", callback_data=f"admin:subcat_add:{category_id}", style="success")
    b.button(text="🔙 رجوع", callback_data=f"admin:cat_view:{category_id}")
    b.adjust(1)
    return b.as_markup()


def admin_subcat_detail_kb(sub_category, category_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if sub_category.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:subcat_toggle:{sub_category.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:subcat_toggle:{sub_category.id}")
    b.button(text="📝 تعديل الاسم", callback_data=f"admin:subcat_edit_name:{sub_category.id}")
    b.button(text="📦 المنتجات", callback_data=f"admin:prods:{sub_category.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:subcat_delete:{sub_category.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data=f"admin:subcats:{category_id}")
    b.adjust(1)
    return b.as_markup()


# ══════════════ المنتجات ══════════════


def admin_products_list_kb(sub_category_id: int, products) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in products:
        status = "🟢" if p.status.value == "active" else "⚪"
        b.button(
            text=f"{status} {p.name_ar} ({p.price_usd}$)",
            callback_data=f"admin:prod_view:{p.id}",
        )
    b.button(text="➕ إضافة منتج", callback_data=f"admin:prod_add:{sub_category_id}", style="success")
    b.button(text="🔙 رجوع", callback_data=f"admin:subcat_view:{sub_category_id}")
    b.adjust(1)
    return b.as_markup()


def admin_product_detail_kb(product, sub_category_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    is_active = product.status.value == "active"
    if is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:prod_toggle:{product.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:prod_toggle:{product.id}")
    b.button(text="➕ أضفه كزر رئيسي", callback_data=f"mb:add_prod:{product.id}", style="success")
    b.button(text="🧪 فحص جاهزية المنتج", callback_data=f"admin:prod_ready:{product.id}")
    b.button(text="💰 تعديل السعر", callback_data=f"admin:prod_edit_price:{product.id}")
    b.button(text="💵 هامش ربح المنتج (%)", callback_data=f"admin:prod_margin:{product.id}", style="primary")
    b.button(text="📝 شرح/وصف الخدمة", callback_data=f"admin:prod_edit_desc:{product.id}")
    b.button(text="✏️ تعديل الاسم", callback_data=f"admin:prod_edit_name:{product.id}")
    b.button(text="🔌 تعديل آيدي المزود", callback_data=f"admin:prod_edit_svc_id:{product.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:prod_delete:{product.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data=f"admin:prods:{sub_category_id}")
    b.adjust(1)
    return b.as_markup()


# ══════════════ مزودو API ══════════════


def admin_api_providers_kb(providers) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for p in providers:
        status = "🟢" if p.is_active else "🔴"
        b.button(
            text=f"{status} {p.name} ({p.type.value})",
            callback_data=f"admin:aprov_view:{p.id}",
        )
    b.button(text="➕ إضافة مزود", callback_data="admin:aprov_add", style="success")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_api_provider_detail_kb(provider) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if provider.is_active:
        b.button(text="🔴 تعطيل", callback_data=f"admin:aprov_toggle:{provider.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:aprov_toggle:{provider.id}")
    b.button(text="💰 فحص الرصيد", callback_data=f"admin:aprov_balance:{provider.id}", style="primary")
    b.button(text="📝 تعديل الاسم", callback_data=f"admin:aprov_edit_name:{provider.id}")
    b.button(text="🔑 تعديل API Key", callback_data=f"admin:aprov_edit_key:{provider.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:aprov_delete:{provider.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data="admin:api_providers")
    b.adjust(1)
    return b.as_markup()


# ══════════════ باقات النجوم ══════════════


def admin_stars_kb(packages) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for pkg in packages:
        status = "🟢" if pkg.is_active else "⚪"
        b.button(
            text=f"{status} {pkg.label} = {pkg.usd_amount}$",
            callback_data=f"admin:star_view:{pkg.id}",
        )
    b.button(text="➕ إضافة باقة", callback_data="admin:star_add", style="success")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_star_detail_kb(package) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if package.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:star_toggle:{package.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:star_toggle:{package.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:star_delete:{package.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data="admin:stars")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الكوبونات ══════════════


def admin_coupons_kb(coupons) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in coupons:
        status = "🟢" if c.is_active else "⚪"
        b.button(
            text=f"{status} {c.code} ({c.used_count}/{c.max_uses})",
            callback_data=f"admin:coupon_view:{c.id}", style="primary",
        )
    b.button(text="➕ إنشاء كوبون", callback_data="admin:coupon_add", style="primary")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_coupon_detail_kb(coupon) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if coupon.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:coupon_toggle:{coupon.id}", style="primary")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:coupon_toggle:{coupon.id}", style="primary")
    b.button(text="🗑 حذف", callback_data=f"admin:coupon_delete:{coupon.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data="admin:coupons")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الأدمنية ══════════════


def admin_multi_admin_kb(admins) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for admin in admins:
        b.button(
            text=f"👤 {admin.full_name or admin.telegram_id} (@{admin.username or '-'})",
            callback_data=f"admin:madmin_view:{admin.id}",
        )
    b.button(text="➕ إضافة أدمن", callback_data="admin:madmin_add", style="success")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_madmin_detail_kb(admin_user, is_primary: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if not is_primary:
        b.button(text="🗑 إزالة الأدمنية", callback_data=f"admin:madmin_remove:{admin_user.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data="admin:multi_admin")
    b.adjust(1)
    return b.as_markup()


# ══════════════ خدمات الأرقام ══════════════


def admin_number_services_kb(services) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for svc in services:
        status = "🟢" if svc.is_active else "⚪"
        b.button(
            text=f"{status} {svc.emoji} {svc.name_ar}",
            callback_data=f"admin:nsvc_view:{svc.id}",
        )
    b.button(text="➕ إضافة خدمة أرقام", callback_data="admin:nsvc_add", style="success")
    b.button(text="📡 قناة التوفر المتقطع", callback_data="admin:nsvc_avail")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_nsvc_avail_kb(
    rotate_stable: bool = True,
    auto_repost: bool = True,
    restock_push: bool = False,
) -> InlineKeyboardMarkup:
    """أزرار ضبط قناة التوفر المتقطع."""
    b = InlineKeyboardBuilder()
    b.button(text="📡 ضبط قناة التوفر", callback_data="admin:nsvc_avail_channel")
    b.button(text="🔢 عدد الدول المعروضة", callback_data="admin:nsvc_avail_topn")
    b.button(text="🌍 الدول النادرة المراقبة", callback_data="admin:nsvc_avail_watchlist")
    b.button(text="📱 اختيار الخدمة", callback_data="admin:nsvc_avail_services")
    b.button(
        text=("🔀 الترتيب الدوّار: مفعّل" if rotate_stable else "⏸ الترتيب الدوّار: معطّل"),
        callback_data="admin:nsvc_avail_rotate",
    )
    b.button(
        text=("🔔 إعادة النشر التلقائي: مفعّل" if auto_repost else "🔕 إعادة النشر التلقائي: معطّل"),
        callback_data="admin:nsvc_avail_autorepost",
    )
    b.button(
        text="⏱ كل كم دورة إعادة النشر",
        callback_data="admin:nsvc_avail_repostevery",
    )
    b.button(
        text=("🔥 إشعار فوري عند الرجوع: مفعّل" if restock_push else "🔥 إشعار فوري عند الرجوع: معطّل"),
        callback_data="admin:nsvc_avail_restockpush",
    )
    b.button(text="🚀 تحديث اللوحة الآن", callback_data="admin:nsvc_avail_post", style="success")
    b.button(text="🔝 إعادة نشرها كرسالة جديدة", callback_data="admin:nsvc_avail_repost", style="success")
    b.button(text="♻️ مسح حالة المقارنة", callback_data="admin:nsvc_avail_reset", style="danger")
    b.button(text="🧩 مركز الإضافات (تفعيل/إيقاف + النص)", callback_data="admin:features")
    b.button(text="🔙 رجوع", callback_data="admin:number_services")
    b.adjust(1)
    return b.as_markup()


def admin_nsvc_avail_services_kb(services) -> InlineKeyboardMarkup:
    """اختيار خدمة الأرقام التي ستُبنى لها اللوحة."""
    b = InlineKeyboardBuilder()
    for svc in services:
        b.button(
            text=f"{svc.emoji} {svc.name_ar}",
            callback_data=f"admin:nsvc_avail_svc:{svc.code}",
        )
    b.button(text="🔙 رجوع", callback_data="admin:nsvc_avail")
    b.adjust(1)
    return b.as_markup()


def admin_nsvc_detail_kb(service) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if service.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:nsvc_toggle:{service.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:nsvc_toggle:{service.id}")
    b.button(
        text="⚙️ السيرفرات/المزودين التابعين",
        callback_data=f"admin:nsvc_servers:{service.id}",
        style="primary",
    )
    b.button(text="📝 تعديل الاسم", callback_data=f"admin:nsvc_edit_name:{service.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:nsvc_delete:{service.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data="admin:number_services")
    b.adjust(1)
    return b.as_markup()


def admin_nsvc_servers_kb(service_id: int, servers) -> InlineKeyboardMarkup:
    """قائمة سيرفرات خدمة أرقام (كل سيرفر = مزود مستقل)."""
    b = InlineKeyboardBuilder()
    for server in servers:
        status = "🟢" if server.is_active else "⚪"
        b.button(
            text=f"{status} {server.emoji} {server.name_ar}",
            callback_data=f"admin:nsvc_server:{server.id}",
        )
    b.button(text="➕ إضافة سيرفر", callback_data=f"admin:nsvc_server_add:{service_id}", style="success")
    b.button(
        text="🤖 إنشاء سيرفر لكل مزود مضبوط تلقائياً",
        callback_data=f"admin:nsvc_server_auto:{service_id}",
        style="success",
    )
    b.button(text="🔙 رجوع", callback_data=f"admin:nsvc_view:{service_id}")
    b.adjust(1)
    return b.as_markup()


def admin_nsvc_server_detail_kb(service_id: int, server) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if server.is_active:
        b.button(text="⚪ تعطيل السيرفر", callback_data=f"admin:nsvc_server_toggle:{server.id}")
    else:
        b.button(text="🟢 تفعيل السيرفر", callback_data=f"admin:nsvc_server_toggle:{server.id}")
    b.button(text="📝 تعديل الاسم", callback_data=f"admin:nsvc_server_edit_name:{server.id}")
    b.button(text="🎨 تعديل الإيموجي", callback_data=f"admin:nsvc_server_edit_emoji:{server.id}")
    b.button(text="🔁 تغيير المزود", callback_data=f"admin:nsvc_server_edit_provider:{server.id}")
    b.button(text="💰 نسبة الربح", callback_data=f"admin:nsvc_server_edit_margin:{server.id}", style="primary")
    b.button(text="🗑 حذف السيرفر", callback_data=f"admin:nsvc_server_delete:{server.id}", style="danger")
    b.button(text="🔙 السيرفرات", callback_data=f"admin:nsvc_servers:{service_id}")
    b.adjust(1)
    return b.as_markup()


def admin_nsvc_choose_provider_kb(service_id: int, server_id: int | None = None, show_back: bool = True) -> InlineKeyboardMarkup:
    """اختيار المزود المرتبط بالسيرفر."""
    from database.models import ProviderName

    b = InlineKeyboardBuilder()
    for provider in ProviderName:
        b.button(text=f"{provider.value}", callback_data=f"admin:nsvc_server_provider:{server_id or 0}:{provider.value}")
    if show_back:
        back = f"admin:nsvc_server:{server_id}" if server_id else f"admin:nsvc_servers:{service_id}"
        b.button(text="🔙 رجوع", callback_data=back)
    b.adjust(2)
    return b.as_markup()


# ══════════════ السيرفرات العامة (كل الأقسام) ══════════════

SCOPE_LABELS = {
    "category": "📂 قسم رئيسي",
    "subcategory": "🗂 قسم فرعي",
    "number_service": "📞 خدمة أرقام",
    "global": "🌐 عام (كل الأقسام)",
}

# أقصى عدد أزرار لكل صفحة في اختيار الهدف — يبقيه أقل بكثير من حد تليجرام
# (100 زر كحد أقصى للوحة كاملة) لتفادي «reply markup is too long».
SSVC_TARGETS_PER_PAGE = 40

# أقصى عدد سيرفرات لكل صفحة في قائمة السيرفرات العامة (نفس السبب).
STORE_SERVERS_PER_PAGE = 30

# أقصى طول لنص الزر — الأسماء الطويلة جداً تضخّم الـ reply markup
# وقد تتجاوز حد تيليجرام حتى مع عدد أزرار صغير.
_MAX_BUTTON_TEXT = 48


def _clip_label(text: str, limit: int = _MAX_BUTTON_TEXT) -> str:
    """يقصّ نص الزر الطويل حتى لا يتضخم الـ reply markup فوق حد تيليجرام."""
    text = " ".join(str(text).split())  # توحيد الأسطر والمسافات
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def admin_store_servers_kb(servers, scope_counts=None, page: int = 0) -> InlineKeyboardMarkup:
    """قائمة كل السيرفرات العامة مع عددها حسب النطاق (مع ترقيم صفحات).

    الترقيم ضروري: عند إنشاء سيرفر لكل قسم فرعي مثلاً يتجاوز العدد
    حد أزرار تيليجرام (100 زر) فتفشل الرسالة بخطأ
    «Bad Request: reply markup is too long».
    """
    b = InlineKeyboardBuilder()
    total = len(servers)
    total_pages = max(1, (total + STORE_SERVERS_PER_PAGE - 1) // STORE_SERVERS_PER_PAGE)
    page = max(0, min(int(page), total_pages - 1))
    start = page * STORE_SERVERS_PER_PAGE
    for server in servers[start : start + STORE_SERVERS_PER_PAGE]:
        status = "🟢" if server.is_active else "⚪"
        kind = "🔌" if server.provider_kind == "api" else "📱"
        b.button(
            text=_clip_label(f"{status} {kind} {server.emoji} {server.name_ar}"),
            callback_data=f"admin:ssvc_server:{server.id}",
        )
    rows = [1] * min(len(servers) - start, STORE_SERVERS_PER_PAGE)
    nav = []
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"admin:store_servers:p:{page - 1}")
        nav.append(1)
    if page < total_pages - 1:
        b.button(text="التالي ▶️", callback_data=f"admin:store_servers:p:{page + 1}")
        nav.append(1)
    if nav:
        rows.append(len(nav))
    b.button(
        text="➕ إضافة سيرفر عام",
        callback_data="admin:ssvc_add",
        style="success",
    )
    rows.append(1)
    b.button(text="🔙 رجوع", callback_data="admin:main")
    rows.append(1)
    b.adjust(*rows)
    return b.as_markup()


def admin_store_server_detail_kb(server) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if server.is_active:
        b.button(text="⚪ تعطيل السيرفر", callback_data=f"admin:ssvc_toggle:{server.id}")
    else:
        b.button(text="🟢 تفعيل السيرفر", callback_data=f"admin:ssvc_toggle:{server.id}")
    b.button(text="📝 تعديل الاسم", callback_data=f"admin:ssvc_edit_name:{server.id}")
    b.button(text="🎨 تعديل الإيموجي", callback_data=f"admin:ssvc_edit_emoji:{server.id}")
    b.button(text="💰 تعديل نسبة الربح", callback_data=f"admin:ssvc_edit_margin:{server.id}", style="primary")
    b.button(text="🩺 تعديل الوصف", callback_data=f"admin:ssvc_edit_desc:{server.id}")
    b.button(text="🗑 حذف السيرفر", callback_data=f"admin:ssvc_delete:{server.id}", style="danger")
    b.button(text="🔙 كل السيرفرات", callback_data="admin:store_servers")
    b.adjust(1)
    return b.as_markup()


def admin_ssvc_scope_kb() -> InlineKeyboardMarkup:
    """اختيار نطاق السيرفر: أي قسم سيُربط به.

    «خدمة الأرقام» معروضة هنا أيضاً، فيستطيع الأدمن ربط سيرفر عام
    بخدمة أرقام محددة (بالإضافة إلى نظام سيرفرات الأرقام المخصص).
    """
    b = InlineKeyboardBuilder()
    for key in ("category", "subcategory", "number_service", "global"):
        b.button(text=SCOPE_LABELS[key], callback_data=f"admin:ssvc_scope:{key}", style="success")
    b.button(text="🔙 رجوع", callback_data="admin:store_servers")
    b.adjust(1)
    return b.as_markup()


def admin_ssvc_target_kb(scope: str, targets, page: int = 0) -> InlineKeyboardMarkup:
    """اختيار القسم المستهدف من القائمة المحددة (مع ترقيم صفحات).

    ترقيم الصفحات ضروري لأن عدد الأقسام الفرعية مثلاً قد يكون كبيراً
    فيتجاوز حد أزرار تليجرام وتظهر رسالة «reply markup is too long».
    """
    b = InlineKeyboardBuilder()
    total = len(targets)
    total_pages = max(1, (total + SSVC_TARGETS_PER_PAGE - 1) // SSVC_TARGETS_PER_PAGE)
    page = max(0, min(int(page), total_pages - 1))
    start = page * SSVC_TARGETS_PER_PAGE
    chunk = targets[start : start + SSVC_TARGETS_PER_PAGE]
    for target in chunk:
        emoji = getattr(target, "emoji", "📦")
        name = getattr(target, "name_ar", str(target))
        b.button(
            text=_clip_label(f"{emoji} {name}"),
            callback_data=f"admin:ssvc_target:{scope}:{target.id}",
            style="success",
        )
    rows = [1] * len(chunk)
    nav = []
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"admin:ssvc_scope:{scope}:{page - 1}")
        nav.append(1)
    if page < total_pages - 1:
        b.button(text="التالي ▶️", callback_data=f"admin:ssvc_scope:{scope}:{page + 1}")
        nav.append(1)
    if nav:
        rows.append(len(nav))
    b.button(text="🔙 النطاق", callback_data="admin:ssvc_add")
    rows.append(1)
    b.adjust(*rows)
    return b.as_markup()


def admin_ssvc_auto_target_kb(scope: str, targets) -> InlineKeyboardMarkup:
    """إنشاء سيرفر تلقائي لكل قسم في النطاق المختار."""
    b = InlineKeyboardBuilder()
    for target in targets:
        emoji = getattr(target, "emoji", "📦")
        name = getattr(target, "name_ar", str(target))
        b.button(
            text=f"{emoji} {name}",
            callback_data=f"admin:ssvc_auto:{scope}:{target.id}",
            style="success",
        )
    b.button(text="🔙 رجوع", callback_data="admin:store_servers")
    b.adjust(1)
    return b.as_markup()


def admin_ssvc_provider_kind_kb() -> InlineKeyboardMarkup:
    """نوع المزود: API (متجر/رشق/ألعاب) أو رقم."""
    b = InlineKeyboardBuilder()
    b.button(text="🔌 مزود متجر/رشق/ألعاب", callback_data="admin:ssvc_provider_kind:api", style="success")
    b.button(text="📱 مزود أرقام", callback_data="admin:ssvc_provider_kind:number", style="success")
    b.button(text="🔙 رجوع", callback_data="admin:ssvc_edit")
    b.adjust(1)
    return b.as_markup()


def admin_ssvc_api_provider_kb(providers) -> InlineKeyboardMarkup:
    """اختيار مزود API مرتبط بالسيرفر."""
    b = InlineKeyboardBuilder()
    for provider in providers:
        status = "🟢" if provider.is_active else "⚪"
        b.button(
            text=f"{status} {provider.name}",
            callback_data=f"admin:ssvc_api_provider:{provider.id}",
            style="success",
        )
    b.button(text="🔙 رجوع", callback_data="admin:ssvc_provider_kind:api")
    b.adjust(1)
    return b.as_markup()


def admin_ssvc_number_provider_kb() -> InlineKeyboardMarkup:
    """اختيار مزود أرقام مرتبط بالسيرفر."""
    from database.models import ProviderName

    b = InlineKeyboardBuilder()
    for provider in ProviderName:
        b.button(
            text=f"📱 {provider.value}",
            callback_data=f"admin:ssvc_number_provider:{provider.value}",
            style="success",
        )
    b.button(text="🔙 رجوع", callback_data="admin:ssvc_provider_kind:number")
    b.adjust(2)
    return b.as_markup()


# ══════════════ الدول ══════════════

ADMIN_COUNTRIES_PER_PAGE = 20


def admin_countries_kb(countries, page: int = 0) -> InlineKeyboardMarkup:
    """قائمة الدول مع ترقيم صفحات وأزرار الإدارة ظاهرة دائماً."""
    b = InlineKeyboardBuilder()

    total_pages = max(
        1, (len(countries) + ADMIN_COUNTRIES_PER_PAGE - 1) // ADMIN_COUNTRIES_PER_PAGE
    )
    page = max(0, min(page, total_pages - 1))

    start = page * ADMIN_COUNTRIES_PER_PAGE
    page_countries = countries[start : start + ADMIN_COUNTRIES_PER_PAGE]

    for c in page_countries:
        status_icon = "🟢" if c.is_active else "⚪"
        b.button(
            text=f"{status_icon} {c.flag} {c.name_ar}",
            callback_data=f"admin:country_view:{c.id}",
        )

    nav_buttons = []
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"admin:countries:{page - 1}")
        nav_buttons.append(1)
    if page < total_pages - 1:
        b.button(text="التالي ▶️", callback_data=f"admin:countries:{page + 1}")
        nav_buttons.append(1)

    b.button(text="➕ إضافة دولة جديدة", callback_data="admin:country_add", style="success")
    b.button(text="🔄 سحب دول من HeroSMS", callback_data="admin:country_sync_herosms")
    b.button(text="🗑 حذف جميع الدول", callback_data="admin:country_delete_all_confirm", style="danger")
    b.button(text="📋 أكواد 5sim المرجعية", callback_data="admin:country_reference_list")
    b.button(text="🔙 رجوع", callback_data="admin:main")

    rows = [2] * ((len(page_countries) + 1) // 2)
    if nav_buttons:
        rows.append(len(nav_buttons))
    rows.extend([2, 2, 1])
    b.adjust(*rows)
    return b.as_markup()


def herosms_sync_menu_kb() -> InlineKeyboardMarkup:
    """قائمة اختيار خدمات السحب من HeroSMS."""
    b = InlineKeyboardBuilder()
    b.button(
        text="💬✈️ واتساب + تيليجرام (موصى به)",
        callback_data="admin:country_sync:whatsapp,telegram",
    )
    b.button(text="💬 واتساب فقط", callback_data="admin:country_sync:whatsapp")
    b.button(text="✈️ تيليجرام فقط", callback_data="admin:country_sync:telegram")
    b.button(
        text="⚪ سحب بدون تفعيل (كلاهما)",
        callback_data="admin:country_sync_idle:whatsapp,telegram",
    )
    b.button(
        text="🗑 تصفير الدول المسحوبة وإعادة السحب",
        callback_data="admin:country_reset", style="danger",
    )
    b.button(text="🔙 رجوع", callback_data="admin:countries")
    b.adjust(1, 2, 1, 1, 1)
    return b.as_markup()


def country_reset_confirm_kb() -> InlineKeyboardMarkup:
    """تأكيد تصفير الدول المسحوبة تلقائياً من HeroSMS."""
    b = InlineKeyboardBuilder()
    b.button(
        text="🗑 نعم، احذف جميع الدول",
        callback_data="admin:country_delete_all_execute", style="danger",
    )
    b.button(text="❌ تراجع", callback_data="admin:countries")
    b.adjust(1)
    return b.as_markup()


def admin_country_detail_kb(country) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if country.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:country_toggle:{country.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:country_toggle:{country.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:country_delete:{country.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data="admin:countries")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الأسعار ══════════════


def admin_pricing_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📈 تعديل نسبة الربح العامة", callback_data="admin:set_margin", style="primary")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الإعدادات ══════════════


def admin_loyalty_settings_kb() -> InlineKeyboardMarkup:
    """أزرار إعدادات برنامج الولاء."""
    b = InlineKeyboardBuilder()
    b.button(
        text="💎 نقاط كل دولار",
        callback_data="admin:loyalty_set:loyalty_points_per_usd", style="primary",
    )
    b.button(
        text="🎁 مكافأة التسجيل اليومي",
        callback_data="admin:loyalty_set:loyalty_daily_points", style="primary",
    )
    b.button(
        text="💵 معامل الاستبدال",
        callback_data="admin:loyalty_set:loyalty_points_per_usd_redeem", style="primary",
    )
    b.button(
        text="🔢 الحد الأدنى للاستبدال",
        callback_data="admin:loyalty_set:loyalty_min_redeem_points", style="primary",
    )
    b.button(text="🔙 لوحة الولاء", callback_data="admin:loyalty")
    b.adjust(1)
    return b.as_markup()


def admin_payment_settings_kb(settings_values: dict[str, bool]) -> InlineKeyboardMarkup:
    """أزرار تشغيل وإيقاف طرق الدفع."""
    labels = {
        "payment_shamcash_manual_enabled": "💵 شام كاش يدوي",
        "payment_stars_enabled": "⭐ نجوم تيليجرام",
        "payment_usdt_manual_enabled": "₮ USDT يدوي",
        "payment_shamcash_auto_enabled": "💳 شام كاش تلقائي",
        "payment_usdt_auto_enabled": "₮ USDT تلقائي",
        "payment_other_enabled": "📞 طرق أخرى",
        "withdraw_shamcash_syp_enabled": "🇸🇾 سحب شام كاش بالليرة",
    }
    b = InlineKeyboardBuilder()
    for key, label in labels.items():
        state = "🟢 مفعّل" if settings_values.get(key, False) else "⚪ معطّل"
        b.button(
            text=f"{state} {label}",
            callback_data=f"admin:payment_toggle:{key}", style="primary",
        )
    b.button(text="🔙 الإعدادات", callback_data="admin:settings")
    b.adjust(1)
    return b.as_markup()


def admin_settings_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💱 أسعار الصرف اليومية", callback_data="admin:rates", style="primary")
    b.button(text="🛠 يوزر الدعم", callback_data="admin:set_support")
    b.button(text="💳 طريقة الدفع", callback_data="admin:set_payment", style="primary")
    b.button(text="🎛 تفعيل طرق الدفع", callback_data="admin:payment_settings", style="primary")
    b.button(text="🎁 إعدادات الولاء", callback_data="admin:loyalty_settings", style="primary")
    b.button(text="🚨 حد التحويل الكبير", callback_data="admin:set_large_tx")
    b.button(text="⏳ مهلة انتظار الكود", callback_data="admin:set_order_timeout", style="primary")
    b.button(text="📝 رسالة الترحيب", callback_data="admin:set_welcome")
    b.button(text="💰 نسبة الكاشباك", callback_data="admin:set_cashback")
    b.button(text="💎 نسبة الإحالة", callback_data="admin:set_referral_percent", style="primary")
    b.button(text="⏱ Rate Limit", callback_data="admin:set_rate_limit")
    b.button(text="📢 قناة الإشعارات العامة", callback_data="admin:set_public_channel")
    b.button(text="💾 قناة البكاب", callback_data="admin:set_backup_channel")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1, 2, 2, 2, 2, 2, 2, 1)
    return b.as_markup()


def admin_rates_kb() -> InlineKeyboardMarkup:
    """أزرار تعديل أسعار صرف العرض اليومية."""
    b = InlineKeyboardBuilder()
    b.button(text="🇸🇾 ليرة سورية", callback_data="admin:rate_set:usd_to_syp_rate")
    b.button(text="🇪🇺 يورو", callback_data="admin:rate_set:usd_to_eur_rate")
    b.button(text="🇪🇬 جنيه مصري", callback_data="admin:rate_set:usd_to_egp_rate")
    b.button(text="🔙 رجوع", callback_data="admin:settings")
    b.adjust(3, 1)
    return b.as_markup()


# ══════════════ الإيداعات ══════════════


def deposit_decision_kb(deposit_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ قبول", callback_data=f"deposit_accept:{deposit_id}", style="primary")
    b.button(text="❌ رفض", callback_data=f"deposit_reject:{deposit_id}", style="danger")
    b.adjust(2)
    return b.as_markup()


# ══════════════ إدارة مستخدم ══════════════


def user_manage_kb(user_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ إضافة رصيد", callback_data=f"admin:user_add_balance:{user_id}", style="primary")
    b.button(text="➖ خصم رصيد", callback_data=f"admin:user_deduct_balance:{user_id}", style="primary")
    b.button(text="📋 سجل المعاملات", callback_data=f"admin:user_transactions:{user_id}")
    b.button(text="📩 إرسال رسالة", callback_data=f"admin:user_send_msg:{user_id}")
    if is_banned:
        b.button(text="✅ فك الحظر", callback_data=f"admin:user_unban:{user_id}", style="danger")
    else:
        b.button(text="🚫 حظر", callback_data=f"admin:user_ban:{user_id}", style="danger")
    b.button(text="🔙 رجوع", callback_data="admin:users")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


# ══════════════ القنوات ══════════════


def admin_channels_kb(channels) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for ch in channels:
        b.button(
            text=f"❌ حذف: {ch.title or ch.chat_id}",
            callback_data=f"admin:channel_del:{ch.id}", style="danger",
        )
    b.button(text="➕ إضافة قناة", callback_data="admin:channel_add", style="success")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()