"""
أزرار خدمة الأرقام:
- عرض 25 دولة في كل صفحة بشكل مربعات (زراين) جنب بعض.
- إظهار السعر النهائي وعلم الدولة على كل زر.
- ترتيب دائم من الأرخص إلى الأغلى.
- أزرار تنقل واضحة بين الصفحات وسهلة الاستخدام.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import Country, NumberService

# تحديد 25 دولة في كل صفحة (صف بمربعين)
COUNTRIES_PER_PAGE = 25


def format_price_display(price) -> str:
    """تنسيق السعر ليظهر بشكل منسق بدون أصفار زائدة."""
    from decimal import Decimal
    p = Decimal(str(price))
    if p < Decimal("0.01"):
        return f"{p:.3f}"
    return f"{p:.2f}"


def number_services_kb(
    services: list[NumberService],
) -> InlineKeyboardMarkup:
    """قائمة خدمات الأرقام (واتساب، تيليجرام.. إلخ)."""
    b = InlineKeyboardBuilder()
    for svc in services:
        b.button(
            text=f"{svc.emoji} أرقام {svc.name_ar}",
            callback_data=f"num_svc:{svc.code}", style="success",
        )
    b.button(
        text="🔙 رجوع للقائمة الرئيسية",
        callback_data="back_to_main",
    )
    b.adjust(2)
    return b.as_markup()


def numbers_hub_kb(
    services: list[NumberService],
    back_to_store: bool = True,
) -> InlineKeyboardMarkup:
    """قسم «الأرقام» الموحّد: كل خدمات الأرقام (واتساب/تيليجرام/جديدة).

    أي خدمة أرقام يضيفها الأدمن من «إدارة خدمات الأرقام» تظهر هنا
    تلقائياً دون تعديل الكود.
    """
    b = InlineKeyboardBuilder()
    for svc in services:
        b.button(
            text=f"{svc.emoji} أرقام {svc.name_ar}",
            callback_data=f"num_svc:{svc.code}", style="success",
        )
    if back_to_store:
        b.button(text="🔙 رجوع للمتجر", callback_data="store:home")
    else:
        b.button(text="🔙 رجوع", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def countries_kb(
    service_code: str,
    countries: list[Country],
    page: int = 0,
) -> InlineKeyboardMarkup:
    """قائمة الدول بدون أسعار (احتياطية) — 25 دولة بمربعات جنب بعض."""
    b = InlineKeyboardBuilder()

    start = page * COUNTRIES_PER_PAGE
    end = start + COUNTRIES_PER_PAGE
    page_countries = countries[start:end]
    total_pages = max(1, (len(countries) + COUNTRIES_PER_PAGE - 1) // COUNTRIES_PER_PAGE)

    for c in page_countries:
        b.button(
            text=f"{c.flag} {c.name_ar}",
            callback_data=f"num_country:{service_code}:{c.code}", style="success",
        )

    nav_buttons_count = 0
    if page > 0:
        b.button(
            text="◀️ السابق",
            callback_data=f"num_page:{service_code}:{page - 1}",
        )
        nav_buttons_count += 1

    b.button(
        text=f"📄 {page + 1}/{total_pages}",
        callback_data="noop",
    )
    nav_buttons_count += 1

    if page < total_pages - 1:
        b.button(
            text="التالي ▶️",
            callback_data=f"num_page:{service_code}:{page + 1}",
        )
        nav_buttons_count += 1

    b.button(
        text="🔙 رجوع",
        callback_data="back_to_main",
    )

    rows = [2] * (len(page_countries) // 2)
    if len(page_countries) % 2:
        rows.append(1)
    if nav_buttons_count:
        rows.append(nav_buttons_count)
    rows.append(1)

    b.adjust(*rows)
    return b.as_markup()


def countries_price_kb(
    service_code: str,
    entries: list,
    page: int = 0,
    server_id: int | None = None,
    server_label: str | None = None,
) -> InlineKeyboardMarkup:
    """
    قائمة الدول مرتبة من الأرخص للأغلى:
    - 25 دولة في كل صفحة.
    - شكل مربعات: زران جنب بعض في كل صف.
    - السعر النهائي (التكلفة + نسبة الربح) ظاهر على كل زر مع العلم.

    ``server_id``: عند وجود سيرفر مختار، تُمرَّر بانيات الأزرار عبر كل الخطوات
    حتى يبقى الشراء على نفس السيرفر.
    """
    b = InlineKeyboardBuilder()

    total_pages = max(1, (len(entries) + COUNTRIES_PER_PAGE - 1) // COUNTRIES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * COUNTRIES_PER_PAGE
    end = start + COUNTRIES_PER_PAGE
    page_entries = entries[start:end]

    suffix = f":{server_id}" if server_id else ""

    # عرض أزرار الدول بشكل مربعات (زراين في كل صف) مع العلم والسعر
    for entry in page_entries:
        price_str = format_price_display(entry.sell_usd)
        name = entry.name_ar
        # نختصر الاسم الطويل حتى لا يُقص السعر مع العرض بصفين
        if len(name) > 18:
            name = name[:17] + "…"
        b.button(
            text=f"{entry.flag} {name} — {price_str}$",
            callback_data=f"num_country:{service_code}:{entry.code}{suffix}", style="success",
        )

    # أزرار التنقل
    nav_buttons_count = 0
    if page > 0:
        b.button(
            text="◀️ السابق",
            callback_data=f"num_page:{service_code}:{page - 1}{suffix}",
        )
        nav_buttons_count += 1

    b.button(
        text=f"📄 {page + 1}/{total_pages}",
        callback_data="noop",
    )
    nav_buttons_count += 1

    if page < total_pages - 1:
        b.button(
            text="التالي ▶️",
            callback_data=f"num_page:{service_code}:{page + 1}{suffix}",
        )
        nav_buttons_count += 1

    if server_id and server_label:
        # ``server_label`` يصل جاهزاً بالإيموجي («🖥 سيرفر 2» أو «🟢 🖥 سيرفر 2»)
        # فلا نضيف إيموجي ثانياً حتى لا يتكرر على الزر.
        b.button(
            text=f"{server_label} · تغيير السيرفر",
            callback_data=f"num_server:{service_code}",
            style="success",
        )
        nav_buttons_count += 1

    b.button(
        text="🔙 رجوع للأقسام",
        callback_data="store:home",
    )

    # صف بمربعين للدول، ثم صف التنقل، ثم الرجوع
    rows = [2] * (len(page_entries) // 2)
    if len(page_entries) % 2:
        rows.append(1)
    if nav_buttons_count:
        rows.append(nav_buttons_count)
    rows.append(1)

    b.adjust(*rows)
    return b.as_markup()


def confirm_purchase_kb(
    service_code: str,
    country_code: str,
    quote_token: str | None = None,
    server_id: int | None = None,
) -> InlineKeyboardMarkup:
    """تأكيد شراء الرقم الفردي أو البدء بالجملة."""
    suffix = f":{server_id}" if server_id else ""
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ تأكيد الشراء الآن",
        callback_data=f"num_confirm:{service_code}:{country_code}:{quote_token or ''}{suffix}", style="primary",
    )
    b.button(
        text="📦 شراء بالجملة",
        callback_data=f"num_bulk_start:{service_code}:{country_code}:{quote_token or ''}{suffix}", style="success",
    )
    b.button(
        text="🔙 تراجع",
        callback_data=f"num_server:{service_code}" if server_id else f"num_svc:{service_code}",
        style="success",
    )
    b.adjust(1)
    return b.as_markup()


def bulk_quantity_kb(
    service_code: str,
    country_code: str,
    quote_token: str | None = None,
    server_id: int | None = None,
) -> InlineKeyboardMarkup:
    """خيارات الكمية للشراء بالجملة."""
    suffix = f":{server_id}" if server_id else ""
    b = InlineKeyboardBuilder()
    for quantity in (5, 10, 25, 50, 100):
        b.button(
            text=f"{quantity} رقم",
            callback_data=f"num_bulk_qty:{service_code}:{country_code}:{quantity}{suffix}", style="success",
        )
    b.button(
        text="✍️ كمية مخصصة",
        callback_data=f"num_bulk_custom:{service_code}:{country_code}:{quote_token or ''}{suffix}", style="success",
    )
    b.button(
        text="🔙 رجوع للسعر",
        callback_data=f"num_country:{service_code}:{country_code}{suffix}",
    )
    b.adjust(2, 2, 1, 1, 1)
    return b.as_markup()


def bulk_confirm_kb(
    service_code: str,
    country_code: str,
    quantity: int,
    server_id: int | None = None,
) -> InlineKeyboardMarkup:
    """تأكيد تنفيذ دفعة الجملة."""
    suffix = f":{server_id}" if server_id else ""
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ تنفيذ الدفعة الآن",
        callback_data=f"num_bulk_confirm:{service_code}:{country_code}:{quantity}{suffix}", style="primary",
    )
    b.button(
        text="🔢 تغيير الكمية",
        callback_data=f"num_bulk_start:{service_code}:{country_code}:{suffix}", style="success",
    )
    b.button(
        text="❌ إلغاء",
        callback_data="back_to_main",
    )
    b.adjust(1)
    return b.as_markup()


def ready_number_packages_kb(packages: list[dict]) -> InlineKeyboardMarkup:
    """باقات أرقام جاهزة للمستخدمين."""
    b = InlineKeyboardBuilder()
    for package in packages:
        b.button(
            text=package["label"],
            callback_data=(
                f"num_bulk_qty:{package['service_code']}:"
                f"{package['country_code']}:{package['quantity']}"
            ), style="success",
        )
    b.button(text="🔙 رجوع للقائمة", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def order_actions_kb(order_id: int) -> InlineKeyboardMarkup:
    """أزرار أثناء انتظار وصول الكود."""
    b = InlineKeyboardBuilder()
    b.button(
        text="🔄 تحديث الكود",
        callback_data=f"num_refresh:{order_id}", style="success",
    )
    b.button(
        text="❌ إلغاء واسترجاع الرصيد",
        callback_data=f"num_cancel:{order_id}", style="danger",
    )
    b.adjust(1)
    return b.as_markup()


def code_received_kb(order_id: int) -> InlineKeyboardMarkup:
    """أزرار بعد استلام الكود."""
    b = InlineKeyboardBuilder()
    b.button(
        text="🔄 انتظار كود إضافي",
        callback_data=f"num_extra:{order_id}", style="success",
    )
    b.button(
        text="✅ انتهيت",
        callback_data=f"num_finish:{order_id}", style="success",
    )
    b.adjust(1)
    return b.as_markup()