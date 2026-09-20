"""أكثر من مزود أرقام = سيرفر مستقل لكل مزود داخل قسم الأرقام.

اختبار تكاملي يشغّل **المسار الحقيقي** (لا يحاكي لوحة الأسعار ولا الشراء):

1. مزودان وهميان داخل ``ProviderManager`` الحقيقي، لكل واحد أسعار مختلفة.
2. شاشة المستخدم: الخدمة ← السيرفرات («سيرفر 1» / «سيرفر 2» بأسماء محايدة)
   ← دول كل سيرفر بأسعار مزوده وحده.
3. شاشة السعر ← تأكيد الشراء: يُشترى من مزود السيرفر المختار حصراً
   (المزود الآخر لا يُلمس إطلاقاً).
4. نسبة ربح السيرفر تتفوق على هامش الخدمة.
5. لوحة الأدمن: إضافة سيرفر ثالث لمزود آخر + تعليمه بالنقطة الخضراء 🟢،
   والنقطة تظهر للمستخدم بلا كشف اسم المزود.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    Country,
    NumberOrder,
    NumberServer,
    NumberService,
    ProviderName,
    User,
)
from handlers.admin.number_services import (
    nsvc_server_add_start,
    nsvc_server_provider_received,
    nsvc_server_view,
    nsvc_server_working_toggle,
    nsvc_servers_list,
)
from handlers.numbers import (
    confirm_buy,
    number_server_picked,
    number_service_selected,
    numbers_server_list,
    show_price,
)
from providers.base import PurchasedNumber
from providers.manager import provider_manager
from services.number_catalog_service import invalidate_board
from services.number_server_service import NumberServerService


# ══════════════ أدوات الاختبار ══════════════


class DummyMessage:
    def __init__(self):
        self.answers: list[tuple[str, object]] = []
        self.edits: list[tuple[str, object]] = []
        self.chat = SimpleNamespace(id=99001)
        self.message_id = 4242

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return SimpleNamespace(chat=self.chat, message_id=len(self.answers) + 500)

    async def edit_text(self, text: str, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return None

    async def edit_reply_markup(self, reply_markup=None):
        return None


class DummyCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = DummyMessage()
        self.answers: list[tuple[str | None, bool]] = []
        self.from_user = SimpleNamespace(id=88001, username="buyer")

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs):
        self.answers.append((text, show_alert))
        return None


class DummyBot:
    async def send_document(self, **kwargs):
        return None

    async def send_message(self, *args, **kwargs):
        return None


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


class FakeNumberProvider:
    """مزود أرقام وهمي يطبّق واجهة ``BaseProvider`` بأسعار محددة مسبقاً."""

    def __init__(self, name: str, prices: dict[tuple[str, str], Decimal]):
        self.name = name
        self.prices = prices
        self.bought: list[tuple[str, str]] = []
        self._counter = 0

    async def get_balance(self) -> Decimal:
        return Decimal("100.00")

    async def get_price(self, country: str, service: str):
        return self.prices.get((country, service))

    async def buy_number(self, country, service, operator=None, max_price=None):
        cost = self.prices.get((country, service))
        if cost is None:
            raise RuntimeError(f"لا مخزون لدى {self.name}")
        if max_price is not None and cost > max_price:
            raise RuntimeError("السعر تجاوز السقف")
        self._counter += 1
        self.bought.append((country, service))
        return PurchasedNumber(
            provider_order_id=f"{self.name}-{self._counter}",
            phone_number=f"+1555{self._counter:07d}",
            cost_usd=cost,
            raw={},
        )

    async def check_status(self, order_id):
        return None

    async def cancel_order(self, order_id) -> bool:
        return True

    async def finish_order(self, order_id) -> bool:
        return True

    async def get_countries_services(self) -> list[dict]:
        return []


async def _noop(*args, **kwargs):
    return None


def _buttons(markup) -> list:
    return [button for row in markup.inline_keyboard for button in row]


def _country_callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for button in _buttons(markup)
        if (button.callback_data or "").startswith("num_country:")
    ]


# ══════════════ تجهيز مزودَين + كتالوج أرقام ══════════════


@pytest.fixture
def two_providers(monkeypatch):
    """5sim أرخص في إندونيسيا، وHeroSMS أرخص في أمريكا.

    اختلاف الترتيب بين المزودَين هو ما يثبت أن كل سيرفر يعرض أرقام مزوده
    وحده (وليس قائمة واحدة مشتركة).
    """
    fivesim = FakeNumberProvider(
        "fivesim",
        {("id", "wa"): Decimal("0.10"), ("usa", "wa"): Decimal("0.50")},
    )
    herosms = FakeNumberProvider(
        "herosms",
        {("id_h", "wa_hero"): Decimal("0.40"), ("us_h", "wa_hero"): Decimal("0.25")},
    )
    monkeypatch.setitem(provider_manager._providers, ProviderName.FIVESIM, fivesim)
    monkeypatch.setitem(provider_manager._providers, ProviderName.HEROSMS, herosms)
    # أي مزود آخر يبقى خارج الاختبار حتى لا يتسرّب إلى الأسعار.
    for extra in (ProviderName.SMS_ACTIVATE, ProviderName.SMSHUB):
        if extra in provider_manager._providers:
            monkeypatch.delitem(provider_manager._providers, extra)
    invalidate_board()
    return SimpleNamespace(fivesim=fivesim, herosms=herosms)


async def _seed_catalog(balance: str = "20"):
    """خدمة واتساب بكود لدى المزودَين + دولتان بكود لدى المزودَين."""
    async with async_session_maker() as session:
        user = User(telegram_id=88001, username="buyer", balance=Decimal(balance))
        service = NumberService(
            code="wa",
            name_ar="واتساب",
            emoji="💬",
            fivesim_code="wa",
            herosms_code="wa_hero",
            is_active=True,
        )
        indonesia = Country(
            code="id",
            name_ar="إندونيسيا",
            flag="🇮🇩",
            fivesim_code="id",
            herosms_code="id_h",
            is_active=True,
        )
        usa = Country(
            code="us",
            name_ar="أمريكا",
            flag="🇺🇸",
            fivesim_code="usa",
            herosms_code="us_h",
            is_active=True,
        )
        session.add_all([user, service, indonesia, usa])
        await session.commit()
        for row in (user, service, indonesia, usa):
            await session.refresh(row)
        # user_id يُعاد بدلاً من الكائن: كل اختبار يحمّل المستخدم داخل
        # جلسته هو (مثل test_number_handlers) حتى تعمل refresh/الرصيد.
        return user.id, service, {"id": indonesia, "us": usa}


async def _servers_by_provider(session, service) -> dict[str, NumberServer]:
    servers = await NumberServerService.ensure_defaults(session, service)
    return {server.provider: server for server in servers}


# ══════════════ 1) كل مزود يظهر كسيرفر مستقل بأسعاره ══════════════


async def test_each_provider_becomes_its_own_server(two_providers):
    user_id, service, _ = await _seed_catalog()

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        callback = DummyCallback("num_svc:wa")
        await number_service_selected(callback, session, user)
        servers = await NumberServerService.list_servers(session, service.id)

    assert len(servers) == 2
    assert {server.provider for server in servers} == {
        ProviderName.FIVESIM.value,
        ProviderName.HEROSMS.value,
    }

    text, markup = callback.message.edits[-1]
    labels = [button.text or "" for button in _buttons(markup)]
    joined = " ".join(labels)
    # المستخدم يرى أسماء محايدة مرقّمة فقط
    assert "سيرفر 1" in joined
    assert "سيرفر 2" in joined
    # ولا يرى اسم أي مزود إطلاقاً
    lowered = (text + " " + joined).lower()
    for leaked in ("fivesim", "5sim", "herosms"):
        assert leaked not in lowered


async def test_each_server_shows_only_its_provider_countries(two_providers):
    user_id, service, countries = await _seed_catalog()
    indonesia_id = countries["id"].id
    usa_id = countries["us"].id

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        by_provider = await _servers_by_provider(session, service)

        boards: dict[str, list[str]] = {}
        for provider_value, server in by_provider.items():
            callback = DummyCallback(f"num_server_pick:wa:{server.id}")
            await number_server_picked(callback, session, user)
            boards[provider_value] = _country_callbacks(callback.message.edits[-1][1])

    # المرجع بالزر هو الرقم الداخلي للدولة (callback_data ≤ 64B).
    # 5sim: إندونيسيا (0.10) أرخص من أمريكا (0.50)
    assert boards[ProviderName.FIVESIM.value] == [
        f"num_country:wa:{indonesia_id}:{by_provider[ProviderName.FIVESIM.value].id}",
        f"num_country:wa:{usa_id}:{by_provider[ProviderName.FIVESIM.value].id}",
    ]
    # HeroSMS: أمريكا (0.25) أرخص من إندونيسيا (0.40) — ترتيب معاكس تماماً
    assert boards[ProviderName.HEROSMS.value] == [
        f"num_country:wa:{usa_id}:{by_provider[ProviderName.HEROSMS.value].id}",
        f"num_country:wa:{indonesia_id}:{by_provider[ProviderName.HEROSMS.value].id}",
    ]


# ══════════════ 2) الشراء من سيرفر = شراء من مزوده حصراً ══════════════


async def test_buy_on_a_server_uses_only_that_provider(two_providers, monkeypatch):
    monkeypatch.setattr(
        "services.notification_service.NotificationService.notify_admin", _noop
    )
    user_id, service, _ = await _seed_catalog()

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        by_provider = await _servers_by_provider(session, service)
        herosms_server = by_provider[ProviderName.HEROSMS.value]

        price_callback = DummyCallback(f"num_country:wa:us:{herosms_server.id}")
        await show_price(price_callback, session, user)
        confirm_data = next(
            button.callback_data
            for button in _buttons(price_callback.message.edits[-1][1])
            if (button.callback_data or "").startswith("num_confirm:")
        )
        # السيرفر يبقى مربوطاً بزر الشراء
        assert confirm_data.endswith(f":{herosms_server.id}")

        buy_callback = DummyCallback(confirm_data)
        await confirm_buy(buy_callback, session, user, DummyBot())

        orders = (await session.execute(select(NumberOrder))).scalars().all()
        await session.refresh(user)
        charged = user.balance

    assert len(orders) == 1
    order = orders[0]
    assert order.provider == ProviderName.HEROSMS
    assert order.price_provider_usd == Decimal("0.25")
    assert order.price_sell_usd > order.price_provider_usd
    # المزود الآخر لم يُستدعَ للشراء إطلاقاً
    assert two_providers.herosms.bought == [("us_h", "wa_hero")]
    assert two_providers.fivesim.bought == []
    assert charged == Decimal("20") - order.price_sell_usd


async def test_server_margin_wins_over_service_margin(two_providers, monkeypatch):
    monkeypatch.setattr(
        "services.notification_service.NotificationService.notify_admin", _noop
    )
    user_id, service, _ = await _seed_catalog()

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        by_provider = await _servers_by_provider(session, service)
        server = by_provider[ProviderName.HEROSMS.value]
        await NumberServerService.update(
            session, server.id, margin_percent=Decimal("30")
        )

        price_callback = DummyCallback(f"num_country:wa:us:{server.id}")
        await show_price(price_callback, session, user)
        confirm_data = next(
            button.callback_data
            for button in _buttons(price_callback.message.edits[-1][1])
            if (button.callback_data or "").startswith("num_confirm:")
        )
        await confirm_buy(DummyCallback(confirm_data), session, user, DummyBot())

        order = (await session.execute(select(NumberOrder))).scalars().one()

    # 0.25 + 30% = 0.325 (هامش السيرفر، لا هامش الخدمة الافتراضي)
    assert order.price_sell_usd == Decimal("0.3250")


# ══════════════ 3) لوحة الأدمن: سيرفر ثالث + النقطة الخضراء ══════════════


async def test_admin_adds_third_server_for_another_provider(two_providers):
    _user_id, service, _countries = await _seed_catalog()

    async with async_session_maker() as session:
        await _servers_by_provider(session, service)

        state = FakeState()
        await nsvc_server_add_start(
            DummyCallback(f"admin:nsvc_server_add:{service.id}"), state, session
        )
        await nsvc_server_provider_received(
            DummyCallback("admin:nsvc_server_provider:0:sms_activate"), state, session
        )

        servers = await NumberServerService.list_servers(
            session, service.id, active_only=False
        )
        third = servers[-1]

        assert len(servers) == 3
        assert third.provider == ProviderName.SMS_ACTIVATE.value
        assert third.name_ar == "سيرفر 3"

        # 🟢 النقطة الخضراء: الأدمن يعلّم السيرفر الشغّال
        await nsvc_server_working_toggle(
            DummyCallback(f"admin:nsvc_server_working:{third.id}"), session
        )
        refreshed = await NumberServerService.get(session, third.id)
        assert refreshed.is_working is True

        # السيرفر المعطّل لا يظهر للمستخدم، والشغّال تظهر أمامه 🟢
        await NumberServerService.update(session, servers[0].id, is_active=False)
        user_callback = DummyCallback("num_server:wa")
        await numbers_server_list(user_callback, session)
        labels = [
            button.text or "" for button in _buttons(user_callback.message.edits[-1][1])
        ]

    assert any(label.startswith("🟢") and "سيرفر 2" in label for label in labels)
    # لا تسريب لاسم المزود في واجهة المستخدم
    assert not any("sms_activate" in label for label in labels)


async def test_admin_is_warned_when_a_server_provider_has_no_api_key(two_providers):
    """سيرفر مربوط بمزود بلا مفتاح API = سيرفر بلا أرقام → تحذير صريح للأدمن.

    في هذا الاختبار المفتاحان المضبوطان هما 5sim وHeroSMS فقط، فالسيرفر
    المربوط بـ SMS-Activate يجب أن يظهر بتحذير في كل شاشات الأدمن.
    """
    _user_id, service, _countries = await _seed_catalog()

    async with async_session_maker() as session:
        # سيرفران افتراضيان (5sim + HeroSMS، وكلاهما بمفتاح مضبوط)
        await _servers_by_provider(session, service)

        # 1) شاشة «إضافة سيرفر» تعرض أي المزودين جاهز وأيها بلا مفتاح
        state = FakeState()
        add_callback = DummyCallback(f"admin:nsvc_server_add:{service.id}")
        await nsvc_server_add_start(add_callback, state, session)
        add_text = add_callback.message.edits[-1][0]
        assert "مزودون جاهزون بمفتاح API" in add_text
        assert "بلا مفتاح API" in add_text
        assert "sms_activate" in add_text.split("بلا مفتاح API")[1]

        # 2) إضافة السيرفر لمزود بلا مفتاح
        await nsvc_server_provider_received(
            DummyCallback("admin:nsvc_server_provider:0:sms_activate"), state, session
        )

        # 3) قائمة السيرفرات تحمل التحذير
        list_callback = DummyCallback(f"admin:nsvc_servers:{service.id}")
        await nsvc_servers_list(list_callback, session)
        list_text = list_callback.message.edits[-1][0]
        assert "مزود بلا مفتاح API" in list_text
        assert "sms_activate" in list_text

        # 4) شاشة السيرفر نفسه: تحذير للمزود بلا مفتاح، و✅ للمزود المضبوط
        servers = await NumberServerService.list_servers(session, service.id)
        unkeyed = next(s for s in servers if s.provider == ProviderName.SMS_ACTIVATE.value)
        keyed = next(s for s in servers if s.provider == ProviderName.FIVESIM.value)

        unkeyed_callback = DummyCallback(f"admin:nsvc_server:{unkeyed.id}")
        await nsvc_server_view(unkeyed_callback, session)
        assert "غير مضبوط" in unkeyed_callback.message.edits[-1][0]

        keyed_callback = DummyCallback(f"admin:nsvc_server:{keyed.id}")
        await nsvc_server_view(keyed_callback, session)
        assert "✅ مضبوط" in keyed_callback.message.edits[-1][0]
