"""
خدمة القسم الرئيسي للذكاء الاصطناعي.

المسؤوليات:
- إدارة الأقسام (إنشاء/تعديل/تفعيل) — كل قسم: موديل + وصف + تكلفة رسالة.
- حساب سعر الرسالة: تكلفة المزود + ربح (مضاعف، الافتراضي 3×).
- الجلسات: جلسة واحدة نشطة لكل مستخدم في كل قسم، مع حفظ الرسائل
  للرجوع إليها لاحقاً.
- المعالجة: خصم الرصيد → استدعاء NanoGPT → حفظ الرسالة.
  عند فشل المزود يُرجع المبلغ (idempotent) ولا يضيع شيء.
- استخراج الملفات من ردود قسم البرمجة (إرسال كود/أداة كملف بدل رسالة
  طويلة تتلخبط في تليجرام).
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import datetime
from decimal import Decimal

from sqlalchemy import desc, func, select

from database.models import (
    AiMessage,
    AiSection,
    AiSession,
    TransactionType,
    User,
)
from services.ai_provider_client import (
    AiCompletion,
    AiProviderError,
    chat_completion,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class AiSectionError(Exception):
    """خطأ عام في قسم الذكاء الاصطناعي (قسم موقوف، غير موجود...)."""


# ══════════════ الإدارة ══════════════


def _now() -> datetime:
    return datetime.utcnow()


class AiSectionService:
    """إدارة الأقسام وسعرها."""

    @staticmethod
    async def list_enabled(session) -> list[AiSection]:
        result = await session.execute(
            select(AiSection)
            .where(AiSection.enabled.is_(True))
            .order_by(AiSection.sort_order, AiSection.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_all(session) -> list[AiSection]:
        result = await session.execute(
            select(AiSection).order_by(AiSection.sort_order, AiSection.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get(session, section_id: int) -> AiSection | None:
        return await session.get(AiSection, section_id)

    @staticmethod
    async def get_by_key(session, key: str) -> AiSection | None:
        result = await session.execute(
            select(AiSection).where(AiSection.key == key)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        session,
        *,
        key: str,
        name_ar: str,
        model: str,
        cost_per_message_usd: Decimal,
        profit_multiplier: float = 3.0,
        kind: str = "chat",
        name_en: str | None = None,
        description_ar: str | None = None,
        description_en: str | None = None,
        enabled: bool = False,
    ) -> AiSection:
        if await AiSectionService.get_by_key(session, key):
            raise AiSectionError(f"يوجد قسم بنفس المعرّف: {key}")
        section = AiSection(
            key=key,
            name_ar=name_ar.strip(),
            name_en=(name_en or "").strip() or None,
            description_ar=(description_ar or "").strip() or None,
            description_en=(description_en or "").strip() or None,
            kind=kind if kind in ("coding", "chat") else "chat",
            model=model.strip(),
            cost_per_message_usd=cost_per_message_usd.quantize(Decimal("0.0001")),
            profit_multiplier=profit_multiplier,
            enabled=enabled,
            sort_order=int(
                await session.scalar(
                    select(func.coalesce(func.max(AiSection.sort_order), 0))
                )
                or 0
            )
            + 1,
        )
        session.add(section)
        await session.commit()
        await session.refresh(section)
        await FeatureService.track("ai_sections", "section_created", value=key)
        return section

    @staticmethod
    async def update(session, section: AiSection, **fields) -> AiSection:
        allowed = {
            "name_ar",
            "name_en",
            "description_ar",
            "description_en",
            "kind",
            "model",
            "cost_per_message_usd",
            "profit_multiplier",
            "enabled",
            "sort_order",
        }
        for name, value in fields.items():
            if name not in allowed:
                continue
            if name in ("cost_per_message_usd",):
                value = Decimal(str(value)).quantize(Decimal("0.0001"))
            if name in ("description_ar", "description_en", "name_en"):
                value = (value or "").strip() or None
            setattr(section, name, value)
        section.updated_at = _now()
        await session.commit()
        await session.refresh(section)
        return section

    @staticmethod
    async def toggle(session, section: AiSection) -> AiSection:
        section.enabled = not section.enabled
        section.updated_at = _now()
        await session.commit()
        await session.refresh(section)
        return section

    # ── التسعير ──

    @staticmethod
    def sell_price(section: AiSection) -> Decimal:
        """سعر الرسالة للمستخدم = تكلفة المزود + الربح (مضاعف)."""
        return section.sell_price_usd

    @staticmethod
    def profit_per_message(section: AiSection) -> Decimal:
        return (section.sell_price_usd - section.cost_per_message_usd).quantize(
            Decimal("0.0001")
        )


# ══════════════ الجلسات والرسائل ══════════════


class AiConversationService:
    """جلسات المستخدمين ورسائلهم."""

    @staticmethod
    async def ensure_session(session, user: User, section: AiSection) -> AiSession:
        """الجلسة النشطة (الأحدث) أو إنشاء جديدة."""
        result = await session.execute(
            select(AiSession)
            .where(
                AiSession.user_id == user.id,
                AiSession.section_id == section.id,
            )
            .order_by(desc(AiSession.updated_at))
            .limit(1)
        )
        ai_session = result.scalar_one_or_none()
        if ai_session is None:
            ai_session = AiSession(user_id=user.id, section_id=section.id)
            session.add(ai_session)
            await session.flush()
        return ai_session

    @staticmethod
    async def get_session(session, user: User, section: AiSection) -> AiSession | None:
        result = await session.execute(
            select(AiSession)
            .where(
                AiSession.user_id == user.id,
                AiSession.section_id == section.id,
            )
            .order_by(desc(AiSession.updated_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def sessions_for(session, user_id: int, section_id: int, limit: int = 10):
        result = await session.execute(
            select(AiSession)
            .where(
                AiSession.user_id == user_id,
                AiSession.section_id == section_id,
            )
            .order_by(desc(AiSession.updated_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def latest_messages(session, ai_session: AiSession, limit: int = 20) -> list[AiMessage]:
        result = await session.execute(
            select(AiMessage)
            .where(AiMessage.session_id == ai_session.id)
            .order_by(AiMessage.id)
        )
        rows = list(result.scalars().all())
        return rows[-limit:]

    # ── معالجة رسالة المستخدم ──

    @staticmethod
    def system_prompt(section: AiSection, language: str) -> str:
        """تعليمات النموذج حسب نوع القسم + وصف الأدمن اليدوي."""
        name = section.name_ar if language != "en" else (section.name_en or section.name_ar)
        description = (
            section.description_en if (language == "en" and section.description_en)
            else section.description_ar
        ) or ""
        desc_block = f"وصف القسم: {description}\n\n" if description else ""

        if section.kind == "coding":
            if language == "en":
                return (
                    f"You are an expert senior programmer working in the "
                    f"'{name}' section of a Telegram bot. {desc_block}\n"
                    "Rules:\n"
                    "1. Write complete, runnable code — never leave TODOs or placeholders.\n"
                    "2. Put each file in its own fenced code block, and write the "
                    "filename as the very first line inside the block, exactly like: "
                    "file: main.py\n"
                    "3. If the project has several files, give each its own block "
                    "with its own file: line.\n"
                    "4. After the code, add a short note (3-6 lines): how to install "
                    "and run it.\n"
                    "5. Reply in the same language the user wrote in."
                )
            return (
                f"أنت مبرمج خبير يعمل في قسم «{name}» داخل بوت تليجرام. "
                f"{desc_block}\n"
                "القواعد:\n"
                "1. اكتب كوداً كاملاً جاهزاً للتشغيل — لا تترك TODO أو أماكن فارغة.\n"
                "2. ضع كل ملف في بلوك كود مستقل، واكتب اسم الملف أول سطر داخل "
                "البلوك بهذه الصيغة بالضبط: file: main.py\n"
                "3. إذا كان المشروع فيه أكثر من ملف، اعطِ كل ملف بلوك مستقل "
                "بسطر file: خاص به.\n"
                "4. بعد الكود، أضف شرحاً قصيراً (3-6 أسطر): كيف يُثبّت ويُشغّل.\n"
                    "5. رد بنفس اللغة التي كتب بها المستخدم."
            )
        if language == "en":
            return (
                f"You are a helpful, unrestricted AI assistant in the '{name}' "
                f"section of a Telegram bot. {desc_block}\n"
                "Rules:\n"
                "1. Answer the user's question directly and accurately.\n"
                "2. Reply in the same language the user wrote in.\n"
                "3. Keep the answer clear and well structured.\n"
                "4. If code is needed, provide it in a fenced code block with a "
                "file: line as the first line inside the block."
            )
        return (
            f"أنت مساعد ذكاء اصطناعي في قسم «{name}» داخل بوت تليجرام. "
            f"{desc_block}\n"
            "القواعد:\n"
            "1. أجب على سؤال المستخدم مباشرة وبإيجاز ودون مواربة.\n"
            "2. رد بنفس اللغة التي كتب بها المستخدم.\n"
            "3. إذا احتاج السؤال كوداً، ضعه في بلوك كود مع سطر file: أول سطر داخل البلوك."
        )

    @staticmethod
    async def process_message(
        session,
        user: User,
        section: AiSection,
        text: str,
    ) -> dict:
        """
        معالجة رسالة مستخدم كاملة:
        تحقق من القسم والرصيد → خصم → استدعاء المزود → حفظ.
        عند فشل المزود: استرجاع المبلغ ورجوع ok=False.

        يرجع: {ok, text, files: [(name, bytes)], price, cost, error?}
        """
        if not section.enabled:
            raise AiSectionError("هذا القسم غير مفعّل حالياً.")

        price = AiSectionService.sell_price(section)
        if user.balance < price:
            raise InsufficientBalanceError(
                f"رصيدك غير كافٍ: المطلوب {price}$ والرسالة تكلفتها عندك {price}$"
            )

        ai_session = await AiConversationService.ensure_session(session, user, section)
        user_message = AiMessage(session_id=ai_session.id, role="user", content=text)
        session.add(user_message)
        await session.flush()

        if ai_session.title is None:
            ai_session.title = text.strip().replace("\n", " ")[:48]
        ai_session.updated_at = _now()

        # 1) خصم السعر — مرتبط برسالة المستخدم (idempotent للاسترجاع).
        await BalanceService.deduct_balance(
            session,
            user.id,
            price,
            TransactionType.AI_USAGE,
            description=f"ذكاء اصطناعي — {section.name_ar}",
            related_table="ai_messages",
            related_id=user_message.id,
        )

        # 2) سياق المحادثة (الرسائل الأخيرة من الجلسة).
        context_limit = await FeatureService.config_int(
            "ai_sections", "context_messages", 20
        )
        history = await AiConversationService.latest_messages(
            session, ai_session, limit=context_limit
        )
        messages: list[dict] = [
            {"role": "system", "content": AiConversationService.system_prompt(section, user.language_code)}
        ]
        for row in history:
            # user_message لم يُحفظ بعد في الاستعلام، نضيفه يدوياً في النهاية.
            if row.id == user_message.id:
                continue
            messages.append({"role": row.role, "content": row.content})
        messages.append({"role": "user", "content": text})

        timeout = await FeatureService.config_int("ai_sections", "timeout_seconds", 120)
        try:
            completion: AiCompletion = await chat_completion(
                messages, section.model, timeout_seconds=timeout
            )
        except AiProviderError as exc:
            # استرجاع المبلغ — idempotent: نفس related_table/related_id/type.
            await BalanceService.add_balance(
                session,
                user.id,
                price,
                TransactionType.REFUND,
                description=f"استرجاع — فشل ذكاء اصطناعي ({section.name_ar})",
                related_table="ai_messages",
                related_id=user_message.id,
            )
            await session.commit()
            await FeatureService.track(
                "ai_sections", "provider_failed", user_id=user.id, value=section.key
            )
            logger.warning("فشل المزود في القسم %s: %s", section.key, exc)
            return {"ok": False, "error": str(exc), "price": price, "cost": section.cost_per_message_usd}

        # 3) حفظ رد المساعد.
        assistant_message = AiMessage(
            session_id=ai_session.id,
            role="assistant",
            content=completion.text,
            cost_usd=section.cost_per_message_usd,
        )
        session.add(assistant_message)
        ai_session.message_count += 2
        ai_session.updated_at = _now()
        await session.commit()

        files: list[tuple[str, bytes]] = []
        if section.kind == "coding":
            _explanation, artifacts = extract_code_artifacts(completion.text)
            files = [(name, content.encode("utf-8")) for name, content in artifacts]

        await FeatureService.track(
            "ai_sections", "message_ok", user_id=user.id, value=section.key
        )
        return {
            "ok": True,
            "text": completion.text,
            "files": files,
            "price": price,
            "cost": section.cost_per_message_usd,
            "session_id": ai_session.id,
        }

    # ── الإحصاءات ──

    @staticmethod
    async def stats(session) -> dict:
        """إجماليات: رسائل، تكلفة مزود، إيرادات، ربح — لكل قسم وعلماً."""
        rows = (
            (
                await session.execute(
                    select(
                        AiSection.id,
                        AiSection.name_ar,
                        AiSection.cost_per_message_usd,
                        func.count(AiMessage.id),
                        func.coalesce(func.sum(AiMessage.cost_usd), 0),
                    )
                    .join(AiSession, AiSession.section_id == AiSection.id)
                    .join(AiMessage, AiMessage.session_id == AiSession.id)
                    .where(AiMessage.role == "assistant")
                    .group_by(AiSection.id, AiSection.name_ar, AiSection.cost_per_message_usd)
                )
            ).all()
        )
        sections = []
        total_messages = 0
        total_cost = Decimal("0")
        total_revenue = Decimal("0")
        for section_id, name_ar, cost_per, count, cost_sum in rows:
            section = await session.get(AiSection, section_id)
            if section is None:
                continue
            count = int(count or 0)
            cost = Decimal(str(cost_sum or 0))
            revenue = AiSectionService.sell_price(section) * count
            sections.append(
                {
                    "id": section_id,
                    "name_ar": name_ar,
                    "messages": count,
                    "cost": cost,
                    "revenue": revenue,
                    "profit": revenue - cost,
                }
            )
            total_messages += count
            total_cost += cost
            total_revenue += revenue
        return {
            "sections": sections,
            "total_messages": total_messages,
            "total_cost": total_cost,
            "total_revenue": total_revenue,
            "total_profit": total_revenue - total_cost,
        }


# ══════════════ استخراج الملفات (قسم البرمجة) ══════════════

_FENCE_RE = re.compile(r"```([\w.+#-]*)[ \t]*([\w./\\-]*\.\w{1,10})?\n(.*?)```", re.S)
_FILE_LINE_RE = re.compile(
    r"^\s*(?:[#/]{1,2}\s*)?file:\s*([\w./\\-]+\.\w{1,10})\s*$", re.I
)

_EXT_BY_LANG: dict[str, str] = {
    "python": "py",
    "js": "js",
    "javascript": "js",
    "jsx": "jsx",
    "ts": "ts",
    "typescript": "ts",
    "tsx": "tsx",
    "html": "html",
    "css": "css",
    "json": "json",
    "bash": "sh",
    "sh": "sh",
    "shell": "sh",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "cs": "cs",
    "csharp": "cs",
    "java": "java",
    "php": "php",
    "ruby": "rb",
    "go": "go",
    "rust": "rs",
    "sql": "sql",
    "yaml": "yml",
    "yml": "yml",
    "xml": "xml",
    "ini": "ini",
    "toml": "toml",
    "md": "md",
}


def _guess_name(lang: str, index: int) -> str:
    ext = _EXT_BY_LANG.get((lang or "").lower(), "txt")
    if index == 0:
        return f"main.{ext}"
    return f"file_{index + 1}.{ext}"


def extract_code_artifacts(text: str) -> tuple[str, list[tuple[str, str]]]:
    """
    يفكك رد النموذج إلى (شرح نصي، ملفات).

    أولوية تسمية الملفات:
    1) سطر داخل البلوك: file: main.py
    2) اسم في ترويسة البلوك: ```python main.py
    3) تخمين من اللغة: main.py / file_2.js ...

    إذا لم يوجد أي بلوك كود → (النص كاملاً، []).
    """
    if not text:
        return "", []

    blocks = list(_FENCE_RE.finditer(text))
    if not blocks:
        return text.strip(), []

    named: list[tuple[str, str]] = []
    unnamed: list[tuple[str, str]] = []
    for match in blocks:
        lang = match.group(1) or ""
        header_name = match.group(2) or ""
        body = match.group(3)
        file_line = _FILE_LINE_RE.match(body.split("\n", 1)[0]) if body else None
        if file_line:
            name = file_line.group(1).replace("\\", "/")
            # نزيل سطر file: من محتوى الملف.
            content = body.split("\n", 1)[1] if "\n" in body else ""
            named.append((name, content))
        elif header_name and "." in header_name:
            named.append((header_name, body))
        else:
            unnamed.append((lang, body))

    if named:
        # إزالة كل البلوكات من النص لتبقى الشارة/الشرح فقط.
        explanation = _FENCE_RE.sub("", text)
        explanation = re.sub(r"\n{3,}", "\n\n", explanation).strip()
        # حذف أسماء المكررة.
        seen: dict[str, int] = {}
        unique: list[tuple[str, str]] = []
        for name, content in named:
            base = name
            if base in seen:
                seen[base] += 1
                stem, dot, ext = name.rpartition(".")
                name = f"{stem}_{seen[base]}.{ext}" if dot else f"{base}_{seen[base]}"
            else:
                seen[base] = 0
            unique.append((name, content.rstrip() + "\n"))
        return explanation, unique

    # بلا أسماء: كل البلوكات ملفات بتسميات تخمينية.
    artifacts = [( _guess_name(lang, i), body.rstrip() + "\n") for i, (lang, body) in enumerate(unnamed)]
    explanation = _FENCE_RE.sub("", text)
    explanation = re.sub(r"\n{3,}", "\n\n", explanation).strip()
    return explanation, artifacts


def files_to_zip(files: list[tuple[str, bytes]]) -> bytes:
    """يضغط عدة ملفات في archive واحد (حد تليجرام للرسائل)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            archive.writestr(name, data)
    return buffer.getvalue()
