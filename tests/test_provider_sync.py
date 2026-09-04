from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import ApiProvider, ApiProviderType, ApiProtocolType, ProviderService
from protocols.base import ProtocolBalance, ProtocolService
from protocols.factory import ProtocolFactory
from services.provider_sync_service import ProviderSyncService
from services.service_localization_service import is_arabic


class FakeProtocol:
    async def get_balance(self):
        return ProtocolBalance(Decimal("10"), "USD")

    async def get_services(self):
        return [
            ProtocolService(
                external_id="42",
                name="Test Service",
                category="Test",
                rate=Decimal("0.2"),
                min_quantity=10,
                max_quantity=100,
            )
        ]


@pytest.mark.asyncio
async def test_provider_sync_persists_normalized_services(monkeypatch):
    monkeypatch.setattr(ProtocolFactory, "create_from_provider", lambda provider: FakeProtocol())
    async with async_session_maker() as session:
        provider = ApiProvider(
            name="Fake",
            type=ApiProviderType.GAMES,
            protocol_type=ApiProtocolType.GAMES_GENERIC,
            api_url="https://provider.test",
            api_key="secret",
            currency="USD",
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        provider_id = provider.id

    result = await ProviderSyncService.sync_provider_services(provider_id)
    assert result.success is True
    assert result.new_services == 1

    async with async_session_maker() as session:
        service = (
            await session.execute(
                select(ProviderService).where(ProviderService.api_provider_id == provider_id)
            )
        ).scalar_one_or_none()
        assert service is not None
        assert service.external_service_id == "42"
        assert service.rate_usd == Decimal("0.2000")


class FakeStoreProtocol:
    """مزود بنمط Hyper Store: خدمات إنجليزية بأسماء متاجر عامة."""

    async def get_balance(self):
        return ProtocolBalance(Decimal("10"), "USD")

    async def get_services(self):
        return [
            ProtocolService(
                external_id="9001",
                name="Free Fire Diamonds 100",
                category="Gaming",
                rate=Decimal("1.1"),
                min_quantity=10,
                max_quantity=100,
            ),
            ProtocolService(
                external_id="9002",
                name="Netflix Premium 1 Month",
                category="Subscriptions",
                rate=Decimal("5.2"),
                min_quantity=1,
                max_quantity=12,
            ),
        ]


@pytest.mark.asyncio
async def test_sync_stores_arabic_name_for_every_service(monkeypatch):
    """كل خدمة مسحوبة تملك اسماً عربياً محفوظاً (name_ar) وقت السحب."""
    monkeypatch.setattr(ProtocolFactory, "create_from_provider", lambda provider: FakeStoreProtocol())
    async with async_session_maker() as session:
        provider = ApiProvider(
            name="Hyper Store",
            type=ApiProviderType.STORE,
            protocol_type=ApiProtocolType.CUSTOM,
            api_url="https://api.hyper4store.com",
            api_key="token",
            currency="USD",
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        provider_id = provider.id

    result = await ProviderSyncService.sync_provider_services(provider_id)
    assert result.success is True
    assert result.new_services == 2

    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(ProviderService).where(
                    ProviderService.api_provider_id == provider_id
                )
            )
        ).scalars().all()
        by_id = {r.external_service_id: r for r in rows}

        ff = by_id["9001"]
        # الاسم الأصلي الإنجليزي يبقى كما وصل من المزود
        assert ff.name == "Free Fire Diamonds 100"
        # والاسم العربي محفوظ ومُعرَّب فعلاً
        assert ff.name_ar is not None
        assert "فري فاير" in ff.name_ar
        assert "ماسات" in ff.name_ar
        assert not is_arabic(ff.name) and is_arabic(ff.name_ar)

        nf = by_id["9002"]
        assert "نتفليكس" in nf.name_ar
        assert "مميز" in nf.name_ar
        assert "شهر" in nf.name_ar


@pytest.mark.asyncio
async def test_resync_refreshes_stored_arabic_name(monkeypatch):
    """المزامنة اللاحقة تحدّث الاسم العربي مع الاسم الأصلي."""
    monkeypatch.setattr(ProtocolFactory, "create_from_provider", lambda provider: FakeStoreProtocol())
    async with async_session_maker() as session:
        provider = ApiProvider(
            name="Hyper Store",
            type=ApiProviderType.STORE,
            protocol_type=ApiProtocolType.CUSTOM,
            api_url="https://api.hyper4store.com",
            api_key="token",
            currency="USD",
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        provider_id = provider.id

    await ProviderSyncService.sync_provider_services(provider_id)

    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(ProviderService).where(
                    ProviderService.api_provider_id == provider_id,
                    ProviderService.external_service_id == "9001",
                )
            )
        ).scalar_one()
        # محاكاة بيانات قديمة بلا اسم عربي ثم مزامنة جديدة تملؤه
        row.name_ar = None
        await session.commit()

    await ProviderSyncService.sync_provider_services(provider_id)

    async with async_session_maker() as session:
        row = (
            await session.execute(
                select(ProviderService).where(
                    ProviderService.api_provider_id == provider_id,
                    ProviderService.external_service_id == "9001",
                )
            )
        ).scalar_one()
        assert row.name_ar is not None and is_arabic(row.name_ar)
