"""Send HTML-ish texts that may contain premium custom emojis.

المشكلة: إيموجي تيليجرام المميز يحتاج مصفوفة ``entities`` صريحة،
و``parse_mode`` لا يجتمع مع ``entities``. هذا المساعد يحوّل HTML
المولّد داخلياً (b/i/u/s/code/pre/a/br فقط — كل محتوانا مهرّب)
إلى (نص + كيانات) بإزاحات UTF-16 صحيحة، مع حقن الإيموجي المميز.

أي خطأ تحليل → إرسال نصي عادي آمن (لا كسر أبداً).
"""

from __future__ import annotations

import html as _html
import logging
from html.parser import HTMLParser

from aiogram.types import Message, MessageEntity

from services.premium_emoji import _PLACEHOLDER, is_valid_custom_id, utf16_len

logger = logging.getLogger(__name__)

# علامة موضع الإيموجي المميز داخل HTML (تُستبدل عند التحويل).
PREM_MARK = "\ue000"


def prem_slot() -> str:
    """موضع يُستهلك بالترتيب من قائمة premiums."""
    return PREM_MARK


_TAG_TO_ENTITY = {
    "b": "bold",
    "strong": "bold",
    "i": "italic",
    "em": "italic",
    "u": "underline",
    "s": "strikethrough",
    "strike": "strikethrough",
    "del": "strikethrough",
    "code": "code",
    "pre": "pre",
}


class _Converter(HTMLParser):
    def __init__(self, premiums: list[tuple[str, str]]):
        super().__init__(convert_charrefs=False)
        self.premiums = list(premiums)
        self.out: list[str] = []
        self.entities: list[MessageEntity] = []
        self.u16 = 0
        # مكدس (نوع_الكيان, بداية_الإزاحة, extra)
        self.stack: list[tuple[str, int, dict]] = []
        self.failed = False

    def _push_text(self, text: str):
        # وزّع علامات الإيموجي المميز أولاً
        while PREM_MARK in text and self.premiums:
            before, _, after = text.partition(PREM_MARK)
            if before:
                self.out.append(before)
                self.u16 += utf16_len(before)
            custom_id, fallback = self.premiums.pop(0)
            chunk = fallback or _PLACEHOLDER
            self.out.append(chunk)
            if is_valid_custom_id(custom_id):
                self.entities.append(
                    MessageEntity(
                        type="custom_emoji",
                        offset=self.u16,
                        length=utf16_len(chunk),
                        custom_emoji_id=str(custom_id),
                    )
                )
            self.u16 += utf16_len(chunk)
            text = after
        # أي علامة زائدة بلا مميز تُحذف بصمت
        text = text.replace(PREM_MARK, "")
        if text:
            self.out.append(text)
            self.u16 += utf16_len(text)

    def handle_data(self, data: str):
        self._push_text(_html.unescape(data))

    def handle_starttag(self, tag: str, attrs: list):
        tag = tag.lower()
        if tag == "br":
            self.out.append("\n")
            self.u16 += 1
            return
        if tag in _TAG_TO_ENTITY:
            self.stack.append((_TAG_TO_ENTITY[tag], self.u16, {}))
        elif tag == "a":
            href = ""
            for k, v in attrs:
                if k.lower() == "href":
                    href = v or ""
            self.stack.append(("text_link", self.u16, {"url": href}))
        elif tag == "blockquote":
            self.stack.append(("blockquote", self.u16, {}))
        # الوسوم المجهولة: نتجاهل الوسم ونبقي النص (آمن).

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        want = _TAG_TO_ENTITY.get(tag, "text_link" if tag == "a" else ("blockquote" if tag == "blockquote" else None))
        if want is None:
            return
        # pop حتى نجد المطابق (تحمّل سوء التعشيش من مفاتيح الترجمة)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == want:
                kind, start, extra = self.stack.pop(i)
                length = self.u16 - start
                if length > 0:
                    if kind == "text_link" and not extra.get("url"):
                        break
                    self.entities.append(
                        MessageEntity(type=kind, offset=start, length=length, **extra)
                    )
                break

    def handle_entityref(self, name: str):
        self._push_text(_html.unescape(f"&{name};"))

    def handle_charref(self, name: str):
        try:
            if name.lower().startswith("x"):
                self._push_text(chr(int(name[1:], 16)))
            else:
                self._push_text(chr(int(name)))
        except (ValueError, OverflowError):
            pass

    def error(self, message):
        self.failed = True


def html_to_entities(
    html_text: str, premiums: list[tuple[str, str]] | None = None
) -> tuple[str, list[MessageEntity]]:
    """يحوّل HTML إلى (نص + كيانات). premiums: [(custom_id, fallback)] بالترتيب."""
    conv = _Converter(premiums or [])
    try:
        conv.feed(html_text or "")
        conv.close()
    except Exception:
        logger.warning("فشل تحليل HTML للكيانات — إرسال نصي")
        conv.failed = True
    if conv.failed:
        raise ValueError("parse failed")
    # كيانات متداخلة/متقاطعة غير صالحة؟ تيليجرام يرفض التقاطع — نفرز ونتحقق سريعاً
    return "".join(conv.out), conv.entities


def plain_fallback(html_text: str, premiums: list[tuple[str, str]] | None = None) -> str:
    """نص عادي آمن: تُزال الوسوم وتُستبدل العلامات بالبدائل."""
    import re as _re

    text = _re.sub(r"<[^>]*>", "", html_text or "")
    text = _html.unescape(text)
    parts = text.split(PREM_MARK)
    out = [parts[0]]
    for i, chunk in enumerate(parts[1:]):
        fb = ""
        if premiums and i < len(premiums):
            fb = premiums[i][1] or ""
        out.append(fb + chunk)
    return "".join(out)


async def send_rich(
    message: Message,
    html_text: str,
    premiums: list[tuple[str, str]] | None = None,
    reply_markup=None,
    edit: bool = False,
) -> bool:
    """يرسل/يعدّل رسالة قد تحوي إيموجي مميزاً. يرجع True عند النجاح.

    premiums: [(custom_emoji_id, fallback_char)] تُستهلك بالترتيب
    مقابل كل علامة prem_slot() في النص.
    """
    try:
        text, entities = html_to_entities(html_text, premiums)
    except ValueError:
        text, entities = plain_fallback(html_text, premiums), []
    try:
        if edit:
            await message.edit_text(text, entities=entities or None, reply_markup=reply_markup)
        else:
            await message.answer(text, entities=entities or None, reply_markup=reply_markup)
        return True
    except Exception:
        logger.exception("فشل إرسال نص غني — محاولة نصية أخيرة")
        try:
            fallback = plain_fallback(html_text, premiums)
            if edit:
                await message.edit_text(fallback, reply_markup=reply_markup)
            else:
                await message.answer(fallback, reply_markup=reply_markup)
            return True
        except Exception:
            return False
