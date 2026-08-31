"""Performance analytics for SMS number providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from database.models import NumberOrder, OrderStatus, ProviderName
from services.feature_service import FeatureService


@dataclass
class NumberProviderStats:
    provider: str
    total: int = 0
    successful: int = 0
    failed: int = 0
    pending: int = 0
    success_rate: float = 0.0
    average_completion_seconds: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class NumberProviderStatsService:
    @staticmethod
    def _empty_stats() -> dict[str, NumberProviderStats]:
        return {provider.value: NumberProviderStats(provider.value) for provider in ProviderName}

    @staticmethod
    async def _collect(
        session,
        days: int = 30,
        service_code: str | None = None,
        country_code: str | None = None,
    ) -> dict[str, NumberProviderStats]:
        since = datetime.utcnow() - timedelta(days=days)
        query = select(NumberOrder).where(NumberOrder.purchased_at >= since)
        if service_code:
            query = query.where(NumberOrder.service == service_code)
        if country_code:
            query = query.where(NumberOrder.country_code == country_code)

        orders = list((await session.execute(query)).scalars().all())
        stats = NumberProviderStatsService._empty_stats()
        durations: dict[str, list[float]] = {provider.value: [] for provider in ProviderName}
        for order in orders:
            provider = getattr(order.provider, "value", str(order.provider))
            row = stats.setdefault(provider, NumberProviderStats(provider))
            row.total += 1
            if order.status in (OrderStatus.COMPLETED, OrderStatus.CODE_RECEIVED):
                row.successful += 1
                if order.completed_at and order.purchased_at:
                    durations.setdefault(provider, []).append(
                        max(0, (order.completed_at - order.purchased_at).total_seconds())
                    )
            elif order.status in (OrderStatus.REFUNDED, OrderStatus.EXPIRED, OrderStatus.CANCELLED):
                row.failed += 1
            else:
                row.pending += 1

        for provider, row in stats.items():
            finished = row.successful + row.failed
            row.success_rate = round(row.successful / finished * 100, 2) if finished else 0.0
            values = durations.setdefault(provider, [])
            row.average_completion_seconds = round(sum(values) / len(values), 2) if values else None
        return stats

    @staticmethod
    async def smart_routing_scores(
        session,
        service_code: str | None = None,
        country_code: str | None = None,
    ) -> dict[ProviderName, float]:
        """
        وزن جودة بين 0.10 و1.10 لكل مزود أرقام.

        - بدون عينة كافية: 1.0 حتى لا نعاقب مزوداً جديداً.
        - نجاح أعلى وزمن وصول أسرع = وزن أعلى.
        - نجاح منخفض جداً = وزن منخفض، فيبتعد عن الاختيار حتى لو كان أرخص.
        """
        if not await FeatureService.enabled("smart_number_routing"):
            return {provider: 1.0 for provider in ProviderName}

        days = await FeatureService.config_int("smart_number_routing", "days", 14)
        min_samples = await FeatureService.config_int("smart_number_routing", "min_samples", 5)
        min_success = await FeatureService.config_int("smart_number_routing", "min_success_rate", 55)
        stats = await NumberProviderStatsService._collect(
            session,
            days=max(1, days),
            service_code=service_code,
            country_code=country_code,
        )

        # نحسب أفضل زمن حقيقي للمقارنة. إن لم توجد أزمنة، عامل السرعة محايد.
        latencies = [
            row.average_completion_seconds
            for row in stats.values()
            if row.average_completion_seconds and row.average_completion_seconds > 0
        ]
        best_latency = min(latencies) if latencies else None

        out: dict[ProviderName, float] = {}
        for provider in ProviderName:
            row = stats.get(provider.value)
            if row is None or row.total < min_samples:
                out[provider] = 1.0
                continue

            success_factor = max(0.10, min(1.0, row.success_rate / 100))
            if row.success_rate < min_success:
                # عقوبة واضحة للمزود السيئ، لكن لا نلغيه نهائياً إن كان الوحيد.
                success_factor *= 0.55

            if best_latency and row.average_completion_seconds:
                speed_factor = max(0.25, min(1.0, best_latency / row.average_completion_seconds))
            else:
                speed_factor = 1.0

            weight = (success_factor * 0.75) + (speed_factor * 0.25)
            out[provider] = round(max(0.10, min(1.10, weight)), 4)
        return out

    @staticmethod
    async def report(session, days: int = 30) -> list[dict]:
        stats = await NumberProviderStatsService._collect(session, days=days)
        return [
            row.as_dict()
            for row in sorted(
                stats.values(), key=lambda value: (-value.success_rate, value.provider)
            )
        ]
