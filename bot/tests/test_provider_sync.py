from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import ApiProvider, ApiProviderType, ApiProtocolType, ProviderService
from protocols.base import ProtocolBalance, ProtocolService
from protocols.factory import ProtocolFactory
from services.provider_sync_service import ProviderSyncService


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
