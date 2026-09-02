"""قياس جودة المزودين من واقع الطلبات المنفذة."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from database.models import (
    NumberOrder,
    OrderStatus,
    UnifiedOrder,
    UnifiedOrderStatus,
)


class QualityService:
    @staticmethod
    def _rate(success: int, total: int) -> float:
        return round((success / total) * 100, 2) if total else 0.0

    @staticmethod
    async def report(session, days: int = 30) -> dict:
        since = datetime.utcnow() - timedelta(days=days)
        numbers = (
            (await session.execute(select(NumberOrder).where(NumberOrder.purchased_at >= since)))
            .scalars()
            .all()
        )
        api_orders = (
            (await session.execute(select(UnifiedOrder).where(UnifiedOrder.created_at >= since)))
            .scalars()
            .all()
        )
        result = {"days": days, "sms": [], "api": []}
        providers = {}
        for order in numbers:
            key = order.provider.value
            row = providers.setdefault(
                key, {"provider": key, "total": 0, "success": 0, "failed": 0}
            )
            row["total"] += 1
            if order.status in (OrderStatus.COMPLETED, OrderStatus.CODE_RECEIVED):
                row["success"] += 1
            elif order.status in (OrderStatus.REFUNDED, OrderStatus.EXPIRED, OrderStatus.CANCELLED):
                row["failed"] += 1
        for row in providers.values():
            row["success_rate"] = QualityService._rate(row["success"], row["total"])
        result["sms"] = sorted(
            providers.values(), key=lambda row: row["success_rate"], reverse=True
        )

        api = {}
        for order in api_orders:
            key = str(order.api_provider_id or "manual")
            row = api.setdefault(
                key, {"provider_id": order.api_provider_id, "total": 0, "success": 0, "failed": 0}
            )
            row["total"] += 1
            if order.status == UnifiedOrderStatus.COMPLETED:
                row["success"] += 1
            elif order.status in (UnifiedOrderStatus.FAILED, UnifiedOrderStatus.REFUNDED):
                row["failed"] += 1
        for row in api.values():
            row["success_rate"] = QualityService._rate(row["success"], row["total"])
        result["api"] = list(api.values())
        return result
