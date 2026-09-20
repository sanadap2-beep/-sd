"""
خدمة مستويات VIP المحسوبة تلقائياً من إجمالي إنفاق المستخدم.

لا تحفظ عموداً في جدول users — تُحسب ديناميكياً من total_spent_usd
totals مع الإعدادات في feature_registry (vip_tiers.tiers_json).
كل مستوى يُعرّف بحد أدنى للإنفاق ومضاعف كاشباك.

المستوى المحفوظ هو حسب إجمالي الإنفاق الكلي (total_spent_usd).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


@dataclass
class VipTier:
    tier: int
    name: str
    cashback_mult: float
    min_spent_usd: Decimal


def _parse_tiers(raw: Any) -> list[VipTier]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = []
    if not isinstance(raw, list):
        raw = []
    tiers: list[VipTier] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            tiers.append(
                VipTier(
                    tier=int(item.get("tier", 0)),
                    name=str(item.get("name", "VIP")),
                    cashback_mult=float(item.get("cashback_mult", 1.0)),
                    min_spent_usd=Decimal(str(item.get("min_spent_usd", 0))),
                )
            )
        except Exception:
            continue
    tiers.sort(key=lambda t: t.min_spent_usd, reverse=True)
    return tiers


class VipService:
    _tiers: list[VipTier] | None = None

    @classmethod
    async def _load_tiers(cls) -> list[VipTier]:
        raw = await FeatureService.config("vip_tiers", "tiers_json", [])
        cls._tiers = _parse_tiers(raw)
        return cls._tiers

    @classmethod
    async def tiers(cls) -> list[VipTier]:
        if cls._tiers is None:
            return await cls._load_tiers()
        return cls._tiers

    @classmethod
    async def tier_for(cls, total_spent_usd: Decimal) -> VipTier:
        tiers = await cls.tiers()
        for tier in tiers:
            if total_spent_usd >= tier.min_spent_usd:
                return tier
        return VipTier(tier=0, name="عادي", cashback_mult=1.0, min_spent_usd=Decimal("0"))

    @classmethod
    async def cashback_multiplier(cls, total_spent_usd: Decimal) -> float:
        tier = await cls.tier_for(total_spent_usd)
        return tier.cashback_mult

    @classmethod
    async def enabled(cls) -> bool:
        return await FeatureService.enabled("vip_tiers")

    @classmethod
    async def show_in_profile(cls) -> bool:
        return await FeatureService.config_bool("vip_tiers", "show_in_profile", True)

    @classmethod
    async def tiers_display(cls, language: str = "ar") -> str:
        """نص يعرض جميع المستويات مع شروطها."""
        tiers = await cls.tiers()
        if language.startswith("ar"):
            lines = ["👑 <b>مستويات VIP</b>\n"]
            for t in sorted(tiers, key=lambda x: x.min_spent_usd):
                lines.append(
                    f"  {'🥉🥈🥇👑'[t.tier] if t.tier < 4 else '💎'} "
                    f"<b>{t.name}</b> — إنفاق ≥ {t.min_spent_usd}$ · "
                    f"كاشباك ×{t.cashback_mult}"
                )
        else:
            lines = ["👑 <b>VIP Tiers</b>\n"]
            for t in sorted(tiers, key=lambda x: x.min_spent_usd):
                lines.append(
                    f"  <b>{t.name}</b> — spent ≥ ${t.min_spent_usd} · "
                    f"cashback ×{t.cashback_mult}"
                )
        return "\n".join(lines)
