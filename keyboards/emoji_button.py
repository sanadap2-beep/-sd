"""أزرار بإيموجي Telegram Premium المميز.

توحّد هذه الوحدة طريقة بناء أزرار العناصر الديناميكية (أقسام، أقسام فرعية،
خدمات أرقام، سيرفرات) وأزرار الواجهة المخصّصة من لوحة الأدمن:

- إن كان للعنصر ``custom_emoji_id`` → يُمرَّر إلى تيليجرام عبر
  ``icon_custom_emoji_id`` ويظهر كأيقونة ملوّنة داخل الزر، ويُحذف الإيموجي
  النصي من بداية الاسم حتى لا يظهر مرتين.
- وإلا → يُبنى الزر كالسابق تماماً (``<emoji> <name>``).

ملاحظة من وثائق Bot API: ``icon_custom_emoji_id`` يعمل فقط للبوتات التي اشترت
يوزر من Fragment، أو في الرسائل المباشرة (خاص/مجموعات) إذا كان مالك البوت
مشتركاً بـ Telegram Premium.
"""

from __future__ import annotations

import re

from aiogram.types import InlineKeyboardButton

# محارف الإيموجي وما يلحقها: مُعدّلات لون البشرة، مُنتقي التنويع،
# رابط العرض الصفري (ZWJ)، ومفتاح الإطار التركيبي (keycap).
_EMOJI_PART = (
    "\U0001f000-\U0001faff"  # رموز ورموز تعبيرية حديثة
    "\U0001fc00-\U0001ffff"
    "\u2600-\u27bf"  # رموز متنوعة وإيموجي كلاسيكي
    "\u2b00-\u2bff"  # أسهم ورموز متنوعة
    "\u2190-\u21ff"
    "\u3297\u3299\u00a9\u00ae\u2122\u2139"
    "\ufe0f\ufe0e\u20e3"  # منتقي التنويع + keycap
    "\U0001f3fb-\U0001f3ff"  # درجات لون البشرة
    "\U0001f1e6-\U0001f1ff"  # حروف الأعلام
    "\U000e0020-\U000e007f"  # وسم العلم (tag sequence)
    "\u200d"  # ZWJ
)

# إيموجي في بداية النص (مع ما يليه من مسافات).
_LEADING_EMOJI_RE = re.compile(rf"^[\s{_EMOJI_PART}]+")


def strip_leading_emoji(text: str | None) -> str:
    """يحذف الإيموجي من بداية النص (ولا يلمس الإيموجي في الوسط).

    إن كان النص كله إيموجي نُعيده كما هو: نص فارغ يرفضه تيليجرام.
    """
    if not text:
        return text or ""
    stripped = _LEADING_EMOJI_RE.sub("", text)
    return stripped or text


def extract_custom_emoji(message) -> str | None:
    """يستخرج مُعرّف الإيموجي المميز من رسالة (إن أُرسل إيموجي بريميوم)."""
    for entity in getattr(message, "entities", None) or []:
        if getattr(entity, "type", None) == "custom_emoji":
            emoji_id = getattr(entity, "custom_emoji_id", None)
            if emoji_id:
                return str(emoji_id)
    return None


def face(
    label: str,
    emoji: str | None = None,
    custom_emoji_id: str | None = None,
) -> tuple[str, dict]:
    """يرجع ``(نص الزر, kwargs)`` مع الأيقونة المميزة إن وُجدت."""
    text = f"{emoji} {label}".strip() if emoji else str(label)
    if not custom_emoji_id:
        return text, {}
    return strip_leading_emoji(text) or text, {"icon_custom_emoji_id": str(custom_emoji_id)}


def face_or(
    emoji_text: str,
    plain_text: str,
    custom_emoji_id: str | None = None,
) -> tuple[str, dict]:
    """للأزرار التي تبدأ ببادئة (حالة 🟢/⚪ أو «└»): الإيموجي ليس في البداية.

    يختار النص المناسب: ``emoji_text`` الكامل إن لم توجد أيقونة مميزة،
    و``plain_text`` (بلا الإيموجي النصي) إن وُجدت الأيقونة.
    """
    if custom_emoji_id:
        return plain_text, {"icon_custom_emoji_id": str(custom_emoji_id)}
    return emoji_text, {}


def icon_button(
    label: str,
    *,
    emoji: str | None = None,
    custom_emoji_id: str | None = None,
    **kwargs,
) -> InlineKeyboardButton:
    """زر داخلي بإيموجي مميز إن توفّر، وبنفس السلوك القديم إن لم يتوفر."""
    text, extra = face(label, emoji=emoji, custom_emoji_id=custom_emoji_id)
    return InlineKeyboardButton(text=text, **extra, **kwargs)
