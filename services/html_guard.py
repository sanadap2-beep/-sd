"""حماية رسائل البوت من أخطاء تنسيق HTML.

تيليجرام يرفض أي رسالة ``parse_mode=HTML`` فيها وسم مكسور، والرفض يظهر كـ::

    Bad Request: can't parse entities: Unexpected end tag at byte offset 82

وتكون النتيجة شاشة كاملة لا تُعرض (قائمة خدمات الرشق، صفحة منتج، ...) مع
تتبّع لا علاقة له بالسبب الحقيقي.

مصادر الوسم المكسور ثلاثة في هذا المشروع:
1) نصوص من قاعدة البيانات (أسماء الأقسام/المنتجات وشرحها، أسماء خدمات
   المزود المسحوبة، روابط يكتبها المستخدم) تحتوي ``<`` أو ``&`` أو وسوماً.
2) مقاطع ترجمة ناتجة عن تقسيم f-string آلياً، فبقي ``</b>`` داخل مفتاح
   مثل ``ux_games_282_17`` بينما الكود يغلق الوسم أيضاً → وسم زائد.
3) كود يفتح ``<b>`` ولا يغلقه لأن الإغلاق سكن في المفتاح الآخر.

ما يوفّره هذا الملف:
- ``esc()`` — تهريب القيم الديناميكية قبل وضعها داخل الوسوم.
- ``repair_html()`` / ``is_parseable()`` — إصلاح أي نص جاهز للإرسال
  (إزالة الوسوم الزائدة، إغلاق الناقص، تهريب ``<`` الحرفي).
- ``to_plain()`` — تحويل الرسالة إلى نص عادي كحل أخير.
- ``HtmlGuardedBot`` — نسخة ``Bot`` تصلح النص قبل الإرسال، وإن رفض
  تيليجرام مرة أخرى أعاده نصاً عادياً بدل أن يفشل المعالج بخطأ.
"""

from __future__ import annotations

import html
import logging
import re
from html import escape as _html_escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)

# الوسوم التي يسمح بها تيليجرام في parse_mode=HTML.
ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "b",
        "i",
        "u",
        "s",
        "strike",
        "a",
        "code",
        "pre",
        "blockquote",
        "span",
        "tg-emoji",
    }
)

# وسم HTML (فتح أو إغلاق) مع ما يلحقه من خصائص.
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
_ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,10};)")


def esc(value: object) -> str:
    """تهريب أي قيمة تُدخل داخل رسالة HTML (اسم منتج، شرح، رابط، معرّف...).

    ``None`` تصبح سلسلة فارغة حتى لا يظهر حرف ``None`` داخل الوسم.
    """
    if value is None:
        return ""
    return _html_escape(str(value), quote=False)


def _escape_text(segment: str) -> str:
    """تهريب مقطع نصي خارج الوسوم.

    ``<`` يُهرّب دائماً، و``&`` يُهرّب إن لم يكن بداية كيان صحيح
    (``&amp;`` / ``&#1234;``). أما ``>`` فتُترك كما هي لأن تيليجرام لا
    يشترط تهريبها داخل النص، فيبقى النص السليم بلا تغيير.
    """
    if "<" not in segment and "&" not in segment:
        return segment
    parts: list[str] = []
    pos = 0
    for match in _ENTITY_RE.finditer(segment):
        parts.append(_raw_escape(segment[pos:match.start()]))
        parts.append(match.group(0))
        pos = match.end()
    parts.append(_raw_escape(segment[pos:]))
    return "".join(parts)


def _raw_escape(chunk: str) -> str:
    return chunk.replace("&", "&amp;").replace("<", "&lt;")


def repair_html(text: str) -> str:
    """إرجاع نص HTML صالحاً للتحليل دون المساس بالتنسيق السليم.

    القواعد:
    - ``<`` لا يبدأ وسماً مسموحاً → يُهرّب (يظهر حرفياً للمستخدم).
    - وسم مسموح زائد بلا فاتح مقابل له → يُحذف.
    - وسم مفتوح ولم يُغلق → يُغلق في نهاية النص.
    - تداخل وسوم (``<b>a<i>b</b>c</i>``) → يُغلق الداخلي قبل الخارجي.

    العملية idempotent: تطبيقها على نص سليم يعيد نفس النص.
    """
    if not isinstance(text, str) or not text:
        return text
    if "<" not in text and "&" not in text:
        return text
    out: list[str] = []
    stack: list[str] = []
    pos = 0
    for match in _TAG_RE.finditer(text):
        out.append(_escape_text(text[pos:match.start()]))
        pos = match.end()
        closing, name = match.group(1) == "/", match.group(2).lower()
        if name not in ALLOWED_TAGS:
            # ليس وسم تيليجرام → نص حرفي (مثال: «<service>» داخل اسم خدمة).
            out.append(_escape_text(match.group(0)))
            continue
        if not closing:
            stack.append(name)
            out.append(match.group(0))
            continue
        if name not in stack:
            # إغلاق يتيم (أشهر أسباب «Unexpected end tag») → يُحذف.
            continue
        # أغلق ما فاقه من وسوم داخلية قبل الإغلاق المطلوب.
        while stack and stack[-1] != name:
            out.append(f"</{stack.pop()}>")
        if stack:
            stack.pop()
        out.append(match.group(0))
    out.append(_escape_text(text[pos:]))
    while stack:
        out.append(f"</{stack.pop()}>")
    return "".join(out)


def is_parseable(text: str) -> bool:
    """هل النص صالح كما هو لتحليل HTML في تيليجرام؟"""
    if not isinstance(text, str):
        return True
    return repair_html(text) == text


def guard_html(text: str) -> str:
    """إصلاح عند الحاجة فقط — بلا نسخ مجاني لكل رسالة."""
    if not isinstance(text, str) or not text:
        return text
    repaired = repair_html(text)
    if repaired != text:
        logger.warning(
            "تم إصلاح تنسيق HTML في رسالة قبل إرسالها (وسوم غير متوازنة). "
            "الأصل: %.200r",
            text,
        )
    return repaired


def to_plain(text: str) -> str:
    """نسخة نصية عادية: تُزال الوسوم ويُفكّ التهريب.

    تُستخدم كحل أخير إن أصرّ تيليجرام على رفض التنسيق، فوصول النص
    بلا تنسيق أفضل من رسالة خطأ للمستخدم.
    """
    if not isinstance(text, str):
        return text
    cleaned = _TAG_RE.sub("", text)
    return html.unescape(cleaned)


def _parse_mode_of(bot: Bot, method: object) -> str:
    """وضع التنسيق الفعلي للميثود (``Default('parse_mode')`` يعني: إعداد البوت)."""
    mode = getattr(method, "parse_mode", None)
    if not isinstance(mode, str) or not mode:
        mode = getattr(getattr(bot, "default", None), "parse_mode", None)
    return str(getattr(mode, "value", mode) or "")


def guard_method(bot: Bot, method: object) -> bool:
    """إصلاح حقول النص في ميثود أيوغرام قبل إرسالها. تُرجع هل غيّرت شيئاً.

    تُتجاوز الحقول التي يمرَّر لها ``entities`` صراحةً: صاحبها يحسب مواضع
    الكيانات بنفسه، وأي تغيير في النص يُفسدها.
    """
    if _parse_mode_of(bot, method) != "HTML":
        return False
    changed = False
    for field, entities_field in (("text", "entities"), ("caption", "caption_entities")):
        value = getattr(method, field, None)
        if not isinstance(value, str):
            continue
        if getattr(method, entities_field, None):
            continue
        fixed = guard_html(value)
        if fixed != value:
            setattr(method, field, fixed)
            changed = True
    return changed


def degrade_to_plain(method: object) -> bool:
    """إسقاط التنسيق عن الميثود (parse_mode=None + نص عادي)."""
    degraded = False
    for field, entities_field in (("text", "entities"), ("caption", "caption_entities")):
        value = getattr(method, field, None)
        if isinstance(value, str):
            setattr(method, field, to_plain(value))
            degraded = True
        if getattr(method, entities_field, None):
            try:
                setattr(method, entities_field, None)
            except Exception:  # pragma: no cover
                pass
    if degraded:
        try:
            method.parse_mode = None  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - ميثود بلا هذا الحقل
            pass
    return degraded


def is_entity_parse_error(exc: BaseException) -> bool:
    """هل الخطأ من نوع «can't parse entities» (مشكلة تنسيق لا مشكلة مزود)؟"""
    return isinstance(exc, TelegramBadRequest) and "can't parse entities" in str(exc).lower()


class HtmlGuardedBot(Bot):
    """نسخة ``Bot`` لا تُسقط رسالة بسبب تنسيق HTML:

    1. تصلح الوسوم المكسورة/الحروف غير المهرّبة قبل الإرسال.
    2. وإن رفض تيليجرام رغم ذلك، تعيد الإرسال نصاً عادياً بدل الخطأ.
    """

    async def __call__(self, method, request_timeout=None):  # type: ignore[override]
        guard_method(self, method)
        try:
            return await super().__call__(method, request_timeout)
        except TelegramBadRequest as exc:
            if not is_entity_parse_error(exc):
                raise
            if not degrade_to_plain(method):
                raise
            logger.warning(
                "تيليجرام رفض تنسيق الرسالة (%s) — أُعيد إرسالها نصاً عادياً.",
                str(exc)[:160],
            )
            return await super().__call__(method, request_timeout)
