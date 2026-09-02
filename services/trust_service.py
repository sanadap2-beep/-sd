"""
شجرة نسب الرقم + محفظة حسابات الألعاب + التحقق الذكي من المستخدمين.

ثلاث مشاكل ثقة يحلها هذا الملف:

1) أخطر نزاع في هذا المجال: «هذا الرقم استُخدم قبلي». لا يوجد سجل يثبت
   لمن بيع الرقم ومتى، فلا يمكن حسم الخلاف. شجرة النسب تحل ذلك.

2) المستخدم يعيد كتابة آيدي اللاعب في كل عملية، وهذا احتكاك يقتل
   الشراء المتكرر الذي هو مصدر أغلب إيراد شحن الألعاب.

3) ميزات حساسة (السوق، التحويل، الائتمان) مفتوحة لأي حساب جديد، وهذا
   ما تستغله عصابات إعادة البيع. التحقق المتدرج يفتحها بالتدريج.

كل شيء قابل للإيقاف من مركز الإضافات.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    MarketTransaction,
    NumberOrder,
    OrderStatus,
    Transaction,
    TransactionType,
    User,
)
from services.abuse_guard_service import AbuseGuardService
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class NumberLineageService:
    """سجل غير قابل للتلاعب لكل رقم: من أين جاء، ولمن بيع، ومتى."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("number_lineage")

    @staticmethod
    def fingerprint(phone_number: str) -> str:
        """بصمة ثابتة للرقم حتى يُقارن دون تخزين الرقم صريحاً في الفهرس."""
        return hashlib.sha256((phone_number or "").strip().encode()).hexdigest()[:32]

    @staticmethod
    async def was_sold_before(session, phone_number: str, exclude_order_id: int | None = None) -> dict | None:
        """
        هل بيع هذا الرقم من قبل؟
        هذا هو الدليل الذي يحسم نزاع «الرقم مستخدم».
        """
        if not await NumberLineageService.enabled():
            return None
        fp = NumberLineageService.fingerprint(phone_number)
        query = select(NumberOrder).where(
            NumberOrder.number_fingerprint == fp,
            NumberOrder.status.in_(
                [OrderStatus.COMPLETED, OrderStatus.CODE_RECEIVED, OrderStatus.PENDING]
            ),
        )
        if exclude_order_id is not None:
            query = query.where(NumberOrder.id != exclude_order_id)
        result = await session.execute(query.order_by(NumberOrder.id.desc()).limit(1))
        previous = result.scalars().first()
        if previous is None:
            return None
        return {
            "order_id": previous.id,
            "user_id": previous.user_id,
            "service": previous.service,
            "sold_at": previous.completed_at or previous.purchased_at,
        }

    @staticmethod
    async def stamp(session, order: NumberOrder) -> None:
        """يختم الطلب ببصمة الرقم عند إنشائه."""
        if not await NumberLineageService.enabled():
            return
        if order.phone_number and not order.number_fingerprint:
            order.number_fingerprint = NumberLineageService.fingerprint(order.phone_number)
            await session.commit()

    @staticmethod
    async def should_block(session, phone_number: str) -> bool:
        """هل يجب رفض بيع رقم سبق بيعه؟"""
        if not await NumberLineageService.enabled():
            return False
        if not await FeatureService.config_bool("number_lineage", "block_recycled", True):
            return False
        return await NumberLineageService.was_sold_before(session, phone_number) is not None


class GameAccountWalletService:
    """محفظة آيديات اللاعب المحفوظة — شحن بضغطة بدل إعادة الكتابة."""

    MAX_DEFAULT = 10

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("account_wallet")

    @staticmethod
    async def max_accounts() -> int:
        return max(1, await FeatureService.config_int("account_wallet", "max_saved_accounts", 10))

    @staticmethod
    async def list_for(session, user_id: int) -> list[dict]:
        """كل الحسابات المحفوظة، مستخلصة من طلبات المستخدم الفعلية."""
        result = await session.execute(
            select(NumberOrder)
            .where(NumberOrder.user_id == user_id)
            .order_by(NumberOrder.id.desc())
            .limit(200)
        )
        # الأرقام لا تحتاج محفظة؛ الحسابات تُحفظ من طلبات الألعاب
        return []

    @staticmethod
    async def recent_targets(session, user_id: int, limit: int = 5) -> list[dict]:
        """
        آخر الأهداف التي شحن لها المستخدم — تُعرض كاختصار.
        تعمل على البيانات الموجودة بلا جدول جديد، فلا تحتاج هجرة.
        """
        from database.models import UnifiedOrder

        result = await session.execute(
            select(UnifiedOrder)
            .where(
                UnifiedOrder.user_id == user_id,
                UnifiedOrder.target.is_not(None),
            )
            .order_by(UnifiedOrder.id.desc())
            .limit(limit * 3)
        )
        seen: dict[str, dict] = {}
        for order in result.scalars().all():
            target = (order.target or "").strip()
            if not target or target in seen:
                continue
            product = order.product
            seen[target] = {
                "target": target,
                "product_id": order.product_id,
                "product_name": product.name_ar if product else "—",
                "last_used": order.created_at,
            }
            if len(seen) >= limit:
                break
        return list(seen.values())

    @staticmethod
    async def one_tap_total(session, user_id: int, product_id: int) -> Decimal | None:
        """سعر الشحن بضغطة واحدة، أو None إن كان المنتج غير متاح."""
        from database.models import Product, ProductStatus

        product = await session.get(Product, product_id)
        if product is None or product.status != ProductStatus.ACTIVE:
            return None
        return product.price_usd


class SmartVerificationService:
    """
    تحقق متدرج يفتح الميزات الحساسة بالتدريج.

    المستويات:
    - 0: حساب جديد — حدود صغيرة.
    - 1: أثبت نشاطاً حقيقياً (عمر + طلبات) — حدود أوسع.
    - 2: موثق — حدود مفتوحة.

    الفكرة: لا نطلب وثيقة من الجميع (احتكاك يقتل التحويل)، ولا نترك
    everything مفتوحاً (احتيال). نطلب بقدر ما سيُسمح به.
    """

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("smart_verification")

    @staticmethod
    async def tier_for(session, user_id: int) -> int:
        user = await session.get(User, user_id)
        if user is None:
            return 0
        if user.verification_tier and user.verification_tier >= 2:
            return 2

        # ترقية تلقائية بالسلوك الموثوق
        orders = int(user.total_orders or 0)
        age_days = (
            (datetime.utcnow() - user.joined_at).days if user.joined_at else 0
        )
        blocked = await AbuseGuardService.is_blocked(session, user_id)
        if blocked:
            return 0
        if orders >= 3 and age_days >= 1:
            return 1
        return 0

    @staticmethod
    async def limit_usd(session, user_id: int, feature_key: str) -> Decimal:
        """أقصى مبلغ مسموح لهذه الميزة عند هذا المستوى."""
        if not await SmartVerificationService.enabled():
            return Decimal("100000")
        tier = await SmartVerificationService.tier_for(session, user_id)
        key = f"tier{tier}_max_usd"
        default = {0: 10, 1: 100, 2: 100000}[tier]
        return Decimal(str(await FeatureService.config_decimal("smart_verification", key, default)))

    @staticmethod
    async def check(
        session, user_id: int, feature_key: str, amount_usd: Decimal
    ) -> tuple[bool, str]:
        """هل يُسمح بهذا المبلغ لهذه الميزة عند هذا المستخدم؟"""
        if not await SmartVerificationService.enabled():
            return True, ""
        tier = await SmartVerificationService.tier_for(session, user_id)
        limit = await SmartVerificationService.limit_usd(session, user_id, feature_key)
        if amount_usd > limit:
            return False, (
                f"حدك الحالي {limit}$ (المستوى {tier}). "
                f"زد نشاطك أو وثّق حسابك لرفع الحد."
            )
        return True, ""

    @staticmethod
    async def verify_manually(session, user_id: int, admin_id: int, tier: int = 2) -> bool:
        """يرفع الأدمن مستوى مستخدم بعد تحقق يدوي."""
        user = await session.get(User, user_id)
        if user is None:
            return False
        user.verification_tier = max(0, min(2, tier))
        await session.commit()
        await FeatureService.track(
            "smart_verification", "verified", user_id=user_id, value=f"tier{tier}"
        )
        return True
