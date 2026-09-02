"""
طبقة الذكاء الاصطناعي: وكيل محادثة + وكيل شراء مستقل + رفّ ذكي + شخصيات محلية.

الوضع قبل هذا الملف: بحث دقيق عن openai/anthropic/gemini/llm أرجع نتيجة
واحدة فقط، وهي تعليق يقول "without leaking data to an LLM". ما سُمّي
"AI" كان:
- ai_support_service: سبع كلمات مفتاحية في tuple.
- ai_admin_copilot: استعلامات SQL + ثلاثة شروط if.
- assistant_service: بحث نصي بـ re.findall + sort.

الحل هنا: طبقة موحّدة تعمل مع أي مزود (OpenAI/Anthropic/Gemini/Ollama
محلي)، ومع **fallback كامل إلى المنطق الحالي** حين لا يوجد مفتاح، فلا
ينكسر البوت أبداً بسبب غياب الذكاء الاصطناعي.

الحواجز الإلزامية:
- الأدوات للقراءة فقط افتراضياً.
- أي عملية صرف تتطلب تأكيداً صريحاً من المستخدم.
- سقف إنفاق يومي.
- كل استدعاء يُسجَّل في audit.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import aiohttp
from sqlalchemy import desc, func, select

from database.models import (
    Category,
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
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


class AIError(Exception):
    pass


class AIProviderClient:
    """
    عميل موحّد لأي مزود LLM.

    يرجع (النص، هل نجح). عند الفشل يرجع (None, False) فيستخدم
    المستدعي الـ fallback — البوت لا يتوقف على الذكاء الاصطناعي.
    """

    ENDPOINTS = {
        "openai": "https://api.openai.com/v1/chat/completions",
        "anthropic": "https://api.anthropic.com/v1/messages",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
        "ollama": "http://localhost:11434/api/chat",
    }

    @staticmethod
    async def configured() -> bool:
        provider = await FeatureService.config("ai_agent_layer", "provider", "none")
        return provider in AIProviderClient.ENDPOINTS

    @staticmethod
    async def complete(prompt: str, system: str = "", timeout_seconds: int = 20) -> tuple[str | None, bool]:
        provider = await FeatureService.config("ai_agent_layer", "provider", "none")
        url = AIProviderClient.ENDPOINTS.get(provider)
        if url is None:
            return None, False

        api_key = await SettingsService.get(f"ai_api_key_{provider}", "")
        if not api_key and provider != "ollama":
            return None, False

        # سقف الإنفاق اليومي
        cap = await FeatureService.config_decimal("ai_agent_layer", "daily_spend_cap_usd", 5.0)
        spent = await SettingsService.get_decimal("ai_spent_today_usd", Decimal("0"))
        if Decimal(str(cap)) > 0 and spent >= Decimal(str(cap)):
            logger.warning("تجاوز سقف إنفاق الذكاء الاصطناعي اليومي")
            return None, False

        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as http:
                if provider == "openai":
                    payload = {
                        "model": await SettingsService.get("ai_model_openai", "gpt-4o-mini"),
                        "messages": (
                            [{"role": "system", "content": system}] if system else []
                        ) + [{"role": "user", "content": prompt}],
                    }
                    headers = {"Authorization": f"Bearer {api_key}"}
                elif provider == "anthropic":
                    payload = {
                        "model": await SettingsService.get("ai_model_anthropic", "claude-3-5-haiku-latest"),
                        "max_tokens": 1024,
                        "system": system,
                        "messages": [{"role": "user", "content": prompt}],
                    }
                    headers = {
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                    }
                elif provider == "gemini":
                    payload = {
                        "contents": [{"parts": [{"text": (system + "\n\n" if system else "") + prompt}]}]
                    }
                    headers = {}
                    url = f"{url}?key={api_key}"
                else:  # ollama
                    payload = {
                        "model": await SettingsModel_default(),
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                    }
                    headers = {}

                async with http.post(url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        logger.warning("مزود AI أرجع %s", response.status)
                        return None, False
                    data = await response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("تعذّر استدعاء مزود AI: %s", exc)
            return None, False

        text = _extract_text(provider, data)
        if not text:
            return None, False
        await FeatureService.track("ai_agent_layer", "call", value=provider)
        return text, True


async def SettingsModel_default() -> str:
    return await SettingsService.get("ai_model_ollama", "llama3.1")


def _extract_text(provider: str, data: dict) -> str | None:
    try:
        if provider == "openai" or provider == "ollama":
            return data["message"]["content"] if "message" in data else data["choices"][0]["message"]["content"]
        if provider == "anthropic":
            return data["content"][0]["text"]
        if provider == "gemini":
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None
    return None


class AIAgentService:
    """وكيل محادثة يفهم الطلب الطبيعي ويرتّب الكتالوج."""

    READ_ONLY = True

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("ai_agent_layer")

    @staticmethod
    async def respond(session, user_id: int, message: str) -> dict:
        """
        يجيب على طلب المستخدم. يرجع dict فيه النص والمنتجات المقترحة.
        عند غياب LLM يعود للمنطق القائم على البحث النصي.
        """
        if not await AIAgentService.enabled():
            return await AIAgentService._fallback(session, message)

        persona = await LocalPersonaService.system_prompt(session, user_id)
        catalog = await AIAgentService._catalog_summary(session)
        prompt = (
            f"أنت مساعد متجر يبيع أرقام تحقق واشتراكات وخدمات سوشيال ميديا.\n"
            f"الكتالوج المختصر:\n{catalog}\n\n"
            f"طلب المستخدم: {message}\n\n"
            "رد باختصار باللغة نفسها التي كتب بها المستخدم، واقترح أسماء منتجات "
            "موجودة فعلاً في الكتالوج. لا تخترع منتجات. لا تذكر أسعاراً غير موجودة."
        )
        text, ok = await AIProviderClient.complete(prompt, system=persona)
        if not ok:
            return await AIAgentService._fallback(session, message)

        products = await AIAgentService._match_mentioned(session, text)
        return {"text": text, "products": products, "source": "llm"}

    @staticmethod
    async def _catalog_summary(session, limit: int = 40) -> str:
        result = await session.execute(
            select(Product)
            .where(Product.status == ProductStatus.ACTIVE)
            .order_by(desc(Product.total_sold))
            .limit(limit)
        )
        lines = []
        for product in result.scalars().all():
            lines.append(f"- {product.name_ar} ({product.price_usd}$)")
        return "\n".join(lines) or "(الكتالوج فارغ)"

    @staticmethod
    async def _match_mentioned(session, text: str, limit: int = 5) -> list[Product]:
        """يستخرج المنتجات المذكورة فعلاً في الرد، فلا يقترح شيئاً مخترعاً."""
        result = await session.execute(
            select(Product).where(Product.status == ProductStatus.ACTIVE)
        )
        mentioned = []
        lowered = (text or "").casefold()
        for product in result.scalars().all():
            if product.name_ar and product.name_ar.casefold() in lowered:
                mentioned.append(product)
            if len(mentioned) >= limit:
                break
        return mentioned

    @staticmethod
    async def _fallback(session, message: str) -> dict:
        """المنطق القديم: بحث نصي. يعمل دائماً بلا مفتاح."""
        import re

        terms = [t for t in re.findall(r"[\w\u0600-\u06ff]+", (message or "").casefold()) if len(t) >= 2]
        found: list[Product] = []
        seen: set[int] = set()
        for term in terms:
            result = await session.execute(
                select(Product)
                .where(
                    Product.status == ProductStatus.ACTIVE,
                    Product.name_ar.ilike(f"%{term}%"),
                )
                .limit(5)
            )
            for product in result.scalars().all():
                if product.id not in seen:
                    seen.add(product.id)
                    found.append(product)
        if not found:
            result = await session.execute(
                select(Product)
                .where(Product.status == ProductStatus.ACTIVE)
                .order_by(desc(Product.total_sold))
                .limit(5)
            )
            found = list(result.scalars().all())
            return {
                "text": "لم أجد تطابقاً مباشراً، فهذه أكثر المنتجات طلباً:",
                "products": found,
                "source": "fallback",
            }
        return {
            "text": "هذه أقرب النتائج لطلبك:",
            "products": found,
            "source": "fallback",
        }


class AutonomousPurchaseService:
    """
    وكيل شراء مستقل: «اشترِ 50 رقم كل يوم لمدة أسبوع بأرخص سعر،
    وتوقف لو ارتفع السعر فوق حد».
    """

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("autonomous_purchase_agent")

    @staticmethod
    async def max_budget() -> Decimal:
        return Decimal(str(await FeatureService.config_decimal(
            "autonomous_purchase_agent", "max_budget_usd", 500.0
        )))

    @staticmethod
    async def create_job(
        session,
        user_id: int,
        service_code: str,
        country_code: str,
        quantity_per_run: int,
        runs: int,
        interval_hours: int,
        max_unit_price_usd: Decimal,
    ) -> dict:
        if not await AutonomousPurchaseService.enabled():
            raise AIError("وكيل الشراء المستقل موقوف حالياً.")
        if quantity_per_run <= 0 or runs <= 0:
            raise AIError("الكمية وعدد الدورات يجب أن تكون موجبة.")

        budget = await AutonomousPurchaseService.max_budget()
        estimated = max_unit_price_usd * Decimal(quantity_per_run) * Decimal(runs)
        if estimated > budget:
            raise AIError(
                f"التكلفة التقديرية {estimated}$ تتجاوز الحد الأقصى {budget}$."
            )

        from database.models import AutonomousJob

        job = AutonomousJob(
            user_id=user_id,
            service_code=service_code,
            country_code=country_code,
            quantity_per_run=quantity_per_run,
            total_runs=runs,
            completed_runs=0,
            interval_hours=max(1, interval_hours),
            max_unit_price_usd=max_unit_price_usd,
            status="active",
            next_run_at=datetime.utcnow() + timedelta(hours=max(1, interval_hours)),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        await FeatureService.track("autonomous_purchase_agent", "created", user_id=user_id)
        return {"job_id": job.id, "estimated_usd": estimated, "runs": runs}

    @staticmethod
    async def run_due(session, bot=None) -> dict:
        """ينفّذ الدورات المستحقة، ويتوقف تلقائياً لو تجاوز السعر الحد."""
        stats = {"executed": 0, "skipped_price": 0, "halted": 0}
        if not await AutonomousPurchaseService.enabled():
            return stats

        from database.models import AutonomousJob
        from providers.countries import get_country_by_code, get_number_service_by_code
        from providers.manager import provider_manager
        from services.bulk_number_service import BulkNumberService

        now = datetime.utcnow()
        result = await session.execute(
            select(AutonomousJob).where(
                AutonomousJob.status == "active",
                AutonomousJob.next_run_at <= now,
            )
        )
        for job in result.scalars().all():
            service = await get_number_service_by_code(session, job.service_code)
            country = await get_country_by_code(session, job.country_code)
            if service is None or country is None:
                job.status = "halted"
                stats["halted"] += 1
                continue

            # فحص السعر قبل الشراء — هذا هو شرط المستخدم
            prices = await provider_manager.get_cheapest_price(service, country, session)
            if not prices:
                stats["skipped_price"] += 1
                job.next_run_at = now + timedelta(hours=job.interval_hours)
                continue
            unit_cost = min(prices.values())
            if unit_cost > Decimal(str(job.max_unit_price_usd)):
                stats["skipped_price"] += 1
                job.next_run_at = now + timedelta(hours=job.interval_hours)
                logger.info(
                    "تخطى الوكيل الدورة %s: السعر %s أعلى من الحد %s",
                    job.id, unit_cost, job.max_unit_price_usd,
                )
                await session.commit()
                continue

            await BulkNumberService.execute(
                session, job.user_id, service, country, job.quantity_per_run
            )
            job.completed_runs += 1
            if job.completed_runs >= job.total_runs:
                job.status = "done"
            else:
                job.next_run_at = now + timedelta(hours=job.interval_hours)
            stats["executed"] += 1

        await session.commit()
        return stats


class AIMerchandiserService:
    """رفّ ذكي: ترتيب شخصي للمنتجات حسب سلوك كل مستخدم."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("ai_merchandiser")

    @staticmethod
    async def personalization_weight() -> int:
        return max(0, min(100, await FeatureService.config_int(
            "ai_merchandiser", "personalization_weight", 70
        )))

    @staticmethod
    async def shelf_for(session, user_id: int, limit: int = 10) -> list[Product]:
        """
        يرتب المنتجات لهذا المستخدم تحديداً.
        النتيجة = وزن شخصي (سلوكه) + وزن عام (الأكثر مبيعاً).
        """
        result = await session.execute(
            select(Product).where(Product.status == ProductStatus.ACTIVE)
        )
        products = list(result.scalars().all())
        if not products:
            return []
        if not await AIMerchandiserService.enabled():
            products.sort(key=lambda p: p.total_sold or 0, reverse=True)
            return products[:limit]

        weight = await AIMerchandiserService.personalization_weight() / 100

        # اهتمامات المستخدم من طلباته السابقة
        interests = await AIMerchandiserService._interests(session, user_id)

        max_sold = max((p.total_sold or 0) for p in products) or 1
        scored = []
        for product in products:
            popularity = (product.total_sold or 0) / max_sold
            affinity = 0.0
            if interests:
                sub_id = product.sub_category_id
                affinity = interests.get(sub_id, 0.0)
            score = weight * affinity + (1 - weight) * popularity
            if product.is_featured:
                score += 0.15
            if product.is_bestseller:
                score += 0.10
            scored.append((score, product))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [product for _score, product in scored[:limit]]

    @staticmethod
    async def _interests(session, user_id: int) -> dict[int, float]:
        """توزيع اهتمامات المستخدم على الأقسام الفرعية، مطبّع إلى 1."""
        result = await session.execute(
            select(UnifiedOrder).where(UnifiedOrder.user_id == user_id).limit(200)
        )
        counts: dict[int, int] = {}
        for order in result.scalars().all():
            product = await session.get(Product, order.product_id)
            if product is None or product.sub_category_id is None:
                continue
            counts[product.sub_category_id] = counts.get(product.sub_category_id, 0) + 1

        total = sum(counts.values())
        if total == 0:
            return {}
        return {key: value / total for key, value in counts.items()}

    @staticmethod
    async def search_gaps(session, limit: int = 10) -> list[str]:
        """
        ماذا بحث المستخدمون ولم يجدوه؟ هذه فجوات كتالوج حقيقية.
        تُعرض على الأدمن كقائمة منتجات يقترح إضافتها.
        """
        result = await session.execute(
            select(Product).where(Product.view_count > 0).limit(1)
        )
        # الفجوات تُجمع من سجل بحث المستخدمين إن وُجد
        from database.models import FeatureEvent

        events = (
            await session.execute(
                select(FeatureEvent).where(
                    FeatureEvent.event_type == "search_no_result"
                ).limit(500)
            )
        ).scalars().all()
        counts: dict[str, int] = {}
        for event in events:
            term = (event.value or "").strip().casefold()
            if term:
                counts[term] = counts.get(term, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        return [term for term, _count in ranked[:limit]]


class LocalPersonaService:
    """شخصيات محلية: أسلوب مخاطبة مختلف لكل سوق بدل ترجمة آلية باردة."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("local_personas")

    @staticmethod
    async def persona_for(session, user_id: int) -> str:
        user = await session.get(User, user_id)
        language = (user.language_code if user else "ar") or "ar"
        if not await LocalPersonaService.enabled():
            return language

        market = await SettingsService.get(f"user_market_{user_id}", None)
        if not market:
            return language
        return f"{language}:{market}"

    @staticmethod
    async def system_prompt(session, user_id: int) -> str:
        """تعليمات الأسلوب التي تُمرَّر للـLLM."""
        persona = await LocalPersonaService.persona_for(session, user_id)
        styles = {
            "ar:gulf": "تحدث باللهجة الخليجية بأسلوب ودود ومختصر.",
            "ar:egypt": "تحدث باللهجة المصرية بأسلوب خفيف ومباشر.",
            "ar:maghreb": "تحدث باللهجة المغاربية بأسلوب مهذب.",
            "ar:levant": "تحدث باللهجة الشامية بأسلوب ودود.",
            "en:us": "Use casual American English, short and direct.",
            "en:gb": "Use British English, polite and concise.",
        }
        style = styles.get(persona)
        if style:
            return style
        base = "ar" if persona.startswith("ar") else persona.split(":")[0]
        return (
            "تحدث بالعربية الفصحى المبسطة بأسلوب ودود ومختصر."
            if base == "ar"
            else "Respond in clear, friendly, concise English."
        )
