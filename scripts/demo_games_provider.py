#!/usr/bin/env python3
"""عرض حي: مزود شحن ألعاب من الإضافة حتى ظهوره للزبون بالعربية.

يُشغَّل يدوياً (بلا شبكة وبلا لمس قاعدة بيانات البوت الحقيقية):

    python scripts/demo_games_provider.py

ماذا يعمل (كله كود المشروع الحقيقي، لا نسخ منه):
1. يحفظ مزود ألعاب كما يحفظه معالج «🔌 مزودو المتجر».
2. يسحب خدماته عبر ``ProviderSyncService`` (البروتوكول مستبدَل بمزود وهمي
   يرجع كتالوج ألعاب، فلا يحتاج internet ولا مفتاحاً حقيقياً).
3. يطبع الكتالوج: الاسم الأصلي ← الاسم العربي المحفوظ وقت السحب.
4. يطبع شاشات الأدمن الفعلية: قائمة خدمات المزود، تفاصيل الخدمة، شاشة النشر.
5. ينشئ قسمين باختيارك وينشر خدمتين في أحدهما، ثم يطبع ما يراه الزبون
   في كل قسم.

قاعدة البيانات تجريبية في /tmp وتُحذف عند كل تشغيل.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ── إعدادات تجريبية قبل استيراد أي شيء من المشروع ──
DEMO_DB = "/tmp/games-provider-demo.db"
if pathlib.Path(DEMO_DB).exists():
    pathlib.Path(DEMO_DB).unlink()

os.environ.setdefault("BOT_TOKEN", "123456:DEMO_TOKEN_FOR_LOCAL_DEMO_123456")
os.environ.setdefault("BOT_USERNAME", "demo_bot")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("ADMIN_NOTIFY_CHAT_ID", "-1001")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DEMO_DB}"

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())

from database.engine import async_session_maker  # noqa: E402
from database.models import (  # noqa: E402
    ApiProvider,
    ApiProtocolType,
    ApiProviderType,
    CategoryType,
    ProviderService,
    SubCategory,
)
from database.seed import init_db  # noqa: E402
from protocols.base import ProtocolService  # noqa: E402
from protocols.factory import ProtocolFactory  # noqa: E402
from services.dynamic_service import DynamicService  # noqa: E402
from services.provider_sync_service import ProviderSyncService  # noqa: E402
from services.pulled_services_service import PulledServicesService  # noqa: E402


# ══════════════ مزود ألعاب وهمي (بدل نداء HTTP حقيقي) ══════════════

GAMES_CATALOG = (
    ProtocolService(
        external_id="1001",
        name="PUBG Mobile 60 UC",
        category="Games",
        service_type="PUBG",
        rate=Decimal("0.90"),
        min_quantity=1,
        max_quantity=1,
        requires_link=False,
        requires_quantity=False,
        requires_player_id=True,
    ),
    ProtocolService(
        external_id="1002",
        name="PUBG Mobile 325 UC",
        category="Games",
        service_type="PUBG",
        rate=Decimal("4.20"),
        min_quantity=1,
        max_quantity=1,
        requires_link=False,
        requires_quantity=False,
        requires_player_id=True,
    ),
    ProtocolService(
        external_id="1003",
        name="Free Fire 100 Diamonds",
        category="Games",
        service_type="Free Fire",
        rate=Decimal("0.80"),
        min_quantity=1,
        max_quantity=1,
        requires_link=False,
        requires_quantity=False,
        requires_player_id=True,
    ),
    ProtocolService(
        external_id="1004",
        name="Google Play Gift Card 25 USD",
        category="Cards",
        rate=Decimal("25.00"),
        min_quantity=1,
        max_quantity=1,
        requires_link=False,
        requires_quantity=False,
        requires_player_id=False,
    ),
)


class FakeGamesProtocol:
    async def test_connection(self) -> bool:
        return True

    async def get_balance(self):
        return None

    async def get_services(self) -> list[ProtocolService]:
        return list(GAMES_CATALOG)


class DemoMessage:
    def __init__(self):
        self.edits: list[tuple[str, object]] = []

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))

    async def answer(self, text, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return None


class DemoCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = DemoMessage()

    async def answer(self, text=None, show_alert=False, **kwargs):
        return None


class DemoState:
    def __init__(self):
        self.data: dict = {}
        self.state = None

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, state=None):
        self.state = state

    async def clear(self):
        self.data = {}
        self.state = None


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"</?[a-zA-Z/][^>]*>", "", text)


def _buttons(markup) -> list[str]:
    return [
        button.text or ""
        for row in markup.inline_keyboard
        for button in row
    ]


def rule(title: str) -> None:
    print("\n" + "═" * 74)
    print(title)
    print("═" * 74)


async def main() -> None:
    await init_db()

    # ── 1) إضافة المزود (كما بعد خطوة «🧪 اختبار الاتصال + حفظ») ──
    ProtocolFactory.create_from_provider = lambda provider: FakeGamesProtocol()

    async with async_session_maker() as session:
        provider = ApiProvider(
            name="GamesWholesale",
            type=ApiProviderType.GAMES,
            protocol_type=ApiProtocolType.GAMES_GENERIC,
            api_url="https://games-provider.example/api",
            api_key="demo-key",
            currency="USD",
            rate_to_usd=Decimal("1"),
            is_active=True,
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        provider_id = provider.id

    rule("1) المزود المحفوظ (🔌 مزودو المتجر ← ➕ إضافة مزود جديد)")
    async with async_session_maker() as session:
        saved = await session.get(ApiProvider, provider_id)
        print(f"الاسم          : {saved.name}")
        print(f"النوع          : {saved.type.value}  (🎮 مزود ألعاب)")
        print(f"البروتوكول     : {saved.protocol_type.value}")
        print(f"الرابط/المفتاح : {saved.api_url} / {'*' * 8}")

    # ── 2) سحب الخدمات (🔄 مزامنة الخدمات) ──
    result = await ProviderSyncService.sync_provider_services(provider_id)

    rule("2) سحب الخدمات — الاسم الأصلي ← الاسم العربي (يُبنى وقت السحب)")
    print(
        f"المزامنة: نجحت={result.success} · مسحوبة={result.total_fetched} "
        f"· جديدة={result.new_services}"
    )
    async with async_session_maker() as session:
        rows = (
            (await session.execute(select_provider_services())).scalars().all()
        )
    print(f"{'ID':<6} {'الاسم عند المزود':<32} {'ما يراه الزبون':<32} {'التكلفة'}")
    print("-" * 84)
    for row in rows:
        print(
            f"{row.external_service_id:<6} {row.name:<32} {row.name_ar:<32} "
            f"{row.rate_usd}$"
        )

    # ── 3) شاشات الأدمن الفعلية ──
    from handlers.admin.api_providers import aprov_service_view
    from keyboards.admin_providers_v2 import provider_services_kb

    async with async_session_maker() as session:
        markup = provider_services_kb(
            provider_id=provider_id,
            services=rows,
            current_page=0,
            total_count=len(rows),
        )
        detail_callback = DemoCallback(f"admin:aprov_svc:{rows[0].id}")
        await aprov_service_view(detail_callback, session)
        detail_text = _strip_html(detail_callback.message.edits[-1][0])

    rule("3) شاشة «📦 خدمات المزود» (الأزرار كما تراها)")
    for label in _buttons(markup):
        print("  •", label)

    rule("4) شاشة تفاصيل الخدمة (عربي + الأصلي للمطابقة)")
    print(detail_text.strip())

    # ── 5) الفرز في القسم الذي تختاره ──
    async with async_session_maker() as session:
        category = await DynamicService.create_category(
            session, "شحن الألعاب", "🎮", CategoryType.GAMES
        )
        pubg_section = await DynamicService.create_sub_category(
            session, category.id, "🔫 شحن ببجي", "🔫",
            description="باقات UC",
        )
        ff_section = await DynamicService.create_sub_category(
            session, category.id, "🔥 شحن فري فاير", "🔥"
        )

        destinations = await PulledServicesService.destination_subcategories(session)
        destinations_count = len(destinations)
        publish_callback = DemoCallback(f"ps:pb:{rows[0].id}")
        from handlers.admin.pulled_services import pulled_publish_start

        await pulled_publish_start(publish_callback, session, DemoState())
        publish_text, publish_markup = publish_callback.message.edits[-1]

        for row in rows:
            if row.external_service_id in {"1001", "1002"}:
                await PulledServicesService.publish(
                    session, row, pubg_section.id, Decimal("1.50")
                )

        mine = await DynamicService.get_active_products(session, pubg_section.id)
        theirs = await DynamicService.get_active_products(session, ff_section.id)
        names = {sub.name_ar for sub in (await session.execute(select_subs())).scalars().all()}

    rule("5) الأقسام المتاحة للنشر (كل الأقسام التي أنشأتها)")
    print(f"عدد الوجهات المتاحة: {destinations_count}")
    print("  •", " | ".join(sorted(names)))

    rule("6) شاشة «📤 نشر في البوت» لخدمة ألعاب")
    print(_strip_html(publish_text).strip())
    print("  الأزرار:", " | ".join(_buttons(publish_markup)[:4]))

    rule("7) ما يراه الزبون داخل قسمك (DynamicService.get_active_products)")
    print(f"📂 {pubg_section.name_ar}:")
    for product in mine:
        print(
            f"  • {product.name_ar} — {product.price_usd}$ "
            f"(التكلفة {product.cost_price_usd}$ · Player ID: "
            f"{'نعم' if product.requires_player_id else 'لا'})"
        )
    print(f"📂 {ff_section.name_ar}: "
          f"{'(فارغ — لم تنشر فيه شيئاً)' if not theirs else theirs}")

    rule("الخلاصة")
    print(
        "المزود أُضيف ← خدماته سُحبت بأسماء عربية ← نُشرت في القسم الذي "
        "اخترته أنت ← والقسم الآخر بقي فارغاً."
    )


def select_provider_services():
    from sqlalchemy import select

    return select(ProviderService).order_by(ProviderService.rate_usd)


def select_subs():
    from sqlalchemy import select

    return select(SubCategory)


if __name__ == "__main__":
    import logging

    logging.disable(logging.INFO)
    asyncio.run(main())
