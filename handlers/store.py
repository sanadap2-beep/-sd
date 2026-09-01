"""Universal storefront for all products, not only SMS numbers."""

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from keyboards.store import SECTION_LABELS, store_home_kb, store_products_kb
from services.currency_service import CurrencyService
from services.feature_service import FeatureService
from services.store_discovery_service import StoreDiscoveryService

router = Router(name="store")


@router.callback_query(F.data == "store:home")
async def store_home(callback: CallbackQuery, session, db_user):
    if not await StoreDiscoveryService.enabled():
        await callback.answer("واجهة المتجر الشاملة غير مفعّلة حالياً.", show_alert=True)
        return

    overview = await StoreDiscoveryService.overview(session)
    counts = overview["type_counts"]
    lines = [
        "🛍 <b>المتجر الشامل</b>",
        "",
        "هنا كل منتجات البوت بمكان واحد، وليس الأرقام فقط:",
        f"🎮 ألعاب: <b>{counts.get('games', 0)}</b>",
        f"📈 سوشيال ميديا: <b>{counts.get('smm', 0)}</b>",
        f"📦 تطبيقات واشتراكات: <b>{counts.get('apps', 0)}</b>",
        f"🛒 إجمالي المنتجات: <b>{overview['total_products']}</b>",
        "",
        "اختر قسماً سريعاً:",
    ]
    await callback.message.edit_text("\n".join(lines), reply_markup=store_home_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("store:section:"))
async def store_section(callback: CallbackQuery, session, db_user):
    section = callback.data.rsplit(":", 1)[1]
    if section not in SECTION_LABELS:
        await callback.answer("قسم غير معروف.", show_alert=True)
        return
    if not await FeatureService.enabled("full_store_hub"):
        await callback.answer("واجهة المتجر الشاملة غير مفعّلة حالياً.", show_alert=True)
        return

    products = await StoreDiscoveryService.products(session, section)
    if not products:
        await callback.message.edit_text(
            f"{SECTION_LABELS[section]}\n\nلا توجد منتجات متاحة هنا حالياً.",
            reply_markup=store_home_kb(),
        )
        await callback.answer()
        return

    lines = [f"{SECTION_LABELS[section]}", ""]
    for index, product in enumerate(products, start=1):
        price = await CurrencyService.format_dual(product.price_usd, db_user, session)
        category = product.sub_category.category if product.sub_category else None
        sub = product.sub_category.name_ar if product.sub_category else "عام"
        stock = await StoreDiscoveryService.instant_stock_count(session, product)
        badges = []
        if product.is_featured:
            badges.append("⭐")
        if product.is_bestseller:
            badges.append("🏆")
        if stock is not None:
            badges.append(f"⚡ مخزون: {stock}")
        badge_text = " ".join(badges)
        lines.append(
            f"{index}. <b>{escape(product.name_ar)}</b> {badge_text}\n"
            f"   {category.emoji if category else '📦'} {escape(sub)} · {price}"
        )

    lines.append("\nاضغط على المنتج لعرض التفاصيل والشراء أو إضافته للسلة.")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=store_products_kb(products, section),
    )
    await callback.answer()
