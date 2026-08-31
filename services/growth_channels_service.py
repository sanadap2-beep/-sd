"""
الدفعات الأخيرة: أسهم حصة الإحالة + مهام مقابل رصيد + غرف شراء جماعي
+ محرك نمو ذاتي + متجر عام + تعدد قنوات + طلب صوتي + ذكاء سوق.

1) أسهم حصة الإحالة: من جلب 100 عميل يبيع جزءاً من عمولته المستقبلية
   مقابل كاش فوري. اقتصاد ثانوي كامل حول البوت.

2) مهام مقابل رصيد: كسب رصيد عبر مهام مفيدة للبوت (تقييم، ترجمة،
   إبلاغ)، فيفتح أسواقاً يصعب فيها الدفع الإلكتروني.

3) غرف شراء جماعي: مستخدمون يتجمعون على طلب كبير لفتح خصم الجملة.
   النمو يأتي من المستخدمين لا من الإعلانات.

4) محرك نمو ذاتي: اختبارات A/B دائمة على الأسعار والعروض مع فوز
   تلقائي، فيصير التسويق نظاماً لا شغلاً يومياً.

5) متجر عام: صفحات مفهرسة لكل منتج، فجلب زيارات عضوية.

6) تعدد قنوات: نفس المحرك عبر واتساب وديسكورد وإضافة متصفح.

7) طلب صوتي: تحويل صوت المستخدم نصاً والرد صوتياً.

8) ذكاء سوق: تقارير مجمعة مجهولة الهوية عن اتجاهات الطلب.

كلها قابلة للإيقاف والضبط من مركز الإضافات.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN

from sqlalchemy import desc, func, select

from database.models import (
    Category,
    CategoryType,
    NumberOrder,
    Product,
    ProductStatus,
    SubCategory,
    TransactionType,
    UnifiedOrder,
    User,
)
from services.balance_service import BalanceService
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class GrowthError(Exception):
    pass


class RevenueShareService:
    """بيع جزء من العمولة المستقبلية مقابل كاش فوري."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("revenue_sharing_tokens")

    @staticmethod
    async def max_share_percent() -> int:
        return max(1, await FeatureService.config_int("revenue_sharing_tokens", "max_share_percent", 50))

    @staticmethod
    async def projected_commission(session, user_id: int, days: int = 30) -> Decimal:
        """ما جناه المستخدم من إحالات خلال الفترة — أساس التقييم."""
        since = datetime.utcnow() - timedelta(days=max(1, days))
        result = await session.execute(
            select(func.coalesce(func.sum(User.total_spent_usd), 0)).where(
                User.referrer_id == user_id
            )
        )
        volume = Decimal(str(result.scalar_one() or 0))
        from services.growth_service import AffiliateService

        percents = await AffiliateService.percents()
        rate = percents[0] if percents else Decimal("5")
        return (volume * rate / Decimal("100")).quantize(Decimal("0.0001"))

    @staticmethod
    async def issue(session, seller_id: int, share_percent: int, price_usd: Decimal) -> dict:
        """يصدر سهماً: البائع يأخذ الكاش الآن، والمشتري يأخذ النسبة لاحقاً."""
        if not await RevenueShareService.enabled():
            raise GrowthError("أسهم حصة الإحالة موقوفة حالياً.")
        if share_percent <= 0 or share_percent > await RevenueShareService.max_share_percent():
            raise GrowthError(
                f"النسبة يجب أن تكون بين 1 و{await RevenueShareService.max_share_percent()}%."
            )
        if price_usd <= 0:
            raise GrowthError("السعر يجب أن يكون موجباً.")

        from database.models import RevenueShareToken

        token = RevenueShareToken(
            issuer_id=seller_id,
            share_percent=share_percent,
            price_usd=price_usd,
            status="active",
        )
        session.add(token)
        await session.flush()

        # البائع يقبض الآن
        await BalanceService.add_balance(
            session, seller_id, price_usd, TransactionType.ADMIN_ADD,
            description=f"بيع حصة إحالة {share_percent}%",
            payment_reference=f"revshare_issue:{token.id}",
        )
        await session.commit()
        await session.refresh(token)
        await FeatureService.track("revenue_sharing_tokens", "issued", user_id=seller_id)
        return {"token_id": token.id, "share_percent": share_percent, "price_usd": price_usd}

    @staticmethod
    async def buy(session, token_id: int, buyer_id: int) -> bool:
        from database.models import RevenueShareToken

        token = await session.get(RevenueShareToken, token_id)
        if token is None or token.status != "active" or token.holder_id is not None:
            return False
        if token.issuer_id == buyer_id:
            return False
        await BalanceService.deduct_balance(
            session, buyer_id, token.price_usd, TransactionType.PURCHASE,
            description=f"شراء حصة إحالة {token.share_percent}%", is_purchase=True,
        )
        token.holder_id = buyer_id
        await session.commit()
        await FeatureService.track("revenue_sharing_tokens", "bought", user_id=buyer_id)
        return True

    @staticmethod
    async def distribute(session, issuer_id: int, amount_usd: Decimal) -> list[dict]:
        """يوزع عمولة مستحقة على حاملي الأسهم."""
        if not await RevenueShareService.enabled() or amount_usd <= 0:
            return []
        from database.models import RevenueShareToken

        result = await session.execute(
            select(RevenueShareToken).where(
                RevenueShareToken.issuer_id == issuer_id,
                RevenueShareToken.holder_id.is_not(None),
                RevenueShareToken.status == "active",
            )
        )
        paid = []
        for token in result.scalars().all():
            share = (
                amount_usd * Decimal(token.share_percent) / Decimal("100")
            ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
            if share <= 0:
                continue
            await BalanceService.add_balance(
                session, token.holder_id, share, TransactionType.REFERRAL_BONUS,
                description=f"حصة إحالة {token.share_percent}%",
                payment_reference=f"revshare:{token.id}:{int(amount_usd * 10000)}",
            )
            paid.append({"holder_id": token.holder_id, "share_usd": share})
        await session.commit()
        return paid


class TaskToCreditService:
    """مهام مفيدة للبوت مقابل رصيد، لا نقاط فقط."""

    TASK_TYPES = {
        "review_product": ("تقييم منتج", Decimal("0.05")),
        "translate_text": ("ترجمة نص", Decimal("0.15")),
        "report_provider": ("إبلاغ عن مزود سيء", Decimal("0.10")),
        "invite_friend": ("دعوة صديق مفعّل", Decimal("0.25")),
    }

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("task_to_credit")

    @staticmethod
    async def reward_for(task_type: str) -> Decimal:
        default = TaskToCreditService.TASK_TYPES.get(task_type, ("", Decimal("0.05")))[1]
        return Decimal(str(await FeatureService.config(
            "task_to_credit", f"reward_{task_type}", str(default)
        )))

    @staticmethod
    async def complete(session, user_id: int, task_type: str, proof: str = "") -> dict:
        """يكافئ المستخدم رصيداً على مهمة مفيدة، مرة واحدة لكل دليل."""
        if not await TaskToCreditService.enabled():
            raise GrowthError("المهام مقابل الرصيد موقوفة حالياً.")
        if task_type not in TaskToCreditService.TASK_TYPES:
            raise GrowthError("نوع مهمة غير معروف.")

        reward = await TaskToCreditService.reward_for(task_type)
        if reward <= 0:
            raise GrowthError("مكافأة هذه المهمة صفر.")

        # مفتاح idempotent من الدليل نفسه، فلا تُصرف المهمة مرتين
        key = f"task2credit:{user_id}:{task_type}:{proof or 'default'}"
        from database.models import LoyaltyEvent

        existing = (
            await session.execute(
                select(LoyaltyEvent).where(LoyaltyEvent.event_key == key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise GrowthError("سبق أن كوفئت على هذه المهمة.")

        await BalanceService.add_balance(
            session, user_id, reward, TransactionType.ADMIN_ADD,
            description=f"مكافأة مهمة: {TaskToCreditService.TASK_TYPES[task_type][0]}",
            payment_reference=key,
        )
        session.add(
            LoyaltyEvent(
                user_id=user_id, event_key=key, event_type="task_to_credit",
                points=0, description=task_type,
            )
        )
        await session.commit()
        await FeatureService.track("task_to_credit", "completed", user_id=user_id, value=task_type)
        return {"task": task_type, "reward_usd": reward}


class PooledRoomService:
    """غرف شراء جماعي: تجمّع على طلب كبير لفتح خصم الجملة."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("pooled_rooms")

    @staticmethod
    async def min_members() -> int:
        return max(2, await FeatureService.config_int("pooled_rooms", "min_members", 5))

    @staticmethod
    async def create(session, creator_id: int, product_id: int, target_quantity: int) -> dict:
        if not await PooledRoomService.enabled():
            raise GrowthError("غرف الشراء الجماعي موقوفة حالياً.")
        product = await session.get(Product, product_id)
        if product is None or product.status != ProductStatus.ACTIVE:
            raise GrowthError("المنتج غير متاح.")

        from database.models import PurchaseRoom

        room = PurchaseRoom(
            creator_id=creator_id,
            product_id=product_id,
            target_quantity=target_quantity,
            members=1,
            status="open",
            expires_at=datetime.utcnow() + timedelta(hours=48),
        )
        session.add(room)
        await session.commit()
        await session.refresh(room)
        await FeatureService.track("pooled_rooms", "created", user_id=creator_id)
        return {"room_id": room.id, "target_quantity": target_quantity}

    @staticmethod
    async def join(session, room_id: int, user_id: int) -> dict:
        from database.models import PurchaseRoom

        room = await session.get(PurchaseRoom, room_id)
        if room is None or room.status != "open":
            raise GrowthError("الغرفة لم تعد مفتوحة.")
        if room.expires_at and room.expires_at < datetime.utcnow():
            room.status = "expired"
            await session.commit()
            raise GrowthError("انتهت مدة الغرفة.")

        room.members = (room.members or 0) + 1
        threshold = await PooledRoomService.min_members()
        ready = room.members >= threshold
        if ready:
            room.status = "ready"
        await session.commit()
        await FeatureService.track("pooled_rooms", "joined", user_id=user_id)
        return {"members": room.members, "needed": threshold, "ready": ready}

    @staticmethod
    async def ready_rooms(session) -> list:
        from database.models import PurchaseRoom

        result = await session.execute(
            select(PurchaseRoom).where(PurchaseRoom.status == "ready")
        )
        return list(result.scalars().all())


class GrowthOptimizerService:
    """اختبارات A/B دائمة مع فوز تلقائي."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("growth_optimizer")

    @staticmethod
    async def record_variant(session, experiment: str, variant: str, converted: bool) -> None:
        """يسجل نتيجة عرض واحد."""
        if not await GrowthOptimizerService.enabled():
            return
        from database.models import ExperimentResult

        session.add(
            ExperimentResult(
                experiment=experiment[:64],
                variant=variant[:32],
                converted=bool(converted),
            )
        )
        await session.commit()

    @staticmethod
    async def winner(session, experiment: str, min_samples: int = 30) -> dict | None:
        """
        يحدد الفائز بمقارنة معدلات التحويل.
        لا يعلن فائزاً قبل حد أدنى من العيّنات حتى لا يقرر على ضجيج.
        """
        if not await GrowthOptimizerService.enabled():
            return None
        from sqlalchemy import Integer as _Integer, case as _case, cast as _cast

        from database.models import ExperimentResult

        # الانتباه: SUM على عمود Boolean يرث نوع Boolean، فيحوّل SQLAlchemy
        # الناتج إلى True/False ويصير كل متغير درجته 1 مهما كانت عيناته.
        # لذلك نجمعه صراحةً كعدد صحيح.
        result = await session.execute(
            select(
                ExperimentResult.variant,
                func.count(ExperimentResult.id),
                func.sum(_cast(ExperimentResult.converted, _Integer)),
            )
            .where(ExperimentResult.experiment == experiment)
            .group_by(ExperimentResult.variant)
        )
        rows = []
        for variant, total, converted in result.all():
            total = int(total or 0)
            if total < min_samples:
                return None  # عيّنات غير كافية
            rows.append(
                {
                    "variant": variant,
                    "samples": total,
                    "conversion_rate": round(int(converted or 0) / total * 100, 2),
                }
            )
        if len(rows) < 2:
            return None
        rows.sort(key=lambda r: r["conversion_rate"], reverse=True)
        return {"winner": rows[0], "all": rows}


class PublicStorefrontService:
    """صفحات عامة مفهرسة لكل منتج — زيارات عضوية بلا إعلانات."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("public_storefront")

    @staticmethod
    async def page_for(session, product_id: int) -> dict | None:
        """بيانات صفحة منتج عامة (بلا أسعار تكلفة ولا بيانات داخلية)."""
        if not await PublicStorefrontService.enabled():
            return None
        product = await session.get(Product, product_id)
        if product is None or product.status != ProductStatus.ACTIVE:
            return None
        sub = (
            await session.get(SubCategory, product.sub_category_id)
            if product.sub_category_id
            else None
        )
        category = await session.get(Category, sub.category_id) if sub else None
        from services.review_service import ReviewService

        rating, review_count = await ReviewService.product_summary(session, product_id)
        return {
            "id": product.id,
            "title": product.name_ar,
            "description": product.description,
            "price_usd": product.price_usd,
            "category": category.name_ar if category else None,
            "subcategory": sub.name_ar if sub else None,
            "rating": rating,
            "review_count": review_count,
            "sold": product.total_sold or 0,
            "slug": _slug(product.name_ar, product.id),
        }

    @staticmethod
    async def sitemap(session, limit: int = 500) -> list[str]:
        if not await PublicStorefrontService.enabled():
            return []
        result = await session.execute(
            select(Product.id, Product.name_ar)
            .where(Product.status == ProductStatus.ACTIVE)
            .limit(limit)
        )
        return [_slug(name, pid) for pid, name in result.all()]


def _slug(name: str, product_id: int) -> str:
    import re

    cleaned = re.sub(r"[^\w\u0600-\u06ff]+", "-", (name or "").strip()).strip("-")
    return f"{cleaned[:40]}-{product_id}" if cleaned else f"product-{product_id}"


class OmnichannelService:
    """نفس المحرك عبر قنوات متعددة."""

    CHANNELS = ("telegram", "whatsapp", "discord", "browser_extension")

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("omnichannel")

    @staticmethod
    async def enabled_channels() -> list[str]:
        if not await OmnichannelService.enabled():
            return ["telegram"]
        out = []
        for channel in OmnichannelService.CHANNELS:
            if channel == "telegram":
                out.append(channel)
                continue
            if await FeatureService.config_bool("omnichannel", f"{channel}_enabled", False):
                out.append(channel)
        return out

    @staticmethod
    async def normalize_inbound(channel: str, payload: dict) -> dict:
        """
        يوحّد رسالة واردة من أي قناة إلى صيغة واحدة.
        بقية البوت لا يعرف من أين جاءت الرسالة.
        """
        if channel == "telegram":
            return {
                "channel": channel,
                "user_ref": str(payload.get("from_id", "")),
                "text": payload.get("text", ""),
            }
        if channel == "whatsapp":
            return {
                "channel": channel,
                "user_ref": str(payload.get("wa_id", "")),
                "text": (payload.get("message") or {}).get("body", ""),
            }
        if channel == "discord":
            return {
                "channel": channel,
                "user_ref": str((payload.get("author") or {}).get("id", "")),
                "text": payload.get("content", ""),
            }
        return {
            "channel": channel,
            "user_ref": str(payload.get("user_ref", "")),
            "text": payload.get("text", ""),
        }

    @staticmethod
    async def autofill_payload(session, phone_number: str, code: str) -> dict:
        """
        حمولة لإضافة المتصفح: تملأ حقل الكود في أي موقع بضغطة.
        """
        if not await OmnichannelService.enabled():
            raise GrowthError("تعدد القنوات موقوف حالياً.")
        return {
            "action": "autofill",
            "selector_hint": "input[type=tel], input[name*=code], input[name*=otp]",
            "value": code,
            "phone": phone_number,
            "ts": int(datetime.utcnow().timestamp()),
        }


class VoiceOrderingService:
    """طلب صوتي: تحويل الكلام نصاً والرد صوتياً."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("voice_ordering")

    @staticmethod
    async def transcribe(file_path: str, language: str = "ar") -> tuple[str | None, bool]:
        """
        يحوّل تسجيلاً صوتياً إلى نص.
        يرجع (None, False) عند غياب الإعداد فيُستخدم الإدخال النصي.
        """
        if not await VoiceOrderingService.enabled():
            return None, False
        provider = await FeatureService.config("voice_ordering", "stt_provider", "none")
        if provider == "none":
            return None, False

        import aiohttp
        from services.settings_service import SettingsService

        api_key = await SettingsService.get(f"stt_api_key_{provider}", "")
        if not api_key:
            return None, False
        try:
            url = {
                "openai": "https://api.openai.com/v1/audio/transcriptions",
            }.get(provider)
            if url is None:
                return None, False
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as http:
                data = aiohttp.FormData()
                data.add_field("file", open(file_path, "rb"), filename="voice.ogg")
                data.add_field("model", "whisper-1")
                data.add_field("language", language)
                async with http.post(
                    url, data=data, headers={"Authorization": f"Bearer {api_key}"}
                ) as response:
                    if response.status != 200:
                        return None, False
                    payload = await response.json()
            text = payload.get("text")
            return (text, True) if text else (None, False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("تعذّر تحويل الصوت: %s", exc)
            return None, False

    @staticmethod
    def parse_intent(text: str) -> dict:
        """
        يستخرج النية من نص منطوق بأبسط صورة موثوقة.

        الانتباه إلى الترتيب: "رصيدي" تحتوي "رصيد"، فلو فُحصت نية الشحن
        أولاً لصار كل استعلام عن الرصيد طلب شحن. لذلك تُفحص الاستعلامات
        الأكثر تحديداً قبل الأعم.
        """
        lowered = (text or "").casefold()
        intent = "unknown"
        if any(w in lowered for w in ("رصيدي", "رصيدى", "كم عندي", "balance", "كم رصيد")):
            intent = "check_balance"
        elif any(w in lowered for w in ("رصيد", "شحن", "اشحن", "topup", "deposit")):
            intent = "deposit"
        elif any(w in lowered for w in ("رقم", "number", "كود", "تفعيل")):
            intent = "buy_number"
        elif any(w in lowered for w in ("متابع", "follower", "لايك", "رشق")):
            intent = "buy_smm"
        return {"intent": intent, "text": text}


class MarketIntelligenceService:
    """تقارير مجمعة مجهولة الهوية عن اتجاهات الطلب."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("market_intelligence")

    @staticmethod
    async def demand_by_country(session, days: int = 30) -> list[dict]:
        """أي الدول زاد الطلب عليها — بلا أي بيانات مستخدم."""
        if not await MarketIntelligenceService.enabled():
            return []
        if not await FeatureService.config_bool("market_intelligence", "anonymize", True):
            logger.warning("ذكاء السوق يتطلب إخفاء الهوية؛ أُوقف التقرير.")
            return []

        since = datetime.utcnow() - timedelta(days=max(1, days))
        result = await session.execute(
            select(NumberOrder.country_code, func.count(NumberOrder.id))
            .where(NumberOrder.purchased_at >= since)
            .group_by(NumberOrder.country_code)
            .order_by(desc(func.count(NumberOrder.id)))
        )
        return [{"country": code, "orders": int(count)} for code, count in result.all()]

    @staticmethod
    async def demand_by_service(session, days: int = 30) -> list[dict]:
        """أي الخدمات الأكثر طلباً (واتساب/تليجرام/إنستغرام)."""
        if not await MarketIntelligenceService.enabled():
            return []
        since = datetime.utcnow() - timedelta(days=max(1, days))
        result = await session.execute(
            select(NumberOrder.service, func.count(NumberOrder.id))
            .where(NumberOrder.purchased_at >= since)
            .group_by(NumberOrder.service)
            .order_by(desc(func.count(NumberOrder.id)))
        )
        return [{"service": code, "orders": int(count)} for code, count in result.all()]

    @staticmethod
    async def report(session, days: int = 30) -> dict:
        """التقرير الكامل — يُباع لأبحاث السوق كمصدر دخل ثانٍ."""
        if not await MarketIntelligenceService.enabled():
            return {"available": False}
        since = datetime.utcnow() - timedelta(days=max(1, days))
        volume = (
            await session.execute(
                select(func.coalesce(func.sum(NumberOrder.price_sell_usd), 0)).where(
                    NumberOrder.purchased_at >= since
                )
            )
        ).scalar_one()
        return {
            "available": True,
            "period_days": days,
            "total_volume_usd": Decimal(str(volume or 0)),
            "by_country": await MarketIntelligenceService.demand_by_country(session, days),
            "by_service": await MarketIntelligenceService.demand_by_service(session, days),
            "generated_at": datetime.utcnow().isoformat(),
            "anonymized": True,
        }
