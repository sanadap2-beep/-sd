"""
خدمة أقسام الذكاء الاصطناعي — القلب الذي تدور حوله كل الأقسام.

الفكرة: الأدمن ينشئ أقساماً من اللوحة (برمجة بدون قيود، دردشة بدون قيود،
وأي قسم ثالث مستقبلاً)، كل قسم له:
  - موديل محدد من NanoGPT + system prompt + شرح يدوي يظهر للمستخدم.
  - تسعير: تكلفة المزود الفعلية (ترجعها NanoGPT مع كل رد) × مضاعف ربح،
    أو سعر ثابت للرسالة. الافتراضي: تكلفة المزود × 3.

دورة الرسالة (run_turn):
  1) نحسب الخصم المتوقع ونتأكد من كفاية الرصيد.
  2) نخصم المتوقع مقدماً (حجز) حتى لا يُستخدم القسم بدون رصيد.
  3) نستدعي الموديل مع سياق الجلسة (آخر N رسالة).
  4) نحسب الخصم الفعلي: إذا زاد نخصم الفرق، إذا نقص نرجع الفرق —
     فالدفاتر تبقى دقيقة دائماً حتى لو رجع المزود تكلفة أعلى/أقل.
  5) نحفظ رسالتي الدور في الجلسة (جلسات ورسائل المستخدمين محفوظة).
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    AISection,
    AISectionMode,
    AISession,
    AIMessage,
    AIMessageRole,
    AIPricingMode,
    User,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.nanogpt_service import NanoGPTError, NanoGPTService
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

QUANT = Decimal("0.0001")
MIN_CHARGE = Decimal("0.0001")


class AISectionError(Exception):
    """خطأ صالح للعرض على المستخدم."""


def _money(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal("0")


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(QUANT, rounding=ROUND_HALF_UP)


# ══════════════ الأقسام (CRUD من لوحة الأدمن) ══════════════


class AISectionService:
    @staticmethod
    async def list_all(db: AsyncSession) -> list[AISection]:
        result = await db.execute(
            select(AISection).order_by(AISection.sort_order, AISection.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_enabled(db: AsyncSession) -> list[AISection]:
        sections = await AISectionService.list_all(db)
        return [s for s in sections if s.is_enabled]

    @staticmethod
    async def get(db: AsyncSession, section_id: int) -> AISection | None:
        return await db.get(AISection, section_id)

    @staticmethod
    async def available_for_users(db: AsyncSession) -> list[AISection]:
        """الأقسام التي تُعرض فعلاً: مفعّلة + مفتاح المزود مضبوط."""
        if not await NanoGPTService.configured():
            return []
        return await AISectionService.list_enabled(db)

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        title: str,
        model: str,
        mode: AISectionMode = AISectionMode.CHAT,
        emoji: str = "🤖",
        description: str | None = None,
        system_prompt: str | None = None,
        pricing_mode: AIPricingMode = AIPricingMode.USAGE,
        est_cost_per_message: Decimal = Decimal("0.003"),
        fixed_price: Decimal = Decimal("0.01"),
        profit_multiplier: Decimal = Decimal("3"),
    ) -> AISection:
        section = AISection(
            title=title.strip()[:64],
            model=model.strip()[:128],
            mode=mode,
            emoji=(emoji or "🤖").strip()[:8] or "🤖",
            description=description,
            system_prompt=system_prompt,
            pricing_mode=pricing_mode,
            est_cost_per_message=_money(est_cost_per_message),
            fixed_price=_money(fixed_price),
            profit_multiplier=_money(profit_multiplier),
        )
        db.add(section)
        await db.commit()
        await db.refresh(section)
        return section

    @staticmethod
    async def toggle(db: AsyncSession, section_id: int) -> AISection | None:
        section = await db.get(AISection, section_id)
        if section is None:
            return None
        section.is_enabled = not section.is_enabled
        await db.commit()
        return section

    @staticmethod
    async def delete(db: AsyncSession, section_id: int) -> bool:
        section = await db.get(AISection, section_id)
        if section is None:
            return False
        await db.delete(section)
        await db.commit()
        return True

    @staticmethod
    def price_label(section: AISection) -> str:
        """سطر التسعير الذي يظهر للمستخدم في شاشة القسم."""
        if section.pricing_mode == AIPricingMode.FIXED:
            return f"سعر ثابت ${_quantize(section.fixed_price)} لكل رسالة"
        est = _quantize(section.est_cost_per_message * section.profit_multiplier)
        return (
            f"حسب استهلاك المزود × {section.profit_multiplier:g} "
            f"(تقديري ≈ ${est}/رسالة، ويُحسب الفعلي بعد كل رد)"
        )

    @staticmethod
    def compute_charge(section: AISection, provider_cost: Decimal | None) -> Decimal:
        """
        الخصم المستحق للرسالة الواحدة:
          USAGE: (التكلفة الفعلية للمزود أو التقديرية) × مضاعف الربح
          FIXED: سعر ثابت
        """
        if section.pricing_mode == AIPricingMode.FIXED:
            charge = _money(section.fixed_price)
        else:
            base = provider_cost if provider_cost is not None else _money(
                section.est_cost_per_message
            )
            charge = base * _money(section.profit_multiplier)
        return _quantize(charge) if charge > MIN_CHARGE else MIN_CHARGE


# ══════════════ الجلسات والرسائل ══════════════


class AISessionService:
    @staticmethod
    async def get_or_create(
        db: AsyncSession, user_id: int, section: AISection, session_id: int | None = None
    ) -> AISession:
        if session_id:
            ai_session = await db.get(AISession, session_id)
            if (
                ai_session is not None
                and ai_session.user_id == user_id
                and ai_session.section_id == section.id
            ):
                return ai_session
        ai_session = AISession(user_id=user_id, section_id=section.id)
        db.add(ai_session)
        await db.flush()
        return ai_session

    @staticmethod
    async def new_session(db: AsyncSession, user_id: int, section: AISection) -> AISession:
        ai_session = AISession(user_id=user_id, section_id=section.id)
        db.add(ai_session)
        await db.commit()
        await db.refresh(ai_session)
        return ai_session

    @staticmethod
    async def list_for_user(
        db: AsyncSession, user_id: int, section_id: int, limit: int = 10
    ) -> list[AISession]:
        result = await db.execute(
            select(AISession)
            .where(
                AISession.user_id == user_id,
                AISession.section_id == section_id,
            )
            .order_by(desc(AISession.updated_at), desc(AISession.id))
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_user_session(
        db: AsyncSession, session_id: int, user_id: int
    ) -> AISession | None:
        ai_session = await db.get(AISession, session_id)
        if ai_session is None or ai_session.user_id != user_id:
            return None
        return ai_session

    @staticmethod
    async def get_messages(db: AsyncSession, ai_session: AISession) -> list[AIMessage]:
        result = await db.execute(
            select(AIMessage)
            .where(AIMessage.session_id == ai_session.id)
            .order_by(AIMessage.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def context_messages(
        db: AsyncSession, ai_session: AISession, limit: int
    ) -> list[dict]:
        """آخر N رسالة كسياق للموديل (الأقدم أولاً)."""
        result = await db.execute(
            select(AIMessage)
            .where(AIMessage.session_id == ai_session.id)
            .order_by(desc(AIMessage.id))
            .limit(limit)
        )
        rows = list(result.scalars().all())
        return [
            {"role": m.role.value, "content": m.content}
            for m in reversed(rows)
            if m.role in (AIMessageRole.USER, AIMessageRole.ASSISTANT)
        ]


# ══════════════ دورة الرسالة: خصم → موديل → تسوية → حفظ ══════════════


async def run_turn(
    db: AsyncSession,
    user: User,
    section: AISection,
    user_text: str,
    ai_session: AISession | None = None,
) -> dict:
    """
    يعالج رسالة واحدة من المستخدم في أي قسم ذكاء اصطناعي.

    يعيد dict: text, paid, provider_cost, model, ai_session.
    ترفع AISectionError عند مشكلة مزود (بدون خصم فعلي بعد التسوية).
    """
    from database.models import TransactionType

    if not section.is_enabled:
        raise AISectionError("هذا القسم موقوف حالياً.")

    ai_session = ai_session or await AISessionService.get_or_create(
        db, user.id, section
    )

    # ── 1) الخصم المتوقع والتأكد من كفاية الرصيد ──
    est_charge = AISectionService.compute_charge(section, provider_cost=None)
    balance = await BalanceService.get_balance(db, user.id)
    if balance < est_charge:
        raise InsufficientBalanceError(
            f"رصيدك غير كافٍ. مطلوب ≈ ${est_charge} لكل رسالة، ورصيدك ${balance}."
        )

    # ── 2) حجز مسبق (يُسوّى بعد رد المزود) ──
    await BalanceService.deduct_balance(
        db,
        user.id,
        est_charge,
        TransactionType.AI_USAGE,
        description=f"قسم {section.title}: رسالة ذكاء اصطناعي (حجز مسبق)",
        related_table="ai_sessions",
        related_id=ai_session.id,
    )

    # ── 3) بناء السياق واستدعاء الموديل ──
    messages: list[dict] = []
    if section.system_prompt and section.system_prompt.strip():
        messages.append({"role": "system", "content": section.system_prompt.strip()})
    messages.extend(
        await AISessionService.context_messages(
            db, ai_session, section.max_context_messages
        )
    )
    messages.append({"role": "user", "content": user_text})

    try:
        result = await NanoGPTService.complete(
            model=section.model,
            messages=messages,
            max_tokens=section.max_output_tokens,
            temperature=float(section.temperature),
        )
    except NanoGPTError:
        # فشل المزود → نرجع الحجز كاملاً حتى لا يخسر المستخدم شيئاً.
        await BalanceService.add_balance(
            db,
            user.id,
            est_charge,
            TransactionType.REFUND,
            description=f"إرجاع حجز قسم {section.title} — فشل المزود",
        )
        raise

    # ── 4) التسوية: خصم الفرق الزائد أو إرجاع الفارق ──
    actual = AISectionService.compute_charge(section, provider_cost=result.provider_cost)
    paid = est_charge
    if actual > est_charge:
        diff = actual - est_charge
        current = await BalanceService.get_balance(db, user.id)
        diff = min(diff, current)  # لا نُغرق الرصيد بالسالب أبداً
        if diff > 0:
            await BalanceService.deduct_balance(
                db,
                user.id,
                diff,
                TransactionType.AI_USAGE_ADJUST,
                description=f"قسم {section.title}: فرق التكلفة الفعلية",
            )
            paid += diff
    elif actual < est_charge:
        diff = est_charge - actual
        await BalanceService.add_balance(
            db,
            user.id,
            diff,
            TransactionType.REFUND,
            description=f"قسم {section.title}: إرجاع فرق التكلفة الفعلية",
        )
        paid -= diff

    # ── 5) حفظ الرسالتين في الجلسة ──
    user_row = AIMessage(
        session_id=ai_session.id,
        role=AIMessageRole.USER,
        content=user_text,
        model=section.model,
    )
    assistant_row = AIMessage(
        session_id=ai_session.id,
        role=AIMessageRole.ASSISTANT,
        content=result.text,
        model=result.model or section.model,
        provider_cost=result.provider_cost or Decimal("0"),
        charged_amount=paid,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    db.add_all([user_row, assistant_row])

    ai_session.messages_count += 2
    ai_session.provider_cost += result.provider_cost or Decimal("0")
    ai_session.charged_total += paid
    if ai_session.title == "جلسة جديدة":
        ai_session.title = user_text.strip()[:76] or "جلسة جديدة"
    await db.commit()

    return {
        "text": result.text,
        "paid": paid,
        "est_charge": est_charge,
        "provider_cost": result.provider_cost,
        "model": result.model or section.model,
        "ai_session": ai_session,
    }


# ══════════════ أدوات مساعدة للعرض ══════════════

_FENCE_RE = re.compile(r"```([A-Za-z0-9_+#.-]*)")

_EXT_MAP = {
    "python": "py", "py": "py", "javascript": "js", "js": "js", "node": "js",
    "typescript": "ts", "ts": "ts", "html": "html", "css": "css", "json": "json",
    "bash": "sh", "shell": "sh", "sh": "sh", "sql": "sql", "java": "java",
    "kotlin": "kt", "c": "c", "cpp": "cpp", "c++": "cpp", "csharp": "cs",
    "cs": "cs", "go": "go", "rust": "rs", "php": "php", "ruby": "rb",
    "swift": "swift", "dart": "dart", "yaml": "yml", "yml": "yml",
    "xml": "xml", "markdown": "md", "md": "md", "text": "txt",
}


def guess_extension(text: str) -> str:
    """يخمّن امتداد الملف من أول code fence في رد الموديل."""
    match = _FENCE_RE.search(text or "")
    if match:
        lang = (match.group(1) or "").lower()
        return _EXT_MAP.get(lang, "txt")
    return "txt"


async def global_ai_stats(db: AsyncSession) -> dict:
    """إحصائيات عامة للأدمن: رسائل، جلسات، تكاليف، أرباح."""
    from sqlalchemy import func

    sessions_count = (
        await db.execute(select(func.count(AISession.id)))
    ).scalar_one()
    messages_count = (
        await db.execute(select(func.count(AIMessage.id)))
    ).scalar_one()
    provider_total = (
        await db.execute(select(func.coalesce(func.sum(AIMessage.provider_cost), 0)))
    ).scalar_one()
    charged_total = (
        await db.execute(select(func.coalesce(func.sum(AIMessage.charged_amount), 0)))
    ).scalar_one()
    return {
        "sessions": int(sessions_count or 0),
        "messages": int(messages_count or 0),
        "provider_cost": _money(provider_total),
        "charged": _money(charged_total),
        "profit": _money(charged_total) - _money(provider_total),
    }
