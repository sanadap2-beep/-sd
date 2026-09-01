"""محرك طلبات السوق: الكتالوج يتطور حسب طلب المستخدمين وتصويتهم."""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from database.models import (
    ProductRequest,
    ProductRequestStatus,
    ProductRequestVote,
)


class ProductRequestError(Exception):
    """خطأ قابل للعرض للمستخدم."""


class ProductRequestService:
    @staticmethod
    def normalize_title(title: str) -> str:
        return re.sub(r"\s+", " ", title.strip().casefold())[:128]

    @staticmethod
    async def create(
        session,
        user_id: int,
        title: str,
        details: str | None = None,
    ) -> tuple[ProductRequest, bool]:
        title = re.sub(r"\s+", " ", title.strip())
        if len(title) < 2 or len(title) > 128:
            raise ProductRequestError("العنوان يجب أن يكون بين 2 و128 حرفاً.")
        details = details.strip() if details else None
        if details and len(details) > 1500:
            raise ProductRequestError("تفاصيل الطلب طويلة جداً.")
        normalized = ProductRequestService.normalize_title(title)

        existing_result = await session.execute(
            select(ProductRequest)
            .where(
                ProductRequest.normalized_title == normalized,
                ProductRequest.status.in_(
                    [ProductRequestStatus.OPEN, ProductRequestStatus.IN_REVIEW]
                ),
            )
            .order_by(desc(ProductRequest.votes_count))
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            await ProductRequestService.vote(session, existing.id, user_id)
            await session.refresh(existing)
            return existing, False

        request = ProductRequest(
            user_id=user_id,
            title=title,
            details=details,
            normalized_title=normalized,
            status=ProductRequestStatus.OPEN,
            votes_count=1,
        )
        session.add(request)
        await session.flush()
        session.add(
            ProductRequestVote(
                request_id=request.id,
                user_id=user_id,
            )
        )
        await session.commit()
        await session.refresh(request)
        return request, True

    @staticmethod
    async def vote(session, request_id: int, user_id: int) -> bool:
        request = await session.get(ProductRequest, request_id)
        if request is None or request.status not in (
            ProductRequestStatus.OPEN,
            ProductRequestStatus.IN_REVIEW,
        ):
            raise ProductRequestError("هذا الطلب لم يعد متاحاً للتصويت.")
        existing = await session.execute(
            select(ProductRequestVote).where(
                ProductRequestVote.request_id == request_id,
                ProductRequestVote.user_id == user_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            return False
        session.add(ProductRequestVote(request_id=request_id, user_id=user_id))
        request.votes_count = (request.votes_count or 0) + 1
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False
        return True

    @staticmethod
    async def get_open(
        session,
        limit: int = 20,
    ) -> list[ProductRequest]:
        result = await session.execute(
            select(ProductRequest)
            .options(selectinload(ProductRequest.user))
            .where(
                ProductRequest.status.in_(
                    [ProductRequestStatus.OPEN, ProductRequestStatus.IN_REVIEW]
                )
            )
            .order_by(desc(ProductRequest.votes_count), desc(ProductRequest.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_for_user(
        session,
        user_id: int,
        limit: int = 20,
    ) -> list[ProductRequest]:
        result = await session.execute(
            select(ProductRequest)
            .where(ProductRequest.user_id == user_id)
            .order_by(desc(ProductRequest.created_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_one(session, request_id: int) -> ProductRequest | None:
        result = await session.execute(
            select(ProductRequest)
            .options(selectinload(ProductRequest.user))
            .where(ProductRequest.id == request_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def set_status(
        session,
        request_id: int,
        status: ProductRequestStatus,
        admin_id: int,
        note: str | None = None,
    ) -> ProductRequest | None:
        request = await session.get(ProductRequest, request_id)
        if request is None:
            return None
        request.status = status
        request.handled_by = admin_id
        request.admin_note = note
        request.handled_at = datetime.utcnow()
        await session.commit()
        return await ProductRequestService.get_one(session, request_id)
