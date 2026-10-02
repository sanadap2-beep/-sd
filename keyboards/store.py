"""Keyboards for the universal store hub."""

from decimal import Decimal

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.i18n_service import I18nService

from keyboards.style_utils import style_for_callback


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


def _chunk(builder, buttons: list, size: int = 2) -> None:
    for i in range(0, len(buttons), size):
        builder.row(*buttons[i:i + size])


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

    from services.button_customization_service import ButtonCustomizationService as BC

    def _bc(action: str, default_label: str, style=None, web_app=None, url=None):
        mapping = {
            "num_hub": "store.numbers",
            "webapp": "store.webapp",
            "menu:search": "store.search",
            "menu:cart": "store.cart",
            "menu:product_request": "store.product_request",
        }
        if action.startswith("store:section:"):
            mapping[action] = f"store.{action.rsplit(':', 1)[-1]}"
        key = mapping.get(action)
        if not key:
            btn = InlineKeyboardButton(text=default_label, callback_data=None if web_app or url else action,
                                       style=style, web_app=web_app, url=url)
            return btn
        return BC.apply(key, default_label, None if web_app or url else action, style, url=url, web_app=web_app)

    if entries is not None:
        section_rows: list[list] = []
        category_rows: list[list] = []
        url_rows: list[list] = []
        other_rows: list[list] = []
        num_hub_btn = None
        webapp_btn = None

        for entry in entries:
            default_label = entry.label or I18nService.t("menu_numbers", language)
            if entry.action == "num_hub":
                num_hub_btn = _bc("num_hub", default_label, "success")
            elif entry.action == "webapp":
                if webapp_url:
                    webapp_btn = _bc("webapp", default_label or I18nService.t("store_webapp", language), "success",
                                     web_app=WebAppInfo(url=webapp_url))
            elif entry.is_url:
                url_rows.append(_bc(entry.action, default_label, None, url=entry.action))
            elif entry.action.startswith("cat:"):
                category_rows.append(_bc(entry.action, default_label, "success"))
            elif entry.action.startswith("store:section:"):
                section_rows.append(_bc(entry.action, default_label, "success"))
            else:
                _style = style_for_callback(entry.action, default_label or "")
                other_rows.append(_bc(entry.action, default_label, _style))

        # التصميم الجديد: زر رئيسي عريض ← أقسام 2×2 ← منتجات/فئات 2×2 ← أدوات
        if num_hub_btn is not None:
            b.row(num_hub_btn)
        _chunk(b, section_rows, size=2)
        _chunk(b, category_rows, size=2)
        _chunk(b, other_rows if other_rows else [], size=2)
        for btn in url_rows:
            b.row(btn)
        b.row(InlineKeyboardButton(text="📦 التطبيقات والأكواد الجاهزة", callback_data="readycode:list", style="success"))
        if webapp_btn is not None:
            b.row(webapp_btn)
        b.row(InlineKeyboardButton(text=_main_menu_label(language), callback_data="back_to_main"))
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
        b.add(_bc(f"store:section:{key}", section_label(key, language), "success"))

    b.add(_bc("menu:search", I18nService.t("store_search", language), "success"))
    b.add(_bc("menu:cart", I18nService.t("store_cart", language), "primary"))
    b.add(_bc("menu:product_request", I18nService.t("store_product_request", language), "success"))
    if webapp_url:
        b.add(_bc("webapp", I18nService.t("store_webapp", language), None, web_app=WebAppInfo(url=webapp_url)))
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
    price_map=None,
) -> InlineKeyboardMarkup:
    rows = []
    for product in products:
        name = product.name_ar if len(product.name_ar) <= 36 else product.name_ar[:35] + "…"
        if price_map is not None and product.id in price_map:
            price = price_map[product.id]
        else:
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
