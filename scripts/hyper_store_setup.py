"""تهيئة مزود Hyper Store: اتصال، سحب الخدمات، فرز، نشر منتجات."""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "123456:DEV_TOKEN"))
os.environ.setdefault("BOT_USERNAME", os.environ.get("BOT_USERNAME", "hyperstore_bot"))
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("ADMIN_NOTIFY_CHAT_ID", "-1001")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./bot_database.db"),
)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
os.environ.setdefault("INVENTORY_ENCRYPTION_KEY", "dev-encryption-key-not-for-prod")

from database.seed import init_db  # noqa: E402
from database.engine import async_session_maker  # noqa: E402
from database.models import (  # noqa: E402
    ApiProvider,
    ApiProviderType,
    ApiProtocolType,
    Category,
    CategoryType,
    SubCategory,
    ProviderService,
    ProviderServiceStatus,
    Product,
)
from services.provider_sync_service import ProviderSyncService  # noqa: E402
from services.dynamic_service import DynamicService  # noqa: E402
from services.settings_service import SettingsService  # noqa: E402
from services.service_localization_service import service_name_ar  # noqa: E402
from services.i18n_service import I18nService  # noqa: E402

TOKEN = os.environ.get("HYPER_STORE_TOKEN", "")
if not TOKEN:
    raise SystemExit("⚠️ ضع التوكن في متغير البيئة HYPER_STORE_TOKEN قبل التشغيل.")
API_URL = os.environ.get("HYPER_STORE_API_URL", "https://api.hyper4store.com")
MARGIN = Decimal(os.environ.get("HYPER_STORE_MARGIN", "10"))

ROOT_DEFS = {
    CategoryType.GAMES: ("شحن الألعاب", "🎮"),
    CategoryType.APPS: ("برامج ودعم ولايفات", "📦"),
    CategoryType.BALANCES: ("رصيد سيرياتيل وMTN", "📱"),
}

GAME_HINTS = [
    "pubg", "ببجي", "بوبجي", "شدة", "free fire", "فري فاير", "mobile legend",
    "موبايل ليجند", "jawaker", "جواكر", "8ball", "ball pool", "delta force",
    "lords", "blood strike", "age of empires", "efootball", "كلاش", "clash",
    "brawl", "ludo", "لودو", "yalla ludo", "zepeto", "asphalt", "arena breakout",
    "arknights", "valor", "identity v", "honor of king", "king shot",
    "whiteout", "marvel", "phantom", "super sus", "stumble", "genshin",
    "honkai", "PUBG", "شدة لعب", "شحن", "freefire",
]
BALANCE_HINTS = [
    "syriatel", "سيرياتيل", "mtn", "شام كاش", "sham", "الكاش", "wallet",
    "فودافون كاش", "alfa", "touch", "رصيد", "بابرا", "instapay", "insta pay",
]
APP_HINTS = [
    "live", "لايف", "chat", "شات", "mixu", "momo", "social", "vpn", "proxy",
    "design", "تصميم", "برمج", "programming", "design", "payment", "دفع",
    "subscription", "اشتراك", "ai ", "ذكاء", "internet", "انترنت", "تليجرام",
    "telegram", "vip", "บัตร",
]


def _lower(s) -> str:
    return (s or "").lower()


def classify(service: ProviderService) -> CategoryType | None:
    text = " ".join(
        _lower(v)
        for v in (service.name, service.name_ar, service.category, service.description)
        if v
    )
    if any(h.lower() in text for h in BALANCE_HINTS):
        return CategoryType.BALANCES
    if any(h.lower() in text for h in GAME_HINTS):
        return CategoryType.GAMES
    if any(h.lower() in text for h in APP_HINTS):
        return CategoryType.APPS
    return None


async def _ensure_root_category(session, ctype: CategoryType) -> Category:
    result = await session.execute(
        __import__("sqlalchemy").select(Category).where(Category.type == ctype)
    )
    category = result.scalar_one_or_none()
    if category is not None:
        return category
    name, emoji = ROOT_DEFS[ctype]
    category = Category(name_ar=name, emoji=emoji, type=ctype, is_active=True, sort_order=100)
    session.add(category)
    await session.flush()
    return category


async def _ensure_subcategory(session, category: Category, service: ProviderService) -> SubCategory:
    name = (service.category or service.name)[:64]
    result = await session.execute(
        __import__("sqlalchemy").select(SubCategory).where(
            SubCategory.category_id == category.id,
            SubCategory.name_ar == name,
        )
    )
    sub = result.scalar_one_or_none()
    if sub is not None:
        return sub
    sub = SubCategory(category_id=category.id, name_ar=name, emoji="📦")
    session.add(sub)
    await session.flush()
    return sub


async def main() -> None:
    await init_db()

    async with async_session_maker() as session:
        provider = ApiProvider(
            name="HyperStore",
            type=ApiProviderType.GAMES,
            protocol_type=ApiProtocolType.CUSTOM,
            api_url=API_URL,
            api_key=TOKEN,
            currency="USD",
            rate_to_usd=Decimal("1"),
            is_active=True,
        )
        provider.custom_config = json.dumps({"engine": "hyper_store"})
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        provider_id = provider.id
        print(f"✅ Provider created: id={provider_id}, type={provider.type.value}, "
              f"protocol={provider.protocol_type.value}, url={provider.api_url}")

    from services.provider_sync_service import ProviderSyncService as PS

    print("⏳ اختبار الاتصال...")
    async with async_session_maker() as session:
        provider = await session.get(ApiProvider, provider_id)
        ok, msg, balance, currency = await PS.test_provider_connection(provider)
        print(f"{'✅' if ok else '❌'} {msg} — الرصيد: {balance} {currency}")

    print("⏳ سحب الخدمات...")
    result = await PS.sync_provider_services(provider_id)
    print(result.summary())

    print("⏳ تصنيف المنتجات تلقائياً...")
    from sqlalchemy import select

    async with async_session_maker() as session:
        services = (
            await session.execute(
                select(ProviderService).where(
                    ProviderService.api_provider_id == provider_id,
                    ProviderService.status == ProviderServiceStatus.ACTIVE,
                )
            )
        ).scalars().all()
        by_group: dict[CategoryType, list[ProviderService]] = {
            CategoryType.GAMES: [],
            CategoryType.APPS: [],
            CategoryType.BALANCES: [],
        }
        unclassified = []
        for svc in services:
            group = classify(svc)
            if group is None:
                unclassified.append(svc.name)
            else:
                by_group[group].append(svc)
        print(f"• ألعاب: {len(by_group[CategoryType.GAMES])}")
        print(f"• برامج/لايفات/شات: {len(by_group[CategoryType.APPS])}")
        print(f"• أرصدة: {len(by_group[CategoryType.BALANCES])}")
        print(f"• غير مصنّف: {len(unclassified)}")

        from database.models import CategoryType as CT

        for ctype, svcs in by_group.items():
            if not svcs:
                continue
            category = await _ensure_root_category(session, ctype)
            sub_cache: dict[str, SubCategory] = {}
            for svc in svcs:
                sub_name = (svc.category or svc.name)[:64]
                if sub_name not in sub_cache:
                    from sqlalchemy import select as _sel
                    res = await session.execute(
                        _sel(SubCategory).where(
                            SubCategory.category_id == category.id,
                            SubCategory.name_ar == sub_name,
                        )
                    )
                    sub = res.scalar_one_or_none()
                    if sub is None:
                        sub = SubCategory(category_id=category.id, name_ar=sub_name, emoji="📦")
                        session.add(sub)
                        await session.flush()
                    sub_cache[sub_name] = sub
                subcategory = sub_cache[sub_name]
                cost = svc.rate_usd or Decimal("0")
                sell = (cost * (Decimal("1") + MARGIN / Decimal("100"))).quantize(
                    Decimal("0.0001")
                )
                if sell <= 0:
                    sell = Decimal("0.0001")
                product_name = service_name_ar(svc) or svc.name
                try:
                    await DynamicService.create_product(
                        session=session,
                        sub_category_id=subcategory.id,
                        name_ar=product_name[:128],
                        description=(svc.description or f"منتج مستورد تلقائياً من HyperStore")[:500],
                        price_usd=sell,
                        cost_price_usd=cost,
                        api_provider_id=provider_id,
                        provider_service_id=svc.external_service_id,
                        provider_service_ref_id=svc.id,
                        fulfillment_type=__import__("database.models", fromlist=["ProductFulfillmentType"]).ProductFulfillmentType.API,
                        min_quantity=svc.min_quantity or 1,
                        max_quantity=svc.max_quantity or 1,
                        requires_link=svc.requires_link,
                        requires_player_id=svc.requires_player_id,
                        requires_quantity=svc.requires_quantity,
                        display_type=__import__("database.models", fromlist=["ProductDisplayType"]).ProductDisplayType.PER_UNIT,
                    )
                except Exception as e:
                    print(f"⚠️ فشل إنشاء منتج {svc.external_service_id}: {e}")
            await session.commit()
            print(f"✅ تم نشر {len(svcs)} منتج في {ROOT_DEFS[ctype][1]} {ROOT_DEFS[ctype][0]}")

    print("🎉 انتهى الإعداد. الأسعار = تكلفة × (1 + " + str(MARGIN) + "%)")


if __name__ == "__main__":
    asyncio.run(main())
