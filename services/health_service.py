"""Deep external health checks for the private admin dashboard."""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import select

from database.models import ApiProvider
from providers.manager import provider_manager
from services.plisio_service import plisio_client
from services.provider_sync_service import ProviderSyncService
from services.sam_api_service import sam_api_client


class HealthService:
    @staticmethod
    async def _check_sms(provider):
        try:
            balance = await asyncio.wait_for(provider_manager.get_balance(provider), timeout=15)
            return {"provider": provider.value, "ok": True, "balance": str(balance)}
        except Exception as exc:
            return {"provider": provider.value, "ok": False, "error": str(exc)[:180]}

    @staticmethod
    async def _check_api(provider: ApiProvider):
        try:
            ok, message, balance, currency = await asyncio.wait_for(
                ProviderSyncService.test_provider_connection(provider), timeout=15
            )
            return {
                "provider": provider.name,
                "ok": ok,
                "message": message,
                "balance": str(balance) if balance is not None else None,
                "currency": currency,
            }
        except Exception as exc:
            return {"provider": provider.name, "ok": False, "error": str(exc)[:180]}

    @staticmethod
    async def _check_plisio():
        if not plisio_client.secret_key:
            return {"configured": False, "ok": None}
        try:
            balance = await asyncio.wait_for(plisio_client.get_balance(), timeout=15)
            return {"configured": True, "ok": True, "balance": str(balance)}
        except Exception as exc:
            return {"configured": True, "ok": False, "error": str(exc)[:180]}

    @staticmethod
    async def _check_sam():
        if not sam_api_client.api_key or not sam_api_client.wallet_address:
            return {"configured": False, "ok": None}
        try:
            wallets = await asyncio.wait_for(sam_api_client.get_wallets(), timeout=15)
            return {"configured": True, "ok": True, "wallets": len(wallets)}
        except Exception as exc:
            return {"configured": True, "ok": False, "error": str(exc)[:180]}

    @staticmethod
    async def deep_check(session) -> dict:
        sms_tasks = [
            HealthService._check_sms(provider)
            for provider in provider_manager.get_available_providers()
        ]
        api_providers = list(
            (await session.execute(select(ApiProvider).where(ApiProvider.is_active.is_(True))))
            .scalars()
            .all()
        )
        results = await asyncio.gather(
            *sms_tasks, *[HealthService._check_api(p) for p in api_providers]
        )
        sms_count = len(sms_tasks)
        sms = list(results[:sms_count])
        api = list(results[sms_count:])
        payments = {
            "plisio": await HealthService._check_plisio(),
            "sam": await HealthService._check_sam(),
        }
        checks = sms + api + [value for value in payments.values() if value["configured"]]
        return {
            "ok": all(item.get("ok") is not False for item in checks),
            "checked_at": datetime.utcnow().isoformat(),
            "sms": sms,
            "api": api,
            "payments": payments,
        }
