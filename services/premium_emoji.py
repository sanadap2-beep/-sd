"""Telegram premium custom emojis for catalog names.

الخلفية: إيموجي تيليجرام المميز ليس حرفاً — بل كيان ``custom_emoji``
برقم تعريفي (19 رقماً) يصل ضمن ``entities`` الرسالة التي يرسلها الأدمن.
عرضه يحتاج إرسال النص مع مصفوفة ``entities`` صريحة بإزاحات UTF-16
(``parse_mode`` لا يجتمع مع ``entities``)، وأزرار الإنلاين تقبله فقط
عبر معامل ``icon_custom_emoji_id`` المنفصل.
"""

from __future__ import annotations

import logging
import re

from aiogram.types import Message, MessageEntity

logger = logging.getLogger(__name__)

_CUSTOM_ID_RE = re.compile(r"^\d{5,25}$")
# محرف بديل يُستبدل بالإيموجي المميز عند العرض (خارج BMP = وحدتا UTF-16).
_PLACEHOLDER = "\U0001F31F"


def is_valid_custom_id(value: object) -> bool:
    return bool(value) and bool(_CUSTOM_ID_RE.match(str(value).strip()))


def extract_custom_emoji_id(message: Message) -> str | None:
    """يستخرج الرقم التعريفي من رسالة الأدمن التي تحوي الإيموجي المميز."""
    for entity in message.entities or []:
        if entity.type == "custom_emoji" and is_valid_custom_id(
            getattr(entity, "custom_emoji_id", None)
        ):
            return str(entity.custom_emoji_id)
    return None


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


Segment = tuple
# ("t", text) | ("b", bold_text) | ("e", custom_id, fallback_char)


def render(*segments: Segment) -> tuple[str, list[MessageEntity]]:
    """يبني (نص + كيانات) جاهزة للإرسال بدون parse_mode.

    مثال:
        text, entities = render(
            ("e", cat.custom_emoji_id, cat.emoji),
            ("t", " "),
            ("b", cat.name_ar),
        )
    """
    parts: list[str] = []
    entities: list[MessageEntity] = []
    offset = 0
    for seg in segments:
        kind = seg[0]
        if kind == "t":
            chunk = str(seg[1])
            parts.append(chunk)
            offset += utf16_len(chunk)
        elif kind == "b":
            chunk = str(seg[1])
            parts.append(chunk)
            entities.append(
                MessageEntity(type="bold", offset=offset, length=utf16_len(chunk))
            )
            offset += utf16_len(chunk)
        elif kind == "e":
            custom_id = str(seg[1] or "").strip() if len(seg) > 1 else ""
            fallback = str(seg[2]) if len(seg) > 2 and seg[2] else _PLACEHOLDER
            if is_valid_custom_id(custom_id):
                parts.append(fallback)
                entities.append(
                    MessageEntity(
                        type="custom_emoji",
                        offset=offset,
                        length=utf16_len(fallback),
                        custom_emoji_id=custom_id,
                    )
                )
                offset += utf16_len(fallback)
            elif fallback:
                parts.append(fallback)
                offset += utf16_len(fallback)
        else:
            chunk = str(seg[1]) if len(seg) > 1 else ""
            parts.append(chunk)
            offset += utf16_len(chunk)
    return "".join(parts), entities


async def answer_rendered(message: Message, *segments: Segment, reply_markup=None) -> Message:
    """يرد على رسالة بنص قد يحوي إيموجي مميزاً (بدون parse_mode)."""
    text, entities = render(*segments)
    try:
        return await message.answer(text, entities=entities or None, reply_markup=reply_markup)
    except Exception:
        logger.exception("فشل إرسال رسالة بإيموجي مميز — إرسال نصي احتياطي")
        plain = "".join(
            (s[1] if s[0] in ("t", "b") else (s[2] if len(s) > 2 and s[2] else ""))
            for s in segments
            if isinstance(s, tuple) and len(s) > 1
        )
        return await message.answer(plain, reply_markup=reply_markup)


async def edit_rendered(message: Message, *segments: Segment, reply_markup=None) -> bool:
    """يعدّل رسالة بنص قد يحوي إيموجي مميزاً. يرجع False عند الفشل."""
    text, entities = render(*segments)
    try:
        await message.edit_text(text, entities=entities or None, reply_markup=reply_markup)
        return True
    except Exception:
        logger.warning("تعذر تعديل رسالة بإيموجي مميز")
        return False


def name_segments(
    name: str, emoji: str | None, custom_emoji_id: str | None
) -> list[Segment]:
    """مقاطع (مميز/بديل + مسافة + اسم عريض) لعنوان بطاقة."""
    return [
        ("e", custom_emoji_id or "", emoji or ""),
        ("t", " "),
        ("b", name or ""),
    ]
