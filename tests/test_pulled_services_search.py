""":🔎 البحث عن خدمة محددة في «الخدمات المسحوبة».

الهدف: أن يجد الأدمن خدمة واحدة بين آلاف الخدمات بالاسم أو بالآيدي،
بدل التصفّح منصة ← نوع ← صفحات.
"""

from __future__ import annotations

from decimal import Decimal

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ApiProviderType,
    ApiProtocolType,
    ProviderService,
    ProviderServiceStatus,
)
from services.pulled_services_service import PulledServicesService


async def _seed() -> tuple[int, int]:
    """مزودان + خدمات متنوعة. يعيد (provider_a_id, provider_b_id)."""
    async with async_session_maker() as session:
        provider_a = ApiProvider(
            name="Hyper Store",
            type=ApiProviderType.STORE,
            protocol_type=ApiProtocolType.CUSTOM,
            api_url="https://api.hyper4store.com",
            api_key="token-a",
        )
        provider_b = ApiProvider(
            name="مزود ثانوي",
            type=ApiProviderType.SMM,
            protocol_type=ApiProtocolType.SMM_V2,
            api_url="https://api.example.com",
            api_key="token-b",
        )
        session.add_all([provider_a, provider_b])
        await session.flush()

        rows = [
            # (provider, external_id, name, category, rate_usd)
            (provider_a, "9002", "PUBG Mobile UC 1000", "ألعاب", "3.50"),
            (provider_a, "9003", "تيك توك متابعين عرب", "تيك توك / متابعون", "1.20"),
            (provider_a, "9004", "تيك توك لايكات", "تيك توك / لايكات", "0.80"),
            (provider_a, "9005", "Instagram Followers", "انستغرام / متابعون", "2.00"),
            (provider_b, "1001", "تـيـك تـوك مشاهدات", "تيك توك / مشاهدات", "0.40"),
            (provider_b, "1002", "YouTube Views", "يوتيوب / مشاهدات", "0.60"),
        ]
        for provider, external_id, name, category, rate in rows:
            session.add(
                ProviderService(
                    api_provider_id=provider.id,
                    external_service_id=external_id,
                    name=name,
                    category=category,
                    service_type="Default",
                    rate=Decimal(rate),
                    rate_usd=Decimal(rate),
                    min_quantity=100,
                    max_quantity=10000,
                    requires_quantity=True,
                    status=ProviderServiceStatus.ACTIVE,
                )
            )
        await session.commit()
        return provider_a.id, provider_b.id


async def test_search_by_name_is_case_insensitive_and_partial():
    provider_a, _provider_b = await _seed()

    async with async_session_maker() as session:
        services, total = await PulledServicesService.search_services(session, "pubg")

    assert total == 1
    assert services[0].external_service_id == "9002"
    assert services[0].api_provider_id == provider_a


async def test_search_finds_english_service_by_its_arabic_name():
    """الدول/المنتجات الإنجليزية يُعثر عليها بالاسم العربي المُعرَّب.

    «PUBG Mobile UC 1000» تُعرَّب إلى «ببجي موبايل UC 1000»، فالبحث عن
    «ببجي» يجب أن يجدها حتى لو الاسم المحفوظ بالإنجليزية.
    """
    provider_a, _provider_b = await _seed()

    async with async_session_maker() as session:
        services, total = await PulledServicesService.search_services(session, "ببجي")

    assert total == 1
    assert services[0].external_service_id == "9002"
    assert services[0].api_provider_id == provider_a


async def test_search_by_provider_service_id():
    await _seed()

    async with async_session_maker() as session:
        services, total = await PulledServicesService.search_services(session, "9002")

    assert total == 1
    assert services[0].name == "PUBG Mobile UC 1000"


async def test_search_matches_several_words_with_and_and_sorts_cheapest_first():
    """«تيك توك متابعين» يطابق خدمة واحدة، ونتائج «تيك توك» من الأرخص للأغلى."""
    await _seed()

    async with async_session_maker() as session:
        services, total = await PulledServicesService.search_services(
            session, "تيك توك متابعين"
        )

    assert total == 1
    assert services[0].external_service_id == "9003"

    async with async_session_maker() as session:
        services, total = await PulledServicesService.search_services(session, "تيك توك")

    # ثلاث خدمات (اثنتان من المزود الأول + واحدة من الثاني) مرتبة بالسعر.
    assert total == 3
    assert [service.external_service_id for service in services] == ["1001", "9004", "9003"]
    assert [Decimal(str(service.rate_usd)) for service in services] == [
        Decimal("0.40"),
        Decimal("0.80"),
        Decimal("1.20"),
    ]


async def test_search_can_be_scoped_to_one_provider():
    _provider_a, provider_b = await _seed()

    async with async_session_maker() as session:
        services, total = await PulledServicesService.search_services(
            session, "تيك توك", provider_id=provider_b
        )

    assert total == 1
    assert services[0].api_provider_id == provider_b
    assert services[0].external_service_id == "1001"


async def test_search_normalizes_arabic_letters_and_tatweel():
    """«تـيـك تـوك» (بتطويل) تُطبع إلى «تيك توك» فتُوجد."""
    await _seed()

    async with async_session_maker() as session:
        services, total = await PulledServicesService.search_services(
            session, "تـيـك تـوك مشاهدات"
        )

    assert total == 1
    assert services[0].external_service_id == "1001"


async def test_search_matches_provider_name():
    _provider_a, _provider_b = await _seed()

    async with async_session_maker() as session:
        services, total = await PulledServicesService.search_services(session, "hyper")

    # اسم المزود «Hyper Store» يطابق كل خدماته الأربع.
    assert total == 4
    assert all(service.name is not None for service in services)


async def test_search_empty_query_returns_nothing():
    await _seed()

    async with async_session_maker() as session:
        services, total = await PulledServicesService.search_services(session, "   ")

    assert (services, total) == ([], 0)


async def test_search_ignores_inactive_services():
    _provider_a, provider_b = await _seed()

    async with async_session_maker() as session:
        result = await session.execute(
            ProviderService.__table__.select().where(
                ProviderService.external_service_id == "1001"
            )
        )
        row = result.first()
        assert row is not None
        service = await session.get(ProviderService, row.id)
        assert service is not None
        service.status = ProviderServiceStatus.INACTIVE
        await session.commit()

    async with async_session_maker() as session:
        services, total = await PulledServicesService.search_services(
            session, "تيك توك", provider_id=provider_b
        )

    assert (services, total) == ([], 0)


async def test_list_active_by_provider_filters_and_sorts_cheapest_first():
    """شاشة «كل خدمات المزود» تعرض كتالوج مزود واحد فقط الأرخص أولاً."""
    provider_a, provider_b = await _seed()

    async with async_session_maker() as session:
        services, total = await PulledServicesService.list_active_by_provider(
            session, provider_a
        )

    assert total == 4
    # أرخص ← أغلى: لايكات 0.80 → متابعين 1.20 → إنستغرام 2.00 → ببجي 3.50
    assert [s.external_service_id for s in services] == ["9004", "9003", "9005", "9002"]

    async with async_session_maker() as session:
        services, total = await PulledServicesService.list_active_by_provider(
            session, provider_b, page=0, per_page=2
        )

    assert total == 2
    assert len(services) == 2
    assert [s.api_provider_id for s in services] == [provider_b, provider_b]


def test_platforms_screen_offers_search_entry():
    """:زر البحث ظاهر في شاشة الخدمات المسحوبة (حتى لو كانت فارغة)."""
    from handlers.admin.pulled_services import _platforms_kb

    callbacks = [
        button.callback_data
        for row in _platforms_kb([]).inline_keyboard
        for button in row
    ]
    assert "ps:search" in callbacks


def test_search_results_keyboard_opens_service_detail():
    """:كل نتيجة تفتح صفحة الخدمة مباشرة (نشر/سعر)."""
    from types import SimpleNamespace

    from handlers.admin.pulled_services import _search_results_kb

    services = [
        SimpleNamespace(
            id=7,
            name="تيك توك متابعين",
            category="تيك توك",
            service_type="Default",
            rate_usd=Decimal("1.2"),
        )
    ]
    markup = _search_results_kb(services, 0, 1)
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "ps:sv:7" in callbacks
    assert "ps:search" in callbacks  # بحث جديد
    assert "admin:pulled_services" in callbacks
