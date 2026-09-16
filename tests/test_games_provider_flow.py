"""مزود شحن ألعاب: إضافة ← سحب ← فرز في قسم يختاره الأدمن ← أسماء عربية.

يشغّل المسار الحقيقي بلا شبكة:
- ``ProtocolFactory.create_from_provider`` مستبدَل ببروتوكول وهمي يرجع
  خدمات ألعاب، فتعمل ``ProviderSyncService.sync_provider_services`` كما هي.
- التعريب لحظة السحب (``ProviderService.name_ar``).
- ``PulledServicesService.publish`` إلى **القسم الفرعي الذي أنشأه الأدمن**،
  والتحقق أن القسم الآخر بقي فارغاً.
- شاشة المتجر نفسها: ``DynamicService.get_active_products`` لقسم الأدمن.
- البحث بالعربية داخل كتالوج المزود.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ApiProtocolType,
    ApiProviderType,
    CategoryType,
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProviderService,
    SubCategory,
    UnifiedOrder,
    User,
)
from protocols.base import ProtocolService
from protocols.factory import ProtocolFactory
from services.dynamic_service import DynamicService
from states.states import GamesOrderStates
from services.provider_sync_service import ProviderSyncService
from services.pulled_services_service import PulledServicesService
from services.service_localization_service import is_arabic


class FakeState:
    """بديل FSMContext: يكفي update_data/get_data/set_state/clear."""

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
        raw={"sku": "pubg-60"},
    ),
    ProtocolService(
        external_id="1002",
        name="Free Fire 100 Diamonds",
        category="Games",
        service_type="Free Fire",
        rate=Decimal("0.80"),
        min_quantity=1,
        max_quantity=1,
        requires_link=False,
        requires_quantity=False,
        requires_player_id=True,
        raw={"sku": "ff-100"},
    ),
    ProtocolService(
        external_id="1003",
        name="Google Play Gift Card 25 USD",
        category="Cards",
        service_type=None,
        rate=Decimal("25.00"),
        min_quantity=1,
        max_quantity=1,
        requires_link=False,
        requires_quantity=False,
        requires_player_id=False,
        raw={"sku": "gplay-25"},
    ),
)


class FakeTextMessage:
    """رسالة نصية من المستخدم (لإرسال Player ID)."""

    def __init__(self, text: str):
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append(text)
        return None

    async def delete(self):
        return None


async def _noop(*args, **kwargs):
    return None


class FakeGamesProtocol:
    """بروتوكول ألعاب وهمي: يعيد كتالوجاً ثابتاً بدل نداء HTTP."""

    def __init__(self):
        self.services_calls = 0

    async def test_connection(self) -> bool:
        return True

    async def get_balance(self):
        return None

    async def get_services(self) -> list[ProtocolService]:
        self.services_calls += 1
        return list(GAMES_CATALOG)


@pytest.fixture
def games_protocol(monkeypatch):
    """يستبدل إنشاء البروتوكول فقط — بقية مسار السحب حقيقي."""
    fake = FakeGamesProtocol()
    monkeypatch.setattr(
        ProtocolFactory, "create_from_provider", lambda provider: fake
    )
    return fake


async def _seed_provider(name: str = "GamesWholesale") -> ApiProvider:
    """مزود ألعاب كما يحفظه معالج «🔌 مزودو المتجر» بعد خطوة الاختبار."""
    async with async_session_maker() as session:
        provider = ApiProvider(
            name=name,
            type=ApiProviderType.GAMES,
            protocol_type=ApiProtocolType.GAMES_GENERIC,
            api_url="https://games-provider.example/api",
            api_key="secret-key",
            currency="USD",
            rate_to_usd=Decimal("1"),
            is_active=True,
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        return provider


async def _pulled(session) -> dict[str, ProviderService]:
    rows = (await session.execute(select(ProviderService))).scalars().all()
    return {row.external_service_id: row for row in rows}


# ══════════════ 1) السحب يحفظ اسماً عربياً لكل خدمة ══════════════


async def test_sync_games_provider_stores_arabic_names(games_protocol):
    provider = await _seed_provider()

    result = await ProviderSyncService.sync_provider_services(provider.id)

    assert result.success is True, result.error_message
    assert result.total_fetched == 3
    assert result.new_services == 3
    assert games_protocol.services_calls == 1

    async with async_session_maker() as session:
        pulled = await _pulled(session)

    assert set(pulled) == {"1001", "1002", "1003"}
    # الاسم الأصلي يبقى كما وصل، والعربي يُبنى وقت السحب
    assert pulled["1001"].name == "PUBG Mobile 60 UC"
    assert pulled["1001"].name_ar == "ببجي موبايل 60 UC"
    assert pulled["1002"].name_ar == "فري فاير 100 ماسات"
    assert pulled["1003"].name_ar == "جوجل بلاي بطاقة هدية 25 دولار"
    for row in pulled.values():
        assert is_arabic(row.name_ar)
    # متطلبات شاشة الشراء تُنقل كما هي (Player ID لشحن الألعاب)
    assert pulled["1001"].requires_player_id is True
    assert pulled["1003"].requires_player_id is False


# ══════════════ 2) النشر يفرز المنتج في قسم الأدمن وحده ══════════════


async def test_publish_lands_in_the_section_the_admin_chose(games_protocol):
    provider = await _seed_provider()
    await ProviderSyncService.sync_provider_services(provider.id)

    async with async_session_maker() as session:
        # الأدمن أنشأ قسمه بنفسه من «📂 إدارة الأقسام»
        category = await DynamicService.create_category(
            session, "شحن الألعاب", "🎮", CategoryType.GAMES
        )
        pubg_section = await DynamicService.create_sub_category(
            session, category.id, "شحن ببجي", "🔫"
        )
        other_section = await DynamicService.create_sub_category(
            session, category.id, "شحن فري فاير", "🔥"
        )

        # قسمه يظهر ضمن وجهات النشر المتاحة
        destinations = await PulledServicesService.destination_subcategories(session)
        assert {pubg_section.id, other_section.id} <= {sub.id for sub in destinations}

        pulled = await _pulled(session)
        product = await PulledServicesService.publish(
            session, pulled["1001"], pubg_section.id, Decimal("1.50")
        )

        mine = await DynamicService.get_active_products(session, pubg_section.id)
        theirs = await DynamicService.get_active_products(session, other_section.id)

    assert [item.name_ar for item in mine] == ["ببجي موبايل 60 UC"]
    assert theirs == []  # لا يتسرّب المنتج لقسم آخر
    assert product.sub_category_id == pubg_section.id
    assert product.price_usd == Decimal("1.50")
    assert product.cost_price_usd == Decimal("0.90")
    assert product.fulfillment_type == ProductFulfillmentType.API
    # باقة ثابتة (ليست لكل 1000) + تحتاج Player ID
    assert product.display_type == ProductDisplayType.FIXED_TOTAL
    assert product.requires_player_id is True
    assert product.api_provider_id == provider.id
    assert product.provider_service_id == "1001"


# ══════════════ 3) كتالوج المزود يُبحث بالعربية ══════════════


async def test_provider_catalog_is_searchable_in_arabic(games_protocol):
    provider = await _seed_provider()
    await ProviderSyncService.sync_provider_services(provider.id)

    async with async_session_maker() as session:
        arabic_hits, arabic_total = await PulledServicesService.search_services(
            session, "ببجي", provider_id=provider.id
        )
        english_hits, _ = await PulledServicesService.search_services(
            session, "pubg", provider_id=provider.id
        )
        other_provider_hits, other_total = await PulledServicesService.search_services(
            session, "ببجي", provider_id=provider.id + 999
        )

    assert arabic_total == 1
    assert arabic_hits[0].external_service_id == "1001"
    assert english_hits[0].external_service_id == "1001"
    assert other_provider_hits == [] and other_total == 0


# ══════════════ 4) شاشات «مزودو المتجر» تعرض الاسم العربي ══════════════


class DummyMessage:
    def __init__(self):
        self.edits: list[tuple[str, object]] = []
        self.answers: list[tuple[str, object]] = []

    async def edit_text(self, text: str, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return None


class DummyBot:
    async def send_message(self, *args, **kwargs):
        return None

    async def send_document(self, *args, **kwargs):
        return None

    async def edit_message_text(self, *args, **kwargs):
        return None


class DummyCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = DummyMessage()
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs):
        self.answers.append((text, show_alert))


async def test_product_created_from_provider_screen_is_arabic(games_protocol):
    """زر «➕ إنشاء منتج من هذه الخدمة» كان يحفظ الاسم الإنجليزي للزبون."""
    from handlers.admin.api_providers import create_product_from_provider_service

    provider = await _seed_provider()
    await ProviderSyncService.sync_provider_services(provider.id)

    async with async_session_maker() as session:
        pulled = await _pulled(session)
        callback = DummyCallback(f"admin:aprov_create_product:{pulled['1001'].id}")
        await create_product_from_provider_service(callback, session)

        products = (await session.execute(select(Product))).scalars().all()

    assert len(products) == 1
    assert products[0].name_ar == "ببجي موبايل 60 UC"
    assert "PUBG" not in products[0].name_ar
    assert products[0].provider_service_id == "1001"
    assert products[0].requires_player_id is True


async def test_provider_screens_show_arabic_names(games_protocol):
    """قائمة خدمات المزود وتفاصيلها بالعربية (والأصلي للمطابقة فقط)."""
    from handlers.admin.api_providers import aprov_service_view
    from keyboards.admin_providers_v2 import provider_services_kb

    provider = await _seed_provider()
    await ProviderSyncService.sync_provider_services(provider.id)

    async with async_session_maker() as session:
        pulled = await _pulled(session)
        services = list(pulled.values())

        markup = provider_services_kb(
            provider_id=provider.id,
            services=services,
            current_page=0,
            total_count=len(services),
        )
        labels = [
            button.text or ""
            for row in markup.inline_keyboard
            for button in row
        ]

        callback = DummyCallback(f"admin:aprov_svc:{pulled['1003'].id}")
        await aprov_service_view(callback, session)
        detail = callback.message.edits[-1][0]

    assert any("ببجي موبايل 60 UC" in label for label in labels)
    assert any("جوجل بلاي بطاقة هدية 25 دولار" in label for label in labels)
    assert not any("PUBG Mobile" in label for label in labels)
    # التفاصيل: العربي بارز والأصلي سطر ثانوي، والتصنيف معرّب
    assert "جوجل بلاي بطاقة هدية 25 دولار" in detail
    assert "أصلي: Google Play Gift Card 25 USD" in detail
    assert "بطاقات" in detail or "ألعاب" in detail


async def test_auto_created_section_name_is_arabic(games_protocol):
    """«➕ إنشاء منتج» ينشئ القسم الفرعي باسم عربي («Games» → «ألعاب»)."""
    from handlers.admin.api_providers import create_product_from_provider_service

    provider = await _seed_provider()
    await ProviderSyncService.sync_provider_services(provider.id)

    async with async_session_maker() as session:
        pulled = await _pulled(session)
        await create_product_from_provider_service(
            DummyCallback(f"admin:aprov_create_product:{pulled['1001'].id}"), session
        )
        await create_product_from_provider_service(
            DummyCallback(f"admin:aprov_create_product:{pulled['1003'].id}"), session
        )

        subs = (await session.execute(select(SubCategory))).scalars().all()
        names = {sub.name_ar for sub in subs}
        by_name = {sub.name_ar: sub for sub in subs}
        products = (await session.execute(select(Product))).scalars().all()

    assert "ألعاب" in names and "بطاقات" in names
    assert "Games" not in names and "Cards" not in names
    # كل منتج في قسم تصنيفه عند المزود، لا في قسم واحد مشترك
    assert by_name["ألعاب"].id == next(
        p.sub_category_id for p in products if p.provider_service_id == "1001"
    )
    assert by_name["بطاقات"].id == next(
        p.sub_category_id for p in products if p.provider_service_id == "1003"
    )


async def test_publish_screen_offers_all_sections_for_a_games_service(games_protocol):
    """خدمة ألعاب لا تنتمي لمنصة رشق → شاشة النشر تعرض «كل الأقسام المتاحة»."""
    from handlers.admin.pulled_services import pulled_publish_start

    provider = await _seed_provider()
    await ProviderSyncService.sync_provider_services(provider.id)

    async with async_session_maker() as session:
        category = await DynamicService.create_category(
            session, "شحن الألعاب", "🎮", CategoryType.GAMES
        )
        await DynamicService.create_sub_category(
            session, category.id, "شحن ببجي", "🔫"
        )
        pulled = await _pulled(session)

        callback = DummyCallback(f"ps:pb:{pulled['1001'].id}")
        await pulled_publish_start(callback, session, FakeState())
        text, markup = callback.message.edits[-1]
        labels = [
            button.text or ""
            for row in markup.inline_keyboard
            for button in row
        ]

    assert "اختر القسم الذي سيظهر فيه المنتج" in text
    assert any("شحن ببجي" in label for label in labels)
    # اسم الخدمة معروض بالعربية في شاشة النشر
    assert "ببجي موبايل 60 UC" in text


# ══════════════ 5) البيع الفعلي: Player ID ثم تنفيذ الطلب ══════════════


class OrderProtocol(FakeGamesProtocol):
    """بروتوكول يسجّل الطلبات المرسلة للمزود."""

    def __init__(self):
        super().__init__()
        self.orders: list[dict] = []

    async def place_order(self, service_id, target, quantity, **kwargs):
        from protocols.base import ProtocolOrder

        self.orders.append(
            {"service_id": service_id, "target": target, "quantity": quantity}
        )
        return ProtocolOrder(
            external_order_id=f"ord-{len(self.orders)}", status="processing"
        )

    async def check_order_status(self, external_order_id):
        return None


async def test_games_purchase_collects_player_id_and_places_order(games_protocol, monkeypatch):
    """منتج شحن ألعاب منشور من خدمة مزود يجب أن يطلب Player ID قبل الشراء."""
    from handlers.games import player_id_received, product_confirm, product_selected

    protocol = OrderProtocol()
    monkeypatch.setattr(ProtocolFactory, "create_from_provider", lambda provider: protocol)

    provider = await _seed_provider()
    await ProviderSyncService.sync_provider_services(provider.id)

    async with async_session_maker() as session:
        category = await DynamicService.create_category(
            session, "شحن الألعاب", "🎮", CategoryType.GAMES
        )
        section = await DynamicService.create_sub_category(
            session, category.id, "شحن ببجي", "🔫"
        )
        pulled = await _pulled(session)
        product = await PulledServicesService.publish(
            session, pulled["1001"], section.id, Decimal("1.50")
        )
        user = User(telegram_id=770001, username="gamer", balance=Decimal("10"))
        session.add(user)
        await session.commit()
        await session.refresh(user)

        state = FakeState()
        select_callback = DummyCallback(f"prod:{product.id}")
        await product_selected(select_callback, session, user, state)

        # يجب أن يطلب Player ID (لا أن يقفز للتأكيد مباشرة)
        assert state.state == GamesOrderStates.waiting_player_id, (
            "منتج شحن ألعاب لم يطلب Player ID"
        )

        await player_id_received(
            FakeTextMessage("5123456789"), state, session, user
        )

        monkeypatch.setattr(
            "services.notification_service.NotificationService.notify_admin", _noop
        )
        await product_confirm(
            DummyCallback(f"prod_confirm:{product.id}"), session, user, DummyBot(), state
        )

        orders = (await session.execute(select(UnifiedOrder))).scalars().all()
        await session.refresh(user)

    assert protocol.orders == [
        {"service_id": "1001", "target": "5123456789", "quantity": 1}
    ]
    assert len(orders) == 1
    assert orders[0].target == "5123456789"
    assert orders[0].external_order_id == "ord-1"
    # السعر المدفوع يتبع أولوية الهوامش (الهامش العام 50% على تكلفة 0.90$)
    # — انظر test_published_price_follows_margin_hierarchy_until_manual.
    charged = Decimal("10") - user.balance
    assert abs(charged - Decimal("1.35")) < Decimal("0.001"), charged


async def test_published_price_follows_margin_hierarchy_until_manual(games_protocol):
    """سعر النشر يصبح هامشاً ضمنياً، والهوامش هي التي تحكم السعر النهائي.

    سلوك مقصود ومُختبَر في tests/test_margins.py: المنتج المسحوب يبقى
    ``margin_manual=False`` حتى يتحكم الأدمن بهامش قسمه. لذلك السعر الذي
    يُكتب وقت النشر لا يُدفع حرفياً إلا بعد جعل هامش المنتج يدوياً.
    """
    from services.margin_service import MarginService

    provider = await _seed_provider()
    await ProviderSyncService.sync_provider_services(provider.id)

    async with async_session_maker() as session:
        category = await DynamicService.create_category(
            session, "شحن الألعاب", "🎮", CategoryType.GAMES
        )
        section = await DynamicService.create_sub_category(
            session, category.id, "شحن ببجي", "🔫"
        )
        pulled = await _pulled(session)
        product = await PulledServicesService.publish(
            session, pulled["1001"], section.id, Decimal("1.50")
        )

        # الهامش الضمني من (1.50 مقابل 0.90) محفوظ لكنه غير يدوي
        assert product.profit_margin_percent == Decimal("66.67")
        assert product.margin_manual is False
        auto_price = await MarginService.product_sell_price(session, product)

        # الأدمن يقفل سعره: يجعل هامش المنتج يدوياً من شاشة المنتج
        product.margin_manual = True
        await session.commit()
        locked_price = await MarginService.product_sell_price(session, product)

    # بالهامش العام 50% على تكلفة 0.90$ → 1.35$ (لا 1.50$)
    assert auto_price == Decimal("1.3500")
    # بعد التثبيت اليدوي → سعر الأدمن نفسه
    assert locked_price == Decimal("1.5000")
