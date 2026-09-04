"""سيرفرات/مزودون عامة لأي قسم.

هذه الخدمة هي «النواة» اللي بنشرحها للمستخدم:
- أي قسم (رئيسي / فرعي / أرقام) يقدر يملك أكثر من سيرفر.
- كل سيرفر مربوط بمزود (رقم أو API).
- كل سيرفر يملك نسبة ربح مستقلة.
- الديناميكية كلها من قاعدة البيانات — بلا كود جديد.

الاستخدام في واجهة المستخدم:
    servers = await StoreServerService.active_for_scope(session, "subcategory", sub.id)
    if servers: show_servers_kb(servers)
    # بعد اختيار سيرفر:
    products = await StoreServerService.filter_products(session, products, server)
    unit = await StoreServerService.unit_price(product, server)
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import ApiProvider, ProviderName, StoreServer


SCOPE_LABELS = {
    "category": "📂 قسم رئيسي",
    "subcategory": "🗂 قسم فرعي",
    "number_service": "📞 خدمة أرقام",
    "global": "🌐 عام (كل الأقسام)",
}


class StoreServerService:
    # ─────────── قراءة ───────────

    @staticmethod
    async def list_for_scope(
        session,
        scope: str,
        scope_id: int,
        active_only: bool = True,
    ) -> list[StoreServer]:
        query = select(StoreServer).where(
            StoreServer.scope == scope,
            StoreServer.scope_id == scope_id,
        )
        if active_only:
            query = query.where(StoreServer.is_active.is_(True))
        result = await session.execute(
            query.order_by(StoreServer.sort_order, StoreServer.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def active_for_scope(
        session, scope: str, scope_id: int
    ) -> list[StoreServer]:
        return await StoreServerService.list_for_scope(session, scope, scope_id, active_only=True)

    @staticmethod
    async def get(session, server_id: int) -> StoreServer | None:
        return await session.get(StoreServer, server_id)

    @staticmethod
    async def count_for_scope(session, scope: str, scope_id: int) -> int:
        return len(await StoreServerService.list_for_scope(session, scope, scope_id, active_only=False))

    @staticmethod
    async def list_all(session, active_only: bool = False) -> list[StoreServer]:
        query = select(StoreServer).order_by(
            StoreServer.scope, StoreServer.sort_order, StoreServer.id
        )
        if active_only:
            query = query.where(StoreServer.is_active.is_(True))
        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def counts_by_scope(session) -> dict[str, int]:
        from sqlalchemy import func

        result = await session.execute(
            select(StoreServer.scope, func.count(StoreServer.id))
            .group_by(StoreServer.scope)
        )
        return {scope: int(count) for scope, count in result.all()}

    # ─────────── إدارة ───────────

    @staticmethod
    async def create(
        session,
        scope: str,
        scope_id: int,
        name_ar: str,
        provider_kind: str = "api",
        provider_value: str | None = None,
        api_provider_id: int | None = None,
        emoji: str = "🖥",
        description: str | None = None,
        margin_percent: Decimal | None = None,
        sort_order: int = 0,
    ) -> StoreServer:
        server = StoreServer(
            scope=scope,
            scope_id=scope_id,
            name_ar=name_ar,
            provider_kind=provider_kind,
            provider_value=provider_value,
            api_provider_id=api_provider_id,
            emoji=emoji,
            description=description,
            margin_percent=margin_percent,
            is_active=True,
            sort_order=sort_order,
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        return server

    @staticmethod
    async def update(session, server_id: int, **kwargs) -> StoreServer | None:
        server = await session.get(StoreServer, server_id)
        if server is None:
            return None
        for key, value in kwargs.items():
            if hasattr(server, key):
                setattr(server, key, value)
        await session.commit()
        await session.refresh(server)
        return server

    @staticmethod
    async def delete(session, server_id: int) -> bool:
        server = await session.get(StoreServer, server_id)
        if server is None:
            return False
        await session.delete(server)
        await session.commit()
        return True

    # ─────────── لصق بالمزود ───────────

    @staticmethod
    async def provider_label(server: StoreServer) -> str:
        if server.provider_kind == "number":
            return server.provider_value or "—"
        if server.api_provider_id:
            return f"#{server.api_provider_id}"
        return "—"

    @staticmethod
    def validate_number_provider(raw: str) -> str | None:
        try:
            return ProviderName(raw).value
        except ValueError:
            return None

    # ─────────── الأسعار / الهامش ───────────

    @staticmethod
    async def unit_price(
        product,
        server: StoreServer | None = None,
        default_price: Decimal | None = None,
    ) -> Decimal:
        """السعر الفعلي للوحدة بعد تطبيق هامش السيرفر (أو سعر المنتج).

        هامش السيرفر يعمل على سعر التكلفة إن وجدت (دون تغيير قاعدة البيانات).
        """
        base = default_price
        if base is None:
            base = getattr(product, "price_usd", None)
        base = Decimal(str(base or 0))
        if server is None or server.margin_percent is None:
            return base
        margin = Decimal(str(server.margin_percent))
        cost = getattr(product, "cost_price_usd", None)
        if cost is None or Decimal(str(cost or 0)) <= 0:
            return base
        unit = Decimal(str(cost)) * (Decimal("100") + margin) / Decimal("100")
        return unit.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    @staticmethod
    async def total_price(
        product,
        quantity: int,
        server: StoreServer | None = None,
        default_price: Decimal | None = None,
    ) -> Decimal:
        unit = await StoreServerService.unit_price(product, server, default_price)
        return (unit * Decimal(quantity)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    @staticmethod
    async def filter_products(products: list, server: StoreServer | None) -> list:
        """يرشّح المنتجات حسب مزود السيرفر.

        - سيرفر API: يعرض منتجات ذلك المزود فقط.
        - سيرفر رقم: لا يرشّح (الأرقام لها مسارها الخاص).
        - بلا سيرفر: يعرض الكل.
        """
        if server is None:
            return list(products)
        if server.provider_kind != "api" or server.api_provider_id is None:
            return list(products)
        return [
            p for p in products if getattr(p, "api_provider_id", None) == server.api_provider_id
        ]

    # ─────────── عدد الأقسام ───────────

    @staticmethod
    async def scope_selector(session) -> list[tuple[str, int]]:
        """عدد السيرفرات لكل نطاق (للأدمن)."""
        from sqlalchemy import func

        result = await session.execute(
            select(StoreServer.scope, func.count(StoreServer.id))
            .group_by(StoreServer.scope)
            .order_by(StoreServer.scope)
        )
        return [(scope, int(count)) for scope, count in result.all()]
