"""Market seller profile service with alias and password hash."""

from __future__ import annotations

import hashlib
import os
import re

from sqlalchemy import select

from database.models import MarketProfile


class MarketProfileError(Exception):
    pass


class MarketProfileService:
    @staticmethod
    def stats(profile: MarketProfile | None) -> dict:
        if profile is None:
            return {
                "alias": "بائع",
                "successful_sales": 0,
                "failed_sales": 0,
                "disputes_count": 0,
                "success_rate": 0.0,
                "tier": "🆕 بائع جديد",
            }
        total = (profile.successful_sales or 0) + (profile.failed_sales or 0)
        success_rate = round((profile.successful_sales or 0) / total * 100, 1) if total else 0.0
        if total >= 50 and success_rate >= 95:
            tier = "👑 بائع ممتاز"
        elif total >= 15 and success_rate >= 90:
            tier = "✅ بائع موثوق"
        elif total >= 3 and success_rate >= 75:
            tier = "⭐ بائع نشيط"
        else:
            tier = "🆕 بائع جديد"
        return {
            "alias": profile.alias,
            "successful_sales": profile.successful_sales or 0,
            "failed_sales": profile.failed_sales or 0,
            "disputes_count": profile.disputes_count or 0,
            "success_rate": success_rate,
            "tier": tier,
        }

    @staticmethod
    def hash_password(password: str, salt: bytes | None = None) -> str:
        salt = salt or os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
        return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"

    @staticmethod
    def verify_password(password: str, stored: str) -> bool:
        try:
            _algo, salt_hex, digest_hex = stored.split("$", 2)
            expected = MarketProfileService.hash_password(password, bytes.fromhex(salt_hex))
            return expected.endswith("$" + digest_hex)
        except Exception:
            return False

    @staticmethod
    async def get(session, user_id: int) -> MarketProfile | None:
        result = await session.execute(select(MarketProfile).where(MarketProfile.user_id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def create(session, user_id: int, alias: str, password: str) -> MarketProfile:
        alias = alias.strip()
        if not re.fullmatch(r"[A-Za-z0-9_\u0600-\u06FF]{3,24}", alias):
            raise MarketProfileError("الاسم المستعار يجب أن يكون 3-24 حرفاً بدون رموز خاصة.")
        if len(password) < 6:
            raise MarketProfileError("كلمة السر يجب أن تكون 6 أحرف على الأقل.")
        existing = await session.execute(select(MarketProfile).where(MarketProfile.alias == alias))
        if existing.scalar_one_or_none() is not None:
            raise MarketProfileError("هذا الاسم المستعار مستخدم مسبقاً.")
        profile = MarketProfile(
            user_id=user_id,
            alias=alias,
            password_hash=MarketProfileService.hash_password(password),
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
        return profile

    @staticmethod
    async def record_failure(session, seller_id: int) -> None:
        profile = await MarketProfileService.get(session, seller_id)
        if profile:
            profile.failed_sales += 1
            profile.disputes_count += 1
            await session.commit()

    @staticmethod
    async def record_success(session, seller_id: int) -> None:
        profile = await MarketProfileService.get(session, seller_id)
        if profile:
            profile.successful_sales += 1
            await session.commit()
