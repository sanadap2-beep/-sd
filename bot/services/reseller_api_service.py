"""Wholesale API keys and reseller access, isolated from Telegram auth."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import ResellerAccount, ResellerApiKey, User


class ResellerAuthError(Exception):
    pass


class ResellerAPIService:
    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    @staticmethod
    def _new_key() -> str:
        return "rsl_" + secrets.token_urlsafe(32)

    @staticmethod
    async def create_account(
        session,
        name: str,
        user_id: int,
        label: str | None = None,
    ) -> tuple[ResellerAccount, str]:
        user = await session.get(User, user_id)
        if user is None:
            raise ResellerAuthError("المستخدم غير موجود.")
        existing = await session.execute(
            select(ResellerAccount).where(ResellerAccount.user_id == user_id)
        )
        account = existing.scalar_one_or_none()
        if account is None:
            account = ResellerAccount(name=name[:128], user_id=user_id)
            session.add(account)
            await session.flush()
        raw_key = ResellerAPIService._new_key()
        session.add(
            ResellerApiKey(
                reseller_id=account.id,
                key_hash=ResellerAPIService._hash(raw_key),
                key_prefix=raw_key[:12],
                label=label[:128] if label else None,
                is_active=True,
            )
        )
        await session.commit()
        await session.refresh(account)
        return account, raw_key

    @staticmethod
    async def authenticate(session, raw_key: str) -> ResellerAccount:
        if not raw_key or len(raw_key) > 256:
            raise ResellerAuthError("مفتاح reseller غير صالح.")
        result = await session.execute(
            select(ResellerApiKey)
            .options(selectinload(ResellerApiKey.reseller).selectinload(ResellerAccount.user))
            .where(
                ResellerApiKey.key_hash == ResellerAPIService._hash(raw_key),
                ResellerApiKey.is_active.is_(True),
            )
        )
        api_key = result.scalar_one_or_none()
        if api_key is None or not api_key.reseller.is_active:
            raise ResellerAuthError("مفتاح reseller غير صالح أو موقوف.")
        api_key.last_used_at = datetime.utcnow()
        await session.commit()
        return api_key.reseller

    @staticmethod
    async def revoke_key(session, key_id: int) -> bool:
        key = await session.get(ResellerApiKey, key_id)
        if key is None:
            return False
        key.is_active = False
        await session.commit()
        return True
