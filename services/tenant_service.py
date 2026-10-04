"""White-Label tenants: lifecycle, wallets, margin pricing, subscriptions.

القواعد المالية (معتمدة):
- التاجر يضع هامش % فوق سعر البوت الأساسي (عام + per-category).
- التاجر يموّل محفظته مسبقاً في البوت الأساسي؛ كل طلب فرعي يخصم
  (السعر الأساسي + عمولة المنصة %) من محفظته.
- اشتراك $8 شهرياً من المحفظة + سماح 3 أيام ثم تجميد webhook.
- هوية مخفية بالكامل: لا أثر للبوت الأساسي في أي رسالة فرعية.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_UP

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from config import settings
from database.models import (
    Tenant,
    TenantCategoryMargin,
    TenantWallet,
)

logger = logging.getLogger(__name__)

SUBSCRIPTION_USD = Decimal("8")
SUBSCRIPTION_DAYS = 30
GRACE_DAYS = 3


class TenantError(Exception):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").strip().encode()).hexdigest()


def _quantize_usd(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_UP)


class TenantService:
    # ─────────── دورة الحياة ───────────

    @staticmethod
    async def create(
        session,
        *,
        owner_user_id: int,
        token: str,
        bot_username: str,
        brand_name: str,
        margin_percent: Decimal = Decimal("20"),
        catalog_mode: str = "full",
    ) -> Tenant:
        token = (token or "").strip()
        if not token or ":" not in token:
            raise TenantError("التوكن غير صالح.")
        brand_name = (brand_name or "").strip()[:64]
        if len(brand_name) < 2:
            raise TenantError("اسم المتجر قصير جداً.")
        if margin_percent < 0 or margin_percent > 500:
            raise TenantError("الهامش يجب أن يكون بين 0 و 500%.")
        if catalog_mode not in ("full", "selective"):
            raise TenantError("وضع الكتالوج غير صالح.")

        from services.encryption_service import EncryptionService

        if not EncryptionService.is_configured():
            raise TenantError(
                "مفتاح التشفير غير مُهيأ — لا يمكن تخزين توكنات التجار بأمان."
            )

        thash = token_hash(token)
        existing = (
            await session.execute(select(Tenant).where(Tenant.token_hash == thash))
        ).scalar_one_or_none()
        if existing is not None:
            raise TenantError("هذا البوت مسجل مسبقاً لدى تاجر آخر.")

        slug_base = "".join(
            ch for ch in (bot_username or "store").lower().lstrip("@")
            if ch.isalnum() or ch == "_"
        )[:28] or "store"
        slug = slug_base
        for attempt in range(5):
            clash = (
                await session.execute(select(Tenant).where(Tenant.slug == slug))
            ).scalar_one_or_none()
            if clash is None:
                break
            slug = f"{slug_base}{attempt + 2}"[:32]

        tenant = Tenant(
            slug=slug,
            owner_user_id=owner_user_id,
            bot_username=(bot_username or "").lower().lstrip("@")[:64] or None,
            brand_name=brand_name,
            token_encrypted=EncryptionService.encrypt(token),
            token_hash=thash,
            margin_percent=margin_percent,
            catalog_mode=catalog_mode,
            is_active=True,
            subscription_status="active",
            subscription_due_at=datetime.utcnow() + timedelta(days=SUBSCRIPTION_DAYS),
        )
        session.add(tenant)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise TenantError("تعذر إنشاء المتجر (تكرار)، حاول مجدداً.") from exc
        session.add(TenantWallet(tenant_id=tenant.id, balance=Decimal("0")))
        await session.commit()
        await session.refresh(tenant)
        logger.info("متجر فرعي جديد #%s للتاجر %s", tenant.id, owner_user_id)
        return tenant

    @staticmethod
    async def get(session, tenant_id: int) -> Tenant | None:
        return await session.get(Tenant, tenant_id)

    @staticmethod
    async def get_by_hash(session, thash: str) -> Tenant | None:
        result = await session.execute(
            select(Tenant).where(Tenant.token_hash == thash)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def merchant_tenants(session, owner_user_id: int) -> list[Tenant]:
        result = await session.execute(
            select(Tenant)
            .where(Tenant.owner_user_id == owner_user_id)
            .order_by(Tenant.id.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    def reveal_token(tenant: Tenant) -> str:
        from services.encryption_service import EncryptionService

        if not tenant.token_encrypted:
            raise TenantError("لا يوجد توكن مخزن لهذا المتجر.")
        return EncryptionService.decrypt(tenant.token_encrypted)

    @staticmethod
    def webhook_url(tenant: Tenant) -> str:
        base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
        if not base:
            raise TenantError("PUBLIC_BASE_URL غير مضبوط — لا يمكن تسجيل webhooks.")
        return f"{base}/wh/{tenant.token_hash}"

    @staticmethod
    def is_usable(tenant: Tenant | None) -> bool:
        return bool(
            tenant
            and tenant.is_active
            and tenant.subscription_status in ("active", "grace")
            and tenant.token_encrypted
        )

    # ─────────── التسعير بالهامش ───────────

    @staticmethod
    async def margin_for(
        session, tenant: Tenant, category_id: int | None = None
    ) -> Decimal:
        if category_id is not None:
            result = await session.execute(
                select(TenantCategoryMargin).where(
                    TenantCategoryMargin.tenant_id == tenant.id,
                    TenantCategoryMargin.category_id == category_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is not None:
                return row.margin_percent
        return tenant.margin_percent or Decimal("0")

    @staticmethod
    async def merchant_price(
        session,
        tenant: Tenant,
        base_price_usd: Decimal,
        category_id: int | None = None,
    ) -> Decimal:
        """سعر الزبون الفرعي = السعر الأساسي + هامش التاجر (تقريب لأعلى)."""
        margin = await TenantService.margin_for(session, tenant, category_id)
        return _quantize_usd(base_price_usd * (Decimal("1") + margin / Decimal("100")))

    @staticmethod
    def platform_fee(base_price_usd: Decimal, tenant: Tenant) -> Decimal:
        fee_pct = tenant.platform_fee_percent or Decimal("0")
        return _quantize_usd(base_price_usd * fee_pct / Decimal("100"))

    # ─────────── المحفظة (ذرية) ───────────

    @staticmethod
    async def wallet(session, tenant_id: int) -> TenantWallet:
        result = await session.execute(
            select(TenantWallet).where(TenantWallet.tenant_id == tenant_id)
        )
        wallet = result.scalar_one_or_none()
        if wallet is None:
            wallet = TenantWallet(tenant_id=tenant_id, balance=Decimal("0"))
            session.add(wallet)
            await session.commit()
        try:
            await session.refresh(wallet)
        except Exception:
            pass
        return wallet

    @staticmethod
    async def fund(
        session, tenant_id: int, amount_usd: Decimal, description: str = ""
    ) -> TenantWallet:
        if amount_usd <= 0 or not amount_usd.is_finite():
            raise TenantError("مبلغ التمويل غير صالح.")
        wallet = await TenantService.wallet(session, tenant_id)
        wallet.balance = wallet.balance + amount_usd
        wallet.total_funded_usd = wallet.total_funded_usd + amount_usd
        await session.commit()
        await session.refresh(wallet)
        logger.info("تمويل محفظة المستأجر %s: +%s$ (%s)", tenant_id, amount_usd, description)
        return wallet

    @staticmethod
    async def charge(
        session, tenant_id: int, amount_usd: Decimal, earned_usd: Decimal = Decimal("0")
    ) -> TenantWallet:
        """خصم ذري من محفظة التاجر — يفشل عند عدم الكفاية (لا رصيد سالب)."""
        if amount_usd <= 0 or not amount_usd.is_finite():
            raise TenantError("مبلغ الخصم غير صالح.")
        result = await session.execute(
            update(TenantWallet)
            .where(
                TenantWallet.tenant_id == tenant_id,
                TenantWallet.balance >= amount_usd,
            )
            .values(
                balance=TenantWallet.balance - amount_usd,
                total_spent_usd=TenantWallet.total_spent_usd + amount_usd,
                total_earned_usd=TenantWallet.total_earned_usd + earned_usd,
            )
            .execution_options(synchronize_session=False)
        )
        if (result.rowcount or 0) == 0:
            raise TenantError("رصيد محفظة المتجر غير كافٍ — موّل محفظتك أولاً.")
        wallet = await TenantService.wallet(session, tenant_id)
        try:
            await session.refresh(wallet, attribute_names=["balance"])
        except Exception:
            pass
        await session.commit()
        return wallet

    # ─────────── الاشتراك الشهري ───────────

    @staticmethod
    async def bill_due(session, *, now: datetime | None = None) -> dict:
        """يخصم $8 عن كل مستأجر مستحق. يرجع تقرير {billed, graced, suspended}."""
        now = now or datetime.utcnow()
        result = await session.execute(
            select(Tenant).where(
                Tenant.is_active.is_(True),
                Tenant.subscription_status.in_(["active", "grace"]),
                Tenant.subscription_due_at <= now,
            )
        )
        report = {"billed": [], "graced": [], "suspended": []}
        for tenant in result.scalars().all():
            # التقط المعرف أولاً — rollback لاحقاً يُبطل الكائنات
            tid = tenant.id
            try:
                await TenantService.charge(session, tid, SUBSCRIPTION_USD)
            except TenantError:
                await session.rollback()
                t2 = await session.get(Tenant, tid)
                if t2 is None:
                    continue
                if t2.subscription_status == "active":
                    t2.subscription_status = "grace"
                    t2.subscription_due_at = now + timedelta(days=GRACE_DAYS)
                    await session.commit()
                    report["graced"].append(tid)
                else:
                    t2.subscription_status = "suspended"
                    t2.suspended_reason = "انتهت مهلة الاشتراك الشهري ($8)"
                    t2.webhook_set = False
                    await session.commit()
                    report["suspended"].append(tid)
                continue
            t3 = await session.get(Tenant, tid)
            if t3 is None:
                continue
            t3.subscription_status = "active"
            t3.subscription_due_at = now + timedelta(days=SUBSCRIPTION_DAYS)
            t3.suspended_reason = None
            await session.commit()
            report["billed"].append(tid)
        if any(report.values()):
            logger.info("فوترة المستأجرين: %s", report)
        return report

    # ─────────── webhook عبر Bot API ───────────

    @staticmethod
    async def register_webhook(session, tenant: Tenant) -> bool:
        """يسجل webhook البوت الفرعي في تيليجرام. يرجع True عند النجاح."""
        from aiogram import Bot

        url = TenantService.webhook_url(tenant)
        token = TenantService.reveal_token(tenant)
        bot = Bot(token=token)
        try:
            await bot.set_webhook(url, drop_pending_updates=True)
            tenant.webhook_set = True
            await session.commit()
            return True
        except Exception:
            logger.exception("فشل تسجيل webhook للمستأجر %s", tenant.id)
            return False
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass

    @staticmethod
    async def unregister_webhook(session, tenant: Tenant) -> None:
        from aiogram import Bot

        try:
            token = TenantService.reveal_token(tenant)
        except TenantError:
            tenant.webhook_set = False
            await session.commit()
            return
        bot = Bot(token=token)
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            logger.warning("تعذر حذف webhook للمستأجر %s", tenant.id)
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass
        tenant.webhook_set = False
        await session.commit()

    @staticmethod
    async def push_branding(tenant: Tenant, *, name: str, description: str) -> bool:
        """يدفع الهوية (الاسم/الوصف/الأوامر) لبوت التاجر عبر Bot API."""
        from aiogram import Bot
        from aiogram.types import BotCommand, MenuButtonCommands

        token = TenantService.reveal_token(tenant)
        bot = Bot(token=token)
        try:
            await bot.set_my_name(name[:64])
            if description:
                await bot.set_my_description(description[:512])
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="القائمة الرئيسية"),
                    BotCommand(command="support", description="الدعم الفني"),
                ]
            )
            try:
                await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
            except Exception:
                pass
            return True
        except Exception:
            logger.exception("فشل دفع الهوية للمستأجر %s", tenant.id)
            return False
        finally:
            try:
                await bot.session.close()
            except Exception:
                pass
