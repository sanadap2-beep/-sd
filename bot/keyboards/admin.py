"""
كل أزرار لوحة الأدمن.
"""

from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings


# ══════════════ اللوحة الرئيسية ══════════════


def admin_main_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📘 شرح البوت", callback_data="admin:guide")
    b.button(text="📊 إحصائيات البوت", callback_data="admin:stats")
    b.button(text="📦 إدارة الطلبات", callback_data="admin:orders")
    b.button(text="📞 طلبات الأرقام", callback_data="admin:number_orders")
    b.button(text="💳 طلبات الشحن", callback_data="admin:deposits")
    b.button(text="💸 طلبات السحب", callback_data="admin:withdrawals")
    b.button(text="📢 إدارة الإعلانات", callback_data="admin:ads")
    b.button(text="🔥 قسم العروض", callback_data="admin:special_offers")
    b.button(text="🎁 بطاقات الهدايا", callback_data="admin:gift_codes")
    b.button(text="📈 طلبات السوق", callback_data="admin:market_requests")
    b.button(text="📂 إدارة الأقسام", callback_data="admin:categories")
    b.button(text="🎛 أزرار الواجهة", callback_data="admin:main_buttons")
    b.button(text="📦 إدارة المنتجات", callback_data="admin:products_menu")
    b.button(text="📞 إدارة خدمات الأرقام", callback_data="admin:number_services")
    b.button(text="🌍 إدارة الدول", callback_data="admin:countries")
    b.button(text="🔌 مزودو المتجر", callback_data="admin:api_providers")
    b.button(text="🌐 مزودو الأرقام", callback_data="admin:providers")
    b.button(text="📊 جودة مزودي الأرقام", callback_data="admin:number_provider_quality")
    b.button(text="💵 تعديل الأسعار", callback_data="admin:pricing")
    b.button(text="⭐ إدارة باقات النجوم", callback_data="admin:stars")
    b.button(text="🎟 إدارة الكوبونات", callback_data="admin:coupons")
    b.button(text="👥 إدارة المستخدمين", callback_data="admin:users")
    b.button(text="👨‍💼 إدارة الأدمنية", callback_data="admin:multi_admin")
    b.button(text="📌 الاشتراك الإجباري", callback_data="admin:channels")
    b.button(text="📢 إذاعة جماعية", callback_data="admin:broadcast")
    b.button(text="🔧 وضع الصيانة", callback_data="admin:maintenance")
    b.button(text="⚙️ الإعدادات العامة", callback_data="admin:settings")
    if settings.ADMIN_WEBAPP_URL:
        b.button(
            text="🌐 لوحة الويب",
            web_app=WebAppInfo(url=settings.ADMIN_WEBAPP_URL),
        )
    b.button(text="📜 سجل الإدارة", callback_data="admin:audit")
    b.button(text="🎫 تذاكر الدعم", callback_data="admin:tickets")
    b.button(text="🩺 صحة النظام", callback_data="admin:health")
    b.button(text="🔔 إدارة الإشعارات", callback_data="admin:notifications")
    b.button(text="🎁 برنامج الولاء", callback_data="admin:loyalty")
    b.button(text="📦 المخزون الرقمي", callback_data="admin:inventory")
    b.button(text="🔥 إدارة العروض", callback_data="admin:promotions")
    b.button(text="🧩 مركز الإضافات", callback_data="admin:features")
    b.button(text="🎛 مركز القيادة", callback_data="admin:cockpit")
    b.button(text="🛠 مركز العمليات", callback_data="admin:ops")
    b.adjust(2)
    return b.as_markup()


def admin_orders_kb(orders, page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    """قائمة الطلبات الموحدة للأدمن."""
    b = InlineKeyboardBuilder()
    for order in orders:
        product_name = order.product.name_ar[:24] if order.product else "منتج"
        status = order.status.value
        b.button(
            text=f"#{order.id} {product_name} · {status}",
            callback_data=f"admin:order_view:{order.id}",
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
            callback_data=f"admin:order_complete:{order.id}",
        )
        b.button(
            text="↩️ استرجاع الرصيد",
            callback_data=f"admin:order_refund_ask:{order.id}",
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
            callback_data=f"admin:deposit_view:{deposit.id}",
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
        b.button(text="✅ قبول وإضافة الرصيد", callback_data=f"deposit_accept:{deposit_id}")
        b.button(text="❌ رفض الطلب", callback_data=f"deposit_reject:{deposit_id}")
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
            callback_data=f"admin:num_order_view:{order.id}",
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
        callback_data=f"admin:order_refund:{order_id}",
    )
    b.button(
        text="❌ إلغاء",
        callback_data=f"admin:order_view:{order_id}",
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
            callback_data=f"admin:num_order_refund_ask:{order.id}",
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
        callback_data=f"admin:num_order_refund:{order_id}",
    )
    b.button(
        text="❌ إلغاء",
        callback_data=f"admin:num_order_view:{order_id}",
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
    b.button(text="➕ إضافة كود/ترخيص", callback_data=f"admin:inv_add:{product_id}")
    b.button(text="📋 العناصر المتاحة", callback_data=f"admin:inv_items:{product_id}")
    b.button(text="🔙 المخزون", callback_data="admin:inventory")
    b.adjust(1)
    return b.as_markup()


def admin_inventory_items_kb(items, product_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for item in items:
        b.button(
            text=f"🗑 إلغاء العنصر #{item.id}",
            callback_data=f"admin:inv_void:{item.id}:{product_id}",
        )
    b.button(
        text="➕ إضافة عنصر",
        callback_data=f"admin:inv_add:{product_id}",
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
        b.button(text="🔴 تفعيل الصيانة", callback_data="admin:maintenance_on")
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
    b.button(text="➕ إضافة قسم جديد", callback_data="admin:cat_add")
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
    b.button(text="🗑 حذف", callback_data=f"admin:cat_delete:{category.id}")
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
    b.button(text="➕ إضافة قسم فرعي", callback_data=f"admin:subcat_add:{category_id}")
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
    b.button(text="🗑 حذف", callback_data=f"admin:subcat_delete:{sub_category.id}")
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
    b.button(text="➕ إضافة منتج", callback_data=f"admin:prod_add:{sub_category_id}")
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
    b.button(text="➕ أضفه كزر رئيسي", callback_data=f"mb:add_prod:{product.id}")
    b.button(text="🧪 فحص جاهزية المنتج", callback_data=f"admin:prod_ready:{product.id}")
    b.button(text="💰 تعديل السعر", callback_data=f"admin:prod_edit_price:{product.id}")
    b.button(text="📝 تعديل الاسم", callback_data=f"admin:prod_edit_name:{product.id}")
    b.button(text="🔌 تعديل آيدي المزود", callback_data=f"admin:prod_edit_svc_id:{product.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:prod_delete:{product.id}")
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
    b.button(text="➕ إضافة مزود", callback_data="admin:aprov_add")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_api_provider_detail_kb(provider) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if provider.is_active:
        b.button(text="🔴 تعطيل", callback_data=f"admin:aprov_toggle:{provider.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:aprov_toggle:{provider.id}")
    b.button(text="💰 فحص الرصيد", callback_data=f"admin:aprov_balance:{provider.id}")
    b.button(text="📝 تعديل الاسم", callback_data=f"admin:aprov_edit_name:{provider.id}")
    b.button(text="🔑 تعديل API Key", callback_data=f"admin:aprov_edit_key:{provider.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:aprov_delete:{provider.id}")
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
    b.button(text="➕ إضافة باقة", callback_data="admin:star_add")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_star_detail_kb(package) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if package.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:star_toggle:{package.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:star_toggle:{package.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:star_delete:{package.id}")
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
            callback_data=f"admin:coupon_view:{c.id}",
        )
    b.button(text="➕ إنشاء كوبون", callback_data="admin:coupon_add")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_coupon_detail_kb(coupon) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if coupon.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:coupon_toggle:{coupon.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:coupon_toggle:{coupon.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:coupon_delete:{coupon.id}")
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
    b.button(text="➕ إضافة أدمن", callback_data="admin:madmin_add")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_madmin_detail_kb(admin_user, is_primary: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if not is_primary:
        b.button(text="🗑 إزالة الأدمنية", callback_data=f"admin:madmin_remove:{admin_user.id}")
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
    b.button(text="➕ إضافة خدمة أرقام", callback_data="admin:nsvc_add")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def admin_nsvc_detail_kb(service) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if service.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:nsvc_toggle:{service.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:nsvc_toggle:{service.id}")
    b.button(text="📝 تعديل الاسم", callback_data=f"admin:nsvc_edit_name:{service.id}")
    b.button(text="🗑 حذف", callback_data=f"admin:nsvc_delete:{service.id}")
    b.button(text="🔙 رجوع", callback_data="admin:number_services")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الدول ══════════════

# عدد الدول في كل صفحة بلوحة الأدمن.
# بدون ترقيم صفحات: قائمة طويلة تدفع أزرار الإدارة (سحب/إضافة) لآخر
# الرسالة حيث لا يراها الأدمن، وتيليجرام يرفض أي لوحة تتجاوز 100 زر.
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

    # ── أزرار التنقل ──
    nav_buttons = []
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"admin:countries:{page - 1}")
        nav_buttons.append(1)
    if page < total_pages - 1:
        b.button(text="التالي ▶️", callback_data=f"admin:countries:{page + 1}")
        nav_buttons.append(1)

    # ── أزرار الإدارة (دائماً أسفل الصفحة) ──
    b.button(text="➕ إضافة دولة جديدة", callback_data="admin:country_add")
    b.button(text="🔄 سحب دول من HeroSMS", callback_data="admin:country_sync_herosms")
    b.button(text="📋 أكواد 5sim المرجعية", callback_data="admin:country_reference_list")
    b.button(text="🔙 رجوع", callback_data="admin:main")

    rows = [2] * ((len(page_countries) + 1) // 2)
    if nav_buttons:
        rows.append(len(nav_buttons))
    rows.extend([2, 2])
    b.adjust(*rows)
    return b.as_markup()


def herosms_sync_menu_kb() -> InlineKeyboardMarkup:
    """قائمة اختيار خدمات السحب من HeroSMS."""
    b = InlineKeyboardBuilder()
    b.button(text="💬 واتساب فقط", callback_data="admin:country_sync:whatsapp")
    b.button(text="✈️ تيليجرام فقط", callback_data="admin:country_sync:telegram")
    b.button(
        text="💬✈️ واتساب + تيليجرام",
        callback_data="admin:country_sync:whatsapp,telegram",
    )
    b.button(
        text="⚪ سحب بدون تفعيل (كلاهما)",
        callback_data="admin:country_sync_idle:whatsapp,telegram",
    )
    b.button(
        text="🗑 تصفير الدول المسحوبة وإعادة السحب",
        callback_data="admin:country_reset",
    )
    b.button(text="🔙 رجوع", callback_data="admin:countries")
    b.adjust(2, 1, 1, 1, 1)
    return b.as_markup()


def country_reset_confirm_kb() -> InlineKeyboardMarkup:
    """تأكيد تصفير الدول المسحوبة تلقائياً من HeroSMS."""
    b = InlineKeyboardBuilder()
    b.button(
        text="🗑 نعم، صفّر وأعد السحب",
        callback_data="admin:country_reset_go",
    )
    b.button(text="❌ إلغاء", callback_data="admin:country_sync_herosms")
    b.adjust(1)
    return b.as_markup()


def admin_country_detail_kb(country) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if country.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"admin:country_toggle:{country.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"admin:country_toggle:{country.id}")
    b.button(
        text="💬 سعر واتساب اليدوي",
        callback_data=f"admin:country_price:whatsapp:{country.id}",
    )
    b.button(
        text="✈️ سعر تيليجرام اليدوي",
        callback_data=f"admin:country_price:telegram:{country.id}",
    )
    b.button(text="🗑 حذف", callback_data=f"admin:country_delete:{country.id}")
    b.button(text="🔙 رجوع", callback_data="admin:countries")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الأسعار ══════════════


def admin_pricing_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📈 تعديل نسبة الربح العامة", callback_data="admin:set_margin")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الإعدادات ══════════════


def admin_loyalty_settings_kb() -> InlineKeyboardMarkup:
    """أزرار إعدادات برنامج الولاء."""
    b = InlineKeyboardBuilder()
    b.button(
        text="💎 نقاط كل دولار",
        callback_data="admin:loyalty_set:loyalty_points_per_usd",
    )
    b.button(
        text="🎁 مكافأة التسجيل اليومي",
        callback_data="admin:loyalty_set:loyalty_daily_points",
    )
    b.button(
        text="💵 معامل الاستبدال",
        callback_data="admin:loyalty_set:loyalty_points_per_usd_redeem",
    )
    b.button(
        text="🔢 الحد الأدنى للاستبدال",
        callback_data="admin:loyalty_set:loyalty_min_redeem_points",
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
            callback_data=f"admin:payment_toggle:{key}",
        )
    b.button(text="🔙 الإعدادات", callback_data="admin:settings")
    b.adjust(1)
    return b.as_markup()


def admin_settings_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💱 أسعار الصرف اليومية", callback_data="admin:rates")
    b.button(text="🛠 يوزر الدعم", callback_data="admin:set_support")
    b.button(text="💳 طريقة الدفع", callback_data="admin:set_payment")
    b.button(text="🎛 تفعيل طرق الدفع", callback_data="admin:payment_settings")
    b.button(text="🎁 إعدادات الولاء", callback_data="admin:loyalty_settings")
    b.button(text="🚨 حد التحويل الكبير", callback_data="admin:set_large_tx")
    b.button(text="⏳ مهلة انتظار الكود", callback_data="admin:set_order_timeout")
    b.button(text="📝 رسالة الترحيب", callback_data="admin:set_welcome")
    b.button(text="💰 نسبة الكاشباك", callback_data="admin:set_cashback")
    b.button(text="💎 نسبة الإحالة", callback_data="admin:set_referral_percent")
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
    b.button(text="✅ قبول", callback_data=f"deposit_accept:{deposit_id}")
    b.button(text="❌ رفض", callback_data=f"deposit_reject:{deposit_id}")
    b.adjust(2)
    return b.as_markup()


# ══════════════ إدارة مستخدم ══════════════


def user_manage_kb(user_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ إضافة رصيد", callback_data=f"admin:user_add_balance:{user_id}")
    b.button(text="➖ خصم رصيد", callback_data=f"admin:user_deduct_balance:{user_id}")
    b.button(text="📋 سجل المعاملات", callback_data=f"admin:user_transactions:{user_id}")
    b.button(text="📩 إرسال رسالة", callback_data=f"admin:user_send_msg:{user_id}")
    if is_banned:
        b.button(text="✅ فك الحظر", callback_data=f"admin:user_unban:{user_id}")
    else:
        b.button(text="🚫 حظر", callback_data=f"admin:user_ban:{user_id}")
    b.button(text="🔙 رجوع", callback_data="admin:users")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()


# ══════════════ القنوات ══════════════


def admin_channels_kb(channels) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for ch in channels:
        b.button(
            text=f"❌ حذف: {ch.title or ch.chat_id}",
            callback_data=f"admin:channel_del:{ch.id}",
        )
    b.button(text="➕ إضافة قناة", callback_data="admin:channel_add")
    b.button(text="🔙 رجوع", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()
