"""Keyboards for the universal store hub."""

from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.i18n_service import I18nService


# Kept as a public mapping for existing callers that validate section names.
SECTION_LABELS = {
    "featured": "⭐ مختارات المتجر",
    "deals": "🔥 عروض اليوم",
    "bestsellers": "🏆 الأكثر مبيعاً",
    "instant": "⚡ تسليم فوري",
    "cheap": "💸 أقل من 2$",
    "games": "🎮 ألعاب",
    "smm": "📈 سوشيال ميديا",
    "apps": "📦 تطبيقات واشتراكات",
}


def section_label(section: str, language: str = "ar") -> str:
    """Return a translated label while retaining Arabic fallback behavior."""
    return I18nService.t(f"store_section_{section}", language)


def _main_menu_label(language: str = "ar") -> str:
    return "🏠 القائمة الرئيسية" if I18nService.normalize_language(language) == "ar" else "🏠 Main menu"


def store_home_kb(
    number_services=None,
    categories=None,
    webapp_url: str | None = None,
    language: str = "ar",
    entries=None,
) -> InlineKeyboardMarkup:
    """Page one of the store, built from admin-controlled entries.

    ``entries`` is the admin-controlled list (StoreSectionService). When it
    is provided it fully owns what appears on the page — including a
    single «الأرقام» hub button that opens every number service
    (واتساب/تيليجرام/أي قسم جديد يُنشأ من لوحة الأدمن).

    Without entries it falls back to the legacy layout (all number services
    + categories + fixed smart sections) so existing callers keep working.
    """
    b = InlineKeyboardBuilder()

    if entries is not None:
        for entry in entries:
            if entry.action == "num_hub":
                b.button(
                    text=entry.label or I18nService.t("menu_numbers", language),
                    callback_data="num_hub", style="success",
                )
            elif entry.action == "webapp":
                if webapp_url:
                    b.button(
                        text=entry.label or I18nService.t("store_webapp", language),
                        web_app=WebAppInfo(url=webapp_url),
                    )
            elif entry.is_url:
                b.button(text=entry.label, url=entry.action)
            else:
                b.button(text=entry.label, callback_data=entry.action)
        b.button(text=_main_menu_label(language), callback_data="back_to_main")
        b.adjust(2)
        return b.as_markup()

    # ── التخطيط القديم (توافق مع الاستدعاءات السابقة) ──
    for service in number_services or []:
        service_name = service.name_ar
        if I18nService.normalize_language(language) == "en":
            service_name = {
                "telegram": "Telegram",
                "tg": "Telegram",
                "whatsapp": "WhatsApp",
                "wa": "WhatsApp",
            }.get(service.code.lower(), service_name)
        b.button(
            text=f"{service.emoji} {service_name}",
            callback_data=f"num_svc:{service.code}", style="success",
        )

    for category in categories or []:
        b.button(
            text=f"{category.emoji} {category.name_ar}",
            callback_data=f"cat:{category.id}", style="success",
        )

    for key in SECTION_LABELS:
        b.button(text=section_label(key, language), callback_data=f"store:section:{key}", style="success")

    b.button(text=I18nService.t("store_search", language), callback_data="menu:search", style="success")
    b.button(text=I18nService.t("store_cart", language), callback_data="menu:cart", style="primary")
    b.button(
        text=I18nService.t("store_product_request", language),
        callback_data="menu:product_request",
    )
    if webapp_url:
        b.button(
            text=I18nService.t("store_webapp", language),
            web_app=WebAppInfo(url=webapp_url),
        )
    b.button(text=_main_menu_label(language), callback_data="back_to_main")
    b.adjust(2)
    return b.as_markup()


def store_empty_section_kb(language: str = "ar") -> InlineKeyboardMarkup:
    """Keyboard shown for empty smart store sections.

    Keep it intentionally small: the user should see that the section is empty,
    then choose either returning to store sections or leaving to the main menu.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=I18nService.t("store_back", language),
                    callback_data="store:home", style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text=_main_menu_label(language),
                    callback_data="back_to_main",
                )
            ],
        ]
    )


def store_section_servers_kb(
    section: str,
    servers,
    language: str = "ar",
) -> InlineKeyboardMarkup:
    """اختيار سيرفر عام لقسم المتجر الذكي (ألعاب/تطبيقات/رشق...)."""
    b = InlineKeyboardBuilder()
    for s in servers:
        margin_label = ""
        if s.margin_percent is not None:
            margin_label = f"  ({s.margin_percent}%)"
        b.button(
            text=f"{s.emoji} {s.name_ar}{margin_label}",
            callback_data=f"store_svc_pick:{section}:{s.id}", style="success",
        )
    b.button(
        text=I18nService.t("store_back", language),
        callback_data="store:home", style="success",
    )
    b.adjust(1)
    return b.as_markup()


def _server_price(product, server) -> Decimal:
    """سعر الوحدة بعد هامش السيرفر (لفرضة عرض ثابتة فقط)."""
    if server is None or server.margin_percent is None:
        return product.price_usd
    cost = getattr(product, "cost_price_usd", None)
    if cost is None or Decimal(str(cost or 0)) <= 0:
        return product.price_usd
    return (Decimal(str(cost)) * (Decimal("100") + Decimal(str(server.margin_percent))) / Decimal("100")).quantize(Decimal("0.0001"))


def store_products_kb(
    products,
    section: str,
    language: str = "ar",
    server=None,
) -> InlineKeyboardMarkup:
    rows = []
    for product in products:
        name = product.name_ar if len(product.name_ar) <= 36 else product.name_ar[:35] + "…"
        price = _server_price(product, server)
        rows.append([
            InlineKeyboardButton(
                text=f"🛒 {name} · {price}$",
                callback_data=f"prod:{product.id}", style="success",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text=I18nService.t("store_back", language),
            callback_data="store:home", style="success",
        ),
        InlineKeyboardButton(
            text=I18nService.t("store_cart", language),
            callback_data="menu:cart", style="primary",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
