"""Admin HTTP API for the browser dashboard."""

import csv
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

from api.deps import get_current_admin, get_session
from api.schemas import ResellerCreateIn
from database.models import (
    ApiProvider,
    DepositRequest,
    DepositStatus,
    Product,
    ProductStatus,
    Promotion,
    SupportTicket,
    SupportTicketStatus,
    Transaction,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
    ProviderStatus,
)
from services.audit_service import AuditService
from services.ai_admin_copilot import AIAdminCopilot
from services.health_service import HealthService
from services.number_provider_stats_service import NumberProviderStatsService
from services.quality_service import QualityService
from services.reseller_api_service import ResellerAPIService, ResellerAuthError

router = APIRouter(
    prefix="/api/v1/admin",
    dependencies=[Depends(get_current_admin)],
)


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


@router.get("/overview")
async def overview(session=Depends(get_session)):
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    values = {
        "users": select(func.count(User.id)),
        "pending_deposits": select(func.count(DepositRequest.id)).where(
            DepositRequest.status == DepositStatus.PENDING
        ),
        "open_tickets": select(func.count(SupportTicket.id)).where(
            SupportTicket.status.in_((SupportTicketStatus.OPEN, SupportTicketStatus.IN_PROGRESS))
        ),
        "active_products": select(func.count(Product.id)).where(
            Product.status == ProductStatus.ACTIVE
        ),
        "active_providers": select(func.count(ApiProvider.id)).where(
            ApiProvider.is_active.is_(True)
        ),
        "active_orders": select(func.count(UnifiedOrder.id)).where(
            UnifiedOrder.status.in_((UnifiedOrderStatus.PENDING, UnifiedOrderStatus.PROCESSING))
        ),
    }
    counts = {key: (await session.execute(query)).scalar_one() for key, query in values.items()}
    balance = await session.execute(select(func.coalesce(func.sum(User.balance), 0)))
    today_purchases = await session.execute(
        select(func.coalesce(func.sum(-Transaction.amount), 0)).where(
            Transaction.type == TransactionType.PURCHASE,
            Transaction.created_at >= today,
        )
    )
    return {
        **counts,
        "total_user_balance_usd": _decimal(balance.scalar_one()),
        "today_purchase_volume_usd": _decimal(today_purchases.scalar_one()),
        "generated_at": now.isoformat(),
    }


@router.get("/finance")
async def finance(
    days: int = Query(default=30, ge=1, le=365),
    session=Depends(get_session),
):
    since = datetime.utcnow() - timedelta(days=days)
    result = await session.execute(
        select(Transaction.type, func.sum(Transaction.amount), func.count(Transaction.id))
        .where(Transaction.created_at >= since)
        .group_by(Transaction.type)
    )
    rows = []
    for tx_type, total, count in result.all():
        rows.append(
            {
                "type": tx_type.value,
                "amount_usd": _decimal(total),
                "count": count,
            }
        )
    return {"days": days, "rows": rows}


@router.get("/finance/export")
async def finance_export(
    days: int = Query(default=30, ge=1, le=365),
    session=Depends(get_session),
):
    since = datetime.utcnow() - timedelta(days=days)
    result = await session.execute(
        select(Transaction).where(Transaction.created_at >= since).order_by(Transaction.created_at)
    )
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["id", "user_id", "type", "amount_usd", "balance_after", "created_at", "description"]
    )
    for tx in result.scalars().all():
        writer.writerow(
            [
                tx.id,
                tx.user_id,
                tx.type.value,
                tx.amount,
                tx.balance_after,
                tx.created_at.isoformat(),
                tx.description or "",
            ]
        )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=finance-{days}d.csv"},
    )


@router.post("/resellers")
async def create_reseller(
    payload: ResellerCreateIn,
    session=Depends(get_session),
):
    try:
        account, api_key = await ResellerAPIService.create_account(
            session,
            payload.name,
            payload.user_id,
            payload.label,
        )
    except ResellerAuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "reseller_id": account.id,
        "api_key": api_key,
        "warning": "احفظ المفتاح الآن؛ لن يتم عرضه مرة أخرى.",
    }


@router.get("/users")
async def users(
    q: str | None = None,
    page: int = Query(default=0, ge=0),
    session=Depends(get_session),
):
    query = select(User).order_by(desc(User.last_activity_at), desc(User.id))
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            User.username.ilike(pattern)
            | User.full_name.ilike(pattern)
            | (User.telegram_id.cast(str) == q.strip())
        )
    result = await session.execute(query.limit(50).offset(page * 50))
    return [
        {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "full_name": user.full_name,
            "balance_usd": user.balance,
            "loyalty_points": user.loyalty_points,
            "is_banned": user.is_banned,
            "is_admin": user.is_admin,
        }
        for user in result.scalars().all()
    ]


@router.get("/orders")
async def orders(
    status: str | None = None,
    page: int = Query(default=0, ge=0),
    session=Depends(get_session),
):
    query = (
        select(UnifiedOrder)
        .options(selectinload(UnifiedOrder.user), selectinload(UnifiedOrder.product))
        .order_by(desc(UnifiedOrder.created_at))
    )
    if status:
        try:
            query = query.where(UnifiedOrder.status == UnifiedOrderStatus(status))
        except ValueError:
            return []
    result = await session.execute(query.limit(50).offset(page * 50))
    return [
        {
            "id": order.id,
            "status": order.status.value,
            "product": order.product.name_ar if order.product else None,
            "user_id": order.user.telegram_id if order.user else None,
            "price_usd": order.price_usd,
            "created_at": order.created_at.isoformat(),
            "status_message": order.status_message,
        }
        for order in result.scalars().all()
    ]


@router.get("/providers")
async def providers(session=Depends(get_session)):
    providers = list(
        (await session.execute(select(ApiProvider).order_by(ApiProvider.priority, ApiProvider.id)))
        .scalars()
        .all()
    )
    statuses = {
        row.provider.value: row
        for row in (await session.execute(select(ProviderStatus))).scalars().all()
    }
    return [
        {
            "id": provider.id,
            "name": provider.name,
            "type": provider.type.value,
            "protocol": provider.protocol_type.value,
            "is_active": provider.is_active,
            "balance": provider.balance,
            "currency": provider.currency,
            "total_services": provider.total_services,
            "last_sync_at": provider.last_sync_at.isoformat() if provider.last_sync_at else None,
            "error": provider.last_error,
            "sms_status": statuses.get(provider.name, {}).is_online
            if provider.name in statuses
            else None,
        }
        for provider in providers
    ]


@router.get("/promotions")
async def promotions(session=Depends(get_session)):
    result = await session.execute(
        select(Promotion)
        .options(selectinload(Promotion.product))
        .order_by(desc(Promotion.created_at))
    )
    return [
        {
            "id": promotion.id,
            "name": promotion.name,
            "product": promotion.product.name_ar if promotion.product else None,
            "discount_type": promotion.discount_type.value,
            "discount_value": promotion.discount_value,
            "starts_at": promotion.starts_at.isoformat(),
            "ends_at": promotion.ends_at.isoformat(),
            "used_count": promotion.used_count,
            "max_uses": promotion.max_uses,
            "is_active": promotion.is_active,
        }
        for promotion in result.scalars().all()
    ]


@router.get("/copilot/brief")
async def copilot_brief(
    days: int = Query(default=7, ge=1, le=90),
    session=Depends(get_session),
):
    return await AIAdminCopilot.brief(session, days)


@router.get("/health/deep")
async def deep_health(session=Depends(get_session)):
    return await HealthService.deep_check(session)


@router.get("/number-providers/stats")
async def number_provider_stats(
    days: int = Query(default=30, ge=1, le=365),
    session=Depends(get_session),
):
    return await NumberProviderStatsService.report(session, days)


@router.get("/quality")
async def quality(
    days: int = Query(default=30, ge=1, le=365),
    session=Depends(get_session),
):
    return await QualityService.report(session, days)


@router.get("/audit")
async def audit(
    page: int = Query(default=0, ge=0),
):
    logs = await AuditService.get_recent_logs(limit=30, offset=page * 30)
    return [
        {
            "id": log.id,
            "action": log.action.value,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "entity_name": log.entity_name,
            "description": log.description,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]
