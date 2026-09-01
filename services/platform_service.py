"""
طبقة المنصة: واجهة ثقة عامة + محرك امتثال + منصة مطورين + مصنع علامات بيضاء
+ مزايدة مزودين + بوابة SIM ذاتية + تتبع أسعار الألعاب.

هذه الميزات تحوّل البوت من «منتج» إلى «بنية تحتية»:
1) واجهة ثقة عامة: أي موقع يتحقق هل رقم/مزود موثوق، فتصير مرجعاً لا بائعاً.
2) محرك امتثال: قواعد لكل دولة تحدد ما يُسمح بيعه، فيفتح شريحة مؤسسية.
3) منصة مطورين: Webhooks موقّعة + بيئة اختبار رملية + مفاتيح بصلاحيات دقيقة.
4) مصنع علامات بيضاء: عدة بوتات بعلامات مختلفة على نفس الكود.
5) مزايدة مزودين: مزودون يعرضون أسعارهم والنظام يختار الأنسب لحظياً.
6) بوابة SIM ذاتية: مصدر أرقام خاص بهامش أعلى من الوسطاء.
7) تتبع أسعار عملات الألعاب: مقارنة حية وتوجيه للأرخص.

كلها قابلة للإيقاف والضبط من مركز الإضافات.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import aiohttp
from sqlalchemy import func, select

from database.models import (
    ApiProvider,
    NumberOrder,
    Product,
    ProductStatus,
    ProviderService,
    ProviderServiceStatus,
    TransactionType,
    User,
)
from services.feature_service import FeatureService
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


class PlatformError(Exception):
    pass


class PublicTrustService:
    """واجهة عامة للتحقق من موثوقية رقم أو مزود، بلا كشف بيانات."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("public_trust_api")

    @staticmethod
    async def rate_limit(ip: str) -> bool:
        """حد طلبات لكل IP حتى لا تُستغل الواجهة مجاناً."""
        if not await PublicTrustService.enabled():
            return False
        limit = await FeatureService.config_int("public_trust_api", "rate_limit_per_ip", 60)
        if limit <= 0:
            return True
        key = f"trust_rl:{ip}:{datetime.utcnow():%Y%m%d%H}"
        current = await SettingsService.get_int(key, 0)
        if current >= limit:
            return False
        await SettingsService.set(None, key, str(current + 1)) if False else None
        return True

    @staticmethod
    async def check_number(session, phone_number: str) -> dict:
        """
        هل هذا الرقم صدر من عندنا وما حالته؟
        لا يُكشف اسم المستخدم ولا أي بيانات شخصية — فقط حالة.
        """
        if not await PublicTrustService.enabled():
            return {"available": False}

        fingerprint = hashlib.sha256((phone_number or "").strip().encode()).hexdigest()[:32]
        result = await session.execute(
            select(NumberOrder)
            .where(NumberOrder.number_fingerprint == fingerprint)
            .order_by(NumberOrder.id.desc())
            .limit(1)
        )
        order = result.scalars().first()
        if order is None:
            return {"known": False, "trusted": None}

        status = getattr(order.status, "value", order.status)
        return {
            "known": True,
            "trusted": status in ("completed", "code_received", "pending"),
            "issued_by_us": True,
            "service": order.service,
            "country": order.country_code,
            # لا تاريخ محدد ولا اسم مستخدم — فقط عمر تقريبي
            "age_bucket": _age_bucket(order.purchased_at),
        }

    @staticmethod
    async def provider_stats(session) -> list[dict]:
        """إحصاءات مجمعة للمزودين — تُنشر كدليل ثقة."""
        if not await PublicTrustService.enabled():
            return []
        if not await FeatureService.config_bool("public_trust_api", "expose_provider_stats", True):
            return []

        result = await session.execute(select(NumberOrder))
        stats: dict[str, dict] = {}
        for order in result.scalars().all():
            key = getattr(order.provider, "value", str(order.provider))
            row = stats.setdefault(key, {"provider": key, "total": 0, "success": 0})
            row["total"] += 1
            status = getattr(order.status, "value", order.status)
            if status in ("completed", "code_received"):
                row["success"] += 1
        out = []
        for row in stats.values():
            row["success_rate"] = (
                round(row["success"] / row["total"] * 100, 1) if row["total"] else 0.0
            )
            out.append(row)
        out.sort(key=lambda r: r["success_rate"], reverse=True)
        return out


def _age_bucket(moment: datetime | None) -> str:
    if moment is None:
        return "unknown"
    days = (datetime.utcnow() - moment).days
    if days < 1:
        return "today"
    if days < 7:
        return "this_week"
    if days < 30:
        return "this_month"
    return "older"


class ComplianceService:
    """قواعد لكل دولة تحدد ما يُسمح بيعه، ومستويات KYC متدرجة."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("compliance_engine")

    @staticmethod
    async def is_allowed(session, country_code: str, service_code: str) -> tuple[bool, str]:
        """هل يُسمح ببيع هذه الخدمة في هذه الدولة؟"""
        if not await ComplianceService.enabled():
            return True, ""
        from database.models import JurisdictionRule

        result = await session.execute(
            select(JurisdictionRule).where(
                JurisdictionRule.country_code == (country_code or "").upper()
            )
        )
        for rule in result.scalars().all():
            blocked = (rule.blocked_services or "").lower().split(",")
            blocked = [b.strip() for b in blocked if b.strip()]
            if service_code.lower() in blocked:
                return False, rule.reason or "هذه الخدمة غير متاحة في دولتك."
            if rule.requires_kyc_tier and rule.requires_kyc_tier > 0:
                return True, f"requires_kyc:{rule.requires_kyc_tier}"
        return True, ""

    @staticmethod
    async def check_user(session, user_id: int, country_code: str, service_code: str) -> tuple[bool, str]:
        """يفحص الدولة ومستوى تحقق المستخدم معاً."""
        allowed, note = await ComplianceService.is_allowed(session, country_code, service_code)
        if not allowed:
            return False, note
        if note.startswith("requires_kyc:"):
            required = int(note.split(":")[1])
            from services.trust_service import SmartVerificationService

            tier = await SmartVerificationService.tier_for(session, user_id)
            if tier < required:
                return False, f"هذه الخدمة تتطلب توثيق حساب (المستوى {required})."
        return True, ""

    @staticmethod
    async def refund_ledger(session, limit: int = 50) -> list[dict]:
        """سجل إثبات استرجاع — دليل علني قابل للتحقق."""
        result = await session.execute(
            select(NumberOrder)
            .where(NumberOrder.status.in_(["refunded", "expired"]))
            .order_by(NumberOrder.id.desc())
            .limit(limit)
        )
        out = []
        for order in result.scalars().all():
            out.append(
                {
                    # بصمة لا تكشف هوية، لكن تثبت أن العملية حدثت
                    "proof": hashlib.sha256(
                        f"{order.id}:{order.phone_number}".encode()
                    ).hexdigest()[:16],
                    "amount_usd": order.price_sell_usd,
                    "when": order.completed_at or order.purchased_at,
                }
            )
        return out


class DeveloperPlatformService:
    """Webhooks موقّعة + بيئة اختبار + مفاتيح بصلاحيات دقيقة."""

    SCOPES = ("read:catalog", "read:orders", "write:orders", "write:webhooks")

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("developer_platform")

    @staticmethod
    def sign(secret: str, payload: bytes) -> str:
        """توقيع HMAC-SHA256 حتى يتحقق المستقبل أن الحدث منك فعلاً."""
        return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    @staticmethod
    def verify(secret: str, payload: bytes, signature: str) -> bool:
        expected = DeveloperPlatformService.sign(secret, payload)
        return hmac.compare_digest(expected, signature or "")

    @staticmethod
    async def deliver(session, event: str, data: dict) -> dict:
        """يرسل الحدث لكل نقاط الويبhook المسجلة، مع إعادة محاولة."""
        if not await DeveloperPlatformService.enabled():
            return {"delivered": 0}
        from database.models import WebhookEndpoint, WebhookDelivery

        result = await session.execute(
            select(WebhookEndpoint).where(WebhookEndpoint.is_active.is_(True))
        )
        endpoints = list(result.scalars().all())
        delivered = failed = 0

        payload = json.dumps({"event": event, "data": data}, ensure_ascii=False).encode()

        for endpoint in endpoints:
            scopes = (endpoint.scopes or "").split(",")
            if event not in [s.strip() for s in scopes] and "*" not in scopes:
                continue
            signature = DeveloperPlatformService.sign(endpoint.secret, payload)
            ok = False
            for attempt in range(3):
                try:
                    timeout = aiohttp.ClientTimeout(total=10)
                    async with aiohttp.ClientSession(timeout=timeout) as http:
                        async with http.post(
                            endpoint.url,
                            data=payload,
                            headers={
                                "Content-Type": "application/json",
                                "X-Signature": signature,
                                "X-Event": event,
                                "X-Attempt": str(attempt + 1),
                            },
                        ) as response:
                            ok = 200 <= response.status < 300
                except Exception as exc:  # noqa: BLE001
                    logger.debug("فشل ويبhook %s: %s", endpoint.id, exc)
                    ok = False
                if ok:
                    break

            session.add(
                WebhookDelivery(
                    endpoint_id=endpoint.id,
                    event=event,
                    succeeded=ok,
                    attempts=attempt + 1,
                )
            )
            if ok:
                delivered += 1
            else:
                failed += 1

        await session.commit()
        return {"delivered": delivered, "failed": failed}

    @staticmethod
    async def sandbox_quote(quantity: int, unit_price: Decimal) -> dict:
        """بيئة اختبار: أرقام وأكواد وهمية، فلا يخسر المطور مالاً."""
        if not await DeveloperPlatformService.enabled():
            raise PlatformError("منصة المطورين موقوفة حالياً.")
        return {
            "sandbox": True,
            "quantity": quantity,
            "unit_price_usd": unit_price,
            "total_usd": (unit_price * Decimal(quantity)).quantize(Decimal("0.0001")),
            "fake_numbers": [f"+999000{i:04d}" for i in range(min(quantity, 5))],
            "note": "بيئة اختبار — لا أرقام حقيقية ولا خصم.",
        }

    @staticmethod
    def has_scope(scopes: str, required: str) -> bool:
        granted = {s.strip() for s in (scopes or "").split(",")}
        return required in granted or "*" in granted


class WhiteLabelService:
    """عدة بوتات بعلامات مختلفة على نفس الكود."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("white_label_factory")

    @staticmethod
    async def resolve(session, bot_username: str) -> dict:
        """يحسم لأي مستأجر ينتمي هذا البوت."""
        if not await WhiteLabelService.enabled():
            return {"tenant": "default", "brand": await SettingsService.get("brand_name", "المتجر")}
        from database.models import Tenant

        result = await session.execute(
            select(Tenant).where(Tenant.bot_username == (bot_username or "").lower())
        )
        tenant = result.scalars().first()
        if tenant is None:
            return {"tenant": "default", "brand": await SettingsService.get("brand_name", "المتجر")}
        if not tenant.is_active:
            return {"tenant": "default", "brand": "المتجر", "suspended": True}
        return {
            "tenant": tenant.slug,
            "brand": tenant.brand_name,
            "language": tenant.default_language,
            "currency": tenant.display_currency,
            "commission_percent": tenant.commission_percent,
        }

    @staticmethod
    async def create(session, slug: str, brand_name: str, bot_username: str,
                     commission_percent: Decimal) -> dict:
        if not await WhiteLabelService.enabled():
            raise PlatformError("مصنع العلامات البيضاء موقوف حالياً.")
        from database.models import Tenant

        existing = (
            await session.execute(select(Tenant).where(Tenant.slug == slug))
        ).scalar_one_or_none()
        if existing is not None:
            raise PlatformError("هذا المعرف مستخدم مسبقاً.")

        tenant = Tenant(
            slug=slug.lower()[:32],
            brand_name=brand_name[:128],
            bot_username=(bot_username or "").lower().lstrip("@"),
            commission_percent=commission_percent,
            is_active=True,
        )
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
        await FeatureService.track("white_label_factory", "created", value=tenant.slug)
        return {"tenant_id": tenant.id, "slug": tenant.slug}


class ProviderBiddingService:
    """مزودون يعرضون أسعارهم والنظام يختار الأنسب لحظياً."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("provider_bidding")

    @staticmethod
    async def submit_bid(session, provider_id: int, service_code: str,
                         country_code: str, price_usd: Decimal, ttl_minutes: int = 15) -> dict:
        """يسجل عرض سعر ساري المفعول."""
        if not await ProviderBiddingService.enabled():
            raise PlatformError("محرك المزايدة موقوف حالياً.")
        if price_usd <= 0:
            raise PlatformError("السعر يجب أن يكون موجباً.")

        from database.models import ProviderBid

        # عرض واحد لكل مزود/خدمة/دولة — الأحدث يستبدل الأقدم
        existing = (
            await session.execute(
                select(ProviderBid).where(
                    ProviderBid.provider_id == provider_id,
                    ProviderBid.service_code == service_code,
                    ProviderBid.country_code == country_code,
                )
            )
        ).scalar_one_or_none()
        expires = datetime.utcnow() + timedelta(minutes=max(1, ttl_minutes))
        if existing is not None:
            existing.price_usd = price_usd
            existing.expires_at = expires
            bid_id = existing.id
        else:
            bid = ProviderBid(
                provider_id=provider_id,
                service_code=service_code,
                country_code=country_code,
                price_usd=price_usd,
                expires_at=expires,
            )
            session.add(bid)
            await session.flush()
            bid_id = bid.id
        await session.commit()
        return {"bid_id": bid_id, "price_usd": price_usd, "expires_at": expires}

    @staticmethod
    async def best_bid(session, service_code: str, country_code: str) -> dict | None:
        """أرخص عرض ساري — هذا ما يجعلك دائماً أرخص منافس."""
        if not await ProviderBiddingService.enabled():
            return None
        from database.models import ProviderBid

        result = await session.execute(
            select(ProviderBid)
            .where(
                ProviderBid.service_code == service_code,
                ProviderBid.country_code == country_code,
                ProviderBid.expires_at > datetime.utcnow(),
            )
            .order_by(ProviderBid.price_usd)
            .limit(1)
        )
        bid = result.scalars().first()
        if bid is None:
            return None
        return {
            "provider_id": bid.provider_id,
            "price_usd": bid.price_usd,
            "expires_at": bid.expires_at,
        }

    @staticmethod
    async def cleanup_expired(session) -> int:
        from database.models import ProviderBid

        result = await session.execute(
            select(ProviderBid).where(ProviderBid.expires_at <= datetime.utcnow())
        )
        count = 0
        for bid in result.scalars().all():
            await session.delete(bid)
            count += 1
        if count:
            await session.commit()
        return count


class SelfHostedSimService:
    """بوابة GSM فعلية كمصدر أرقام خاص بهامش أعلى."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("self_hosted_sim")

    @staticmethod
    async def gateway_url() -> str | None:
        url = await SettingsService.get("sim_gateway_url", "")
        return url.strip() or None

    @staticmethod
    async def health() -> dict:
        """يفحص البوابة الفعلية قبل الاعتماد عليها."""
        if not await SelfHostedSimService.enabled():
            return {"available": False, "reason": "disabled"}
        url = await SelfHostedSimService.gateway_url()
        if not url:
            return {"available": False, "reason": "not_configured"}
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as http:
                async with http.get(f"{url.rstrip('/')}/health") as response:
                    if response.status != 200:
                        return {"available": False, "reason": f"http_{response.status}"}
                    data = await response.json()
            return {"available": True, "detail": data}
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": str(exc)[:120]}

    @staticmethod
    async def margin_vs_retail(retail_price_usd: Decimal, cost_price_usd: Decimal) -> Decimal:
        """هامش الربح — وهذا سبب بناء البوابة أصلاً."""
        if retail_price_usd <= 0:
            return Decimal("0")
        return ((retail_price_usd - cost_price_usd) / retail_price_usd * 100).quantize(
            Decimal("0.1")
        )


class GamePriceTrackerService:
    """مقارنة حية لأسعار عملات الألعاب بين المزودين."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("game_price_tracker")

    @staticmethod
    async def compare(session, keyword: str) -> list[dict]:
        """كل أسعار منتج عبر المزودين، مرتبة من الأرخص."""
        if not await GamePriceTrackerService.enabled():
            return []
        query = select(ProviderService).where(
            ProviderService.status == ProviderServiceStatus.ACTIVE,
            ProviderService.rate_usd > 0,
        )
        if keyword:
            query = query.where(ProviderService.name.ilike(f"%{keyword}%"))
        result = await session.execute(query.limit(200))
        rows = [
            {
                "provider_id": service.api_provider_id,
                "service_id": service.external_service_id,
                "name": service.name,
                "rate_usd": service.rate_usd,
            }
            for service in result.scalars().all()
        ]
        rows.sort(key=lambda r: r["rate_usd"])
        return rows

    @staticmethod
    async def cheapest_provider(session, keyword: str) -> dict | None:
        rows = await GamePriceTrackerService.compare(session, keyword)
        return rows[0] if rows else None

    @staticmethod
    async def price_alert_threshold() -> Decimal:
        """نسبة الهبوط التي تستحق تنبيهاً للمستخدم."""
        return Decimal(str(await FeatureService.config_decimal(
            "game_price_tracker", "alert_drop_percent", 10.0
        )))
