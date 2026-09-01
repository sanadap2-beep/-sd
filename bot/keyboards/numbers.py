"""
أزرار خدمة الأرقام.
تعمل مع النظام الديناميكي بالكامل.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import Country, NumberService

COUNTRIES_PER_PAGE = 12


def number_services_kb(
    services: list[NumberService],
) -> InlineKeyboardMarkup:
    """قائمة خدمات الأرقام الديناميكية."""
    b = InlineKeyboardBuilder()
    for svc in services:
        b.button(
            text=f"{svc.emoji} أرقام {svc.name_ar}",
            callback_data=f"num_svc:{svc.code}",
        )
    b.button(
        text="🔙 رجوع للقائمة",
        callback_data="back_to_main",
    )
    b.adjust(2)
    return b.as_markup()


def countries_kb(
    service_code: str,
    countries: list[Country],
    page: int = 0,
) -> InlineKeyboardMarkup:
    """
    قائمة الدول مع Pagination.
    تعرض COUNTRIES_PER_PAGE دولة في كل صفحة.
    """
    b = InlineKeyboardBuilder()

    start = page * COUNTRIES_PER_PAGE
    end = start + COUNTRIES_PER_PAGE
    page_countries = countries[start:end]
    total_pages = (len(countries) + COUNTRIES_PER_PAGE - 1) // COUNTRIES_PER_PAGE

    for c in page_countries:
        b.button(
            text=f"{c.flag} {c.name_ar}",
            callback_data=(f"num_country:{service_code}:{c.code}"),
        )

    # ── أزرار التنقل ──
    nav_buttons = []
    if page > 0:
        b.button(
            text="◀️ السابق",
            callback_data=(f"num_page:{service_code}:{page - 1}"),
        )
        nav_buttons.append(1)
    if page < total_pages - 1:
        b.button(
            text="التالي ▶️",
            callback_data=(f"num_page:{service_code}:{page + 1}"),
        )
        nav_buttons.append(1)

    b.button(
        text="🔙 رجوع",
        callback_data="back_to_main",
    )

    rows = [2] * (len(page_countries) // 2)
    if len(page_countries) % 2:
        rows.append(1)
    if nav_buttons:
        rows.append(len(nav_buttons))
    rows.append(1)

    b.adjust(*rows)
    return b.as_markup()


def countries_price_kb(
    service_code: str,
    entries: list,
    page: int = 0,
) -> InlineKeyboardMarkup:
    """
    قائمة الدول مع سعر البيع لكل دولة، مرتبة من الأرخص للأغلى.

    entries: قائمة BoardEntry من NumberCatalogService.build_board.
    الدول بلا سعر أو مخزون لا تظهر في اللوحة أصلاً.
    """
    from services.number_catalog_service import format_price

    b = InlineKeyboardBuilder()

    start = page * COUNTRIES_PER_PAGE
    end = start + COUNTRIES_PER_PAGE
    page_entries = entries[start:end]
    total_pages = (len(entries) + COUNTRIES_PER_PAGE - 1) // COUNTRIES_PER_PAGE

    for entry in page_entries:
        b.button(
            text=f"{entry.flag} {entry.name_ar} · {format_price(entry.sell_usd)}$",
            callback_data=(f"num_country:{service_code}:{entry.code}"),
        )

    # ── أزرار التنقل ──
    nav_buttons = []
    if page > 0:
        b.button(
            text="◀️ السابق",
            callback_data=(f"num_page:{service_code}:{page - 1}"),
        )
        nav_buttons.append(1)
    if page < total_pages - 1:
        b.button(
            text="التالي ▶️",
            callback_data=(f"num_page:{service_code}:{page + 1}"),
        )
        nav_buttons.append(1)

    b.button(
        text="🔙 رجوع",
        callback_data="back_to_main",
    )

    rows = [1] * len(page_entries)
    if nav_buttons:
        rows.append(len(nav_buttons))
    rows.append(1)

    b.adjust(*rows)
    return b.as_markup()


def confirm_purchase_kb(
    service_code: str,
    country_code: str,
    quote_token: str | None = None,
) -> InlineKeyboardMarkup:
    """تأكيد شراء رقم أو بدء دفعة جملة."""
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ شراء رقم واحد",
        callback_data=f"num_confirm:{service_code}:{country_code}:{quote_token or ''}",
    )
    b.button(
        text="📦 شراء بالجملة",
        callback_data=f"num_bulk_start:{service_code}:{country_code}:{quote_token or ''}",
    )
    b.button(
        text="❌ إلغاء",
        callback_data="back_to_main",
    )
    b.adjust(1)
    return b.as_markup()


def bulk_quantity_kb(
    service_code: str,
    country_code: str,
    quote_token: str | None = None,
) -> InlineKeyboardMarkup:
    """اختيارات كمية سريعة لشراء الأرقام بالجملة."""
    b = InlineKeyboardBuilder()
    for quantity in (5, 10, 25, 50, 100):
        b.button(
            text=f"{quantity} رقم",
            callback_data=f"num_bulk_qty:{service_code}:{country_code}:{quantity}",
        )
    b.button(
        text="✍️ كمية مخصصة",
        callback_data=f"num_bulk_custom:{service_code}:{country_code}:{quote_token or ''}",
    )
    b.button(
        text="🔙 رجوع للسعر",
        callback_data=f"num_country:{service_code}:{country_code}",
    )
    b.adjust(2, 2, 1, 1, 1)
    return b.as_markup()


def bulk_confirm_kb(
    service_code: str,
    country_code: str,
    quantity: int,
) -> InlineKeyboardMarkup:
    """تأكيد تنفيذ دفعة الجملة بعد عرض السعر."""
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ تنفيذ الدفعة الآن",
        callback_data=f"num_bulk_confirm:{service_code}:{country_code}:{quantity}",
    )
    b.button(
        text="🔢 تغيير الكمية",
        callback_data=f"num_bulk_start:{service_code}:{country_code}:",
    )
    b.button(
        text="❌ إلغاء",
        callback_data="back_to_main",
    )
    b.adjust(1)
    return b.as_markup()


def ready_number_packages_kb(packages: list[dict]) -> InlineKeyboardMarkup:
    """باقات أرقام جاهزة للمستخدمين والتجار."""
    b = InlineKeyboardBuilder()
    for package in packages:
        b.button(
            text=package["label"],
            callback_data=(
                f"num_bulk_qty:{package['service_code']}:"
                f"{package['country_code']}:{package['quantity']}"
            ),
        )
    b.button(text="🔙 رجوع للقائمة", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def order_actions_kb(order_id: int) -> InlineKeyboardMarkup:
    """أزرار أثناء انتظار الكود."""
    b = InlineKeyboardBuilder()
    b.button(
        text="🔄 تحديث",
        callback_data=f"num_refresh:{order_id}",
    )
    b.button(
        text="❌ إلغاء واسترجاع الرصيد",
        callback_data=f"num_cancel:{order_id}",
    )
    b.adjust(1)
    return b.as_markup()


def code_received_kb(
    order_id: int,
) -> InlineKeyboardMarkup:
    """تظهر بعد استلام الكود."""
    b = InlineKeyboardBuilder()
    b.button(
        text="🔄 انتظار كود إضافي",
        callback_data=f"num_extra:{order_id}",
    )
    b.button(
        text="✅ انتهيت",
        callback_data=f"num_finish:{order_id}",
    )
    b.adjust(1)
    return b.as_markup()
