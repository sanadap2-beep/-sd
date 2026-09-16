"""
لوحة ألقاب المشترين النشط.

يعرض أفضل المشترين حسب إنفاقهم الحقيقي خلال الأسبوع الجاري
من جميع أنواع الطلبات (أرقام، رشق، ألعاب، تطبيقات، عروض خاصة).
الألقاب ذهبية/فضية/برونزية تتحول تلقائياً حسب الترتيب.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from database.models import NumberOrder, SpecialOfferOrder, User
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)

TITLES = (
    ("🥇", "الذهبي"),
    ("🥈", "الفضي"),
    ("🥉", "البرونزي"),
)


class LeaderboardService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("top_buyers")

    @staticmethod
    async def _spend_since(session, since) -> dict[int, Decimal]:
        totals: dict[int, Decimal] = defaultdict(Decimal)

        number_rows = await session.execute(
            select(NumberOrder.user_id, NumberOrder.price_sell_usd).where(
                NumberOrder.purchased_at >= since
            )
        )
        for user_id, price in number_rows.all():
            totals[user_id] += price

        offer_rows = await session.execute(
            select(SpecialOfferOrder.user_id, SpecialOfferOrder.price_usd).where(
                SpecialOfferOrder.created_at >= since
            )
        )
        for user_id, price in offer_rows.all():
            totals[user_id] += price

        return totals

    @staticmethod
    async def weekly_board(session, limit: int | None = None) -> list[dict]:
        """أفضل المشترين هذا الأسبوع (منذ بداية الأسبوع). تُرجع قوائم معالَجة."""
        if limit is None:
            limit = int(await FeatureService.config("top_buyers", "max_players", 10))

        start_of_week = datetime.utcnow().date()
        start_of_week = start_of_week - timedelta(days=start_of_week.weekday())
        since = datetime.combine(start_of_week, datetime.min.time())
        totals = await LeaderboardService._spend_since(session, since)

        if not totals:
            return []

        top_user_ids = [
            uid
            for uid, _ in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[: int(limit)]
        ]
        rows = []
        for uid in top_user_ids:
            user = await session.get(User, uid)
            if user is None:
                continue
            spent = totals[uid]
            rows.append(
                {
                    "user_id": uid,
                    "name": user.full_name or user.username or f"مستخدم {uid}",
                    "username": user.username,
                    "spent_usd": spent,
                    "orders_today": None,
                }
            )
        return rows

    @staticmethod
    async def my_rank(session, user_id: int) -> tuple[int, Decimal] | None:
        """ترتيب المستخدم وأنفقه هذا الأسبوع."""
        start_of_week = datetime.utcnow().date() - timedelta(
            days=datetime.utcnow().date().weekday()
        )
        since = datetime.combine(start_of_week, datetime.min.time())
        totals = await LeaderboardService._spend_since(session, since)
        mine = totals.get(user_id, Decimal("0"))
        if mine <= 0:
            return None
        rank = sum(1 for v in totals.values() if v > mine) + 1
        return rank, mine

    @staticmethod
    def title_for(rank: int) -> tuple[str, str]:
        if rank <= 3:
            return TITLES[rank - 1]
        return "", ""

    @staticmethod
    async def render_board(session, include_self_user_id: int | None = None) -> str:
        board = await LeaderboardService.weekly_board(session)
        if not board:
            return "لا توجد مشتريات هذا الأسبوع بعد — كن أول المشترين! 🚀"

        lines = ["🏆 <b>لوحة أبطال هذا الأسبوع</b>\n"]
        for i, row in enumerate(board, start=1):
            emoji, title = LeaderboardService.title_for(i)
            name = row["name"]
            tag = f' @{row["username"]}' if row["username"] else ""
            medal = emoji or "•"
            if i <= 3 and title:
                lines.append(
                    f"{medal} <b>{name}</b>{tag}\n"
                    f"    └ {title} اللقب · أنفق {row['spent_usd']:g}$"
                )
            else:
                lines.append(f"{medal} {name}{tag} · {row['spent_usd']:g}$")

        if include_self_user_id is not None:
            rank_info = await LeaderboardService.my_rank(session, include_self_user_id)
            if rank_info is not None:
                lines.append(f"\n📍 ترتيبك الآن: <b>#{rank_info[0]}</b> (أنفقت {rank_info[1]:g}$)")
        return "\n".join(lines)