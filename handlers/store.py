"""Universal storefront for all products, not only SMS numbers."""

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import settings
from keyboards.store import (
    SECTION_LABELS,
    section_label,
    store_empty_section_kb,
    store_home_kb,
    store_products_kb,
)
from services.currency_service import CurrencyService
from services.feature_service import FeatureService
from services.i18n_service import I18nService
from services.store_discovery_service import StoreDiscoveryService
from services.dynamic_service import DynamicService
from providers.countries import get_active_number_services

router = Router(name="store")


@router.callback_query(F.data == "store:home")
async def store_home(callback: CallbackQuery, session, db_user):
    language = getattr(db_user, "language_code", "ar") or "ar"
    if not await StoreDiscoveryService.enabled():
        await callback.answer(I18nService.t("not_available", language), show_alert=True)
        return

    number_services = await get_active_number_services(session)
    categories = await DynamicService.get_active_categories(session)
    overview = await StoreDiscoveryService.overview(session)
    counts = overview["type_counts"]
    lines = [
        I18nService.t("menu_full_store", language),
        "",
        I18nService.t("store_choose_service", language),
        I18nService.t("store_numbers_count", language, count=len(number_services)),
        I18nService.t("store_games_count", language, count=counts.get("games", 0)),
        I18nService.t("store_smm_count", language, count=counts.get("smm", 0)),
        I18nService.t("store_apps_count", language, count=counts.get("apps", 0)),
        I18nService.t("store_total_count", language, count=overview["total_products"]),
    ]
    # الأزرار تُبنى من التحكم المركزي بالأدمن: أي زر يُطفأ من
    # «🛍 التحكم بالمتجر» يختفي هنا فوراً، وأي قسم جديد يُضاف يظهر.
    from services.store_section_service import StoreEntry, StoreSectionService

    entries = [
        entry
        for entry in await StoreSectionService.list_entries(include_inactive=True)
        if entry.is_active
    ]
    # الأقسام الديناميكية تظهر بعد الأقسام الذكية (حسب ترتيبها في الإدارة).
    for category in categories:
        entries.append(
            StoreEntry(
                key=f"cat:{category.id}",
                label=f"{category.emoji} {category.name_ar}",
                action=f"cat:{category.id}",
                is_active=True,
                sort_order=40 + min(max(category.sort_order, 0), 55),
                is_builtin=True,
            )
        )
    entries.sort(key=lambda item: (item.sort_order, item.key))
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=store_home_kb(entries=entries, webapp_url=settings.WEBAPP_URL, language=language),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("store:section:"))
async def store_section(callback: CallbackQuery, session, db_user):
    language = getattr(db_user, "language_code", "ar") or "ar"
    section = callback.data.rsplit(":", 1)[1]
    if section not in SECTION_LABELS:
        await callback.answer(I18nService.t("not_available", language), show_alert=True)
        return
    if not await FeatureService.enabled("full_store_hub"):
        await callback.answer(I18nService.t("not_available", language), show_alert=True)
        return

    products = await StoreDiscoveryService.products(session, section)
    if not products:
        await callback.message.edit_text(
            f"{section_label(section, language)}\n\n"
            f"{I18nService.t('store_no_products', language)}",
            reply_markup=store_empty_section_kb(language),
        )
        await callback.answer()
        return

    lines = [section_label(section, language), ""]
    for index, product in enumerate(products, start=1):
        price = await CurrencyService.format_dual(product.price_usd, db_user, session)
        category = product.sub_category.category if product.sub_category else None
        sub = product.sub_category.name_ar if product.sub_category else (
            "General" if language == "en" else "عام"
        )
        stock = await StoreDiscoveryService.instant_stock_count(session, product)
        badges = []
        if product.is_featured:
            badges.append("⭐")
        if product.is_bestseller:
            badges.append("🏆")
        if stock is not None:
            stock_label = "Stock" if language == "en" else "مخزون"
            badges.append(f"⚡ {stock_label}: {stock}")
        badge_text = " ".join(badges)
        lines.append(
            f"{index}. <b>{escape(product.name_ar)}</b> {badge_text}\n"
            f"   {category.emoji if category else '📦'} {escape(sub)} · {price}"
        )

    lines.append(f"\n{I18nService.t('store_products_hint', language)}")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=store_products_kb(products, section, language),
    )
    await callback.answer()