"""أنماط ألوان الأزرار الرسمية في تيليجرام (Native Button Style).

تعتمد على ``aiogram.enums.ButtonStyle`` (متوفّرة من aiogram 3.31.0+).
العملاء القدامى الذين لا يدعمون الحقل ``style`` يعرضون النمط الافتراضي
الرمادي تلقائياً — سلوك متوافق للخلف حسب توثيق تيليجرام.

جداول التصنيف هنا هي المصدر الوحيد المعتمد:
- ``classify`` / ``style_for_callback`` للتلوين اللحظي (أزرار ديناميكية).
- ``scripts/apply_button_styles.py`` يستوردها لتلوين/تدقيق الأزرار الثابتة.
"""

from __future__ import annotations

import re

from aiogram.enums import ButtonStyle

# 🟢 أخضر: إجراءات أساسية/متجر/إحصائيات إنجاز
STYLE_STORE = ButtonStyle.SUCCESS
# 🔵 أزرق: مالية/حساب/نقاط/طلبات
STYLE_ACCOUNT = ButtonStyle.PRIMARY
# 🔴 أحمر: شروط/تحذيرات/إلغاء
STYLE_WARNING = ButtonStyle.DANGER

__all__ = [
    "ButtonStyle",
    "STYLE_STORE",
    "STYLE_ACCOUNT",
    "STYLE_WARNING",
    "add_styled",
    "classify",
    "style_for_callback",
]

# ── ضبط حدود الكلمات ──
# بدون هذا الحارس التقط نمط "reset" كلمة "preset" فصُبغت أزرار قوالب
# إنشاء المزود بالأحمر بدل الأخضر. أي نمط يبدأ بحرف يجب أن يبدأ عند حدّ
# كلمة حقيقي (بداية النص، أو بعد ":" / "_" / "-") وإلا يُعتبر جزءاً من
# كلمة أطول.
_WORD_PREFIX = r"(?<![A-Za-z0-9])"


def _bounded(pattern: str) -> str:
    """يمنع مطابقة النمط داخل كلمة أطول: ``reset`` لا يطابق ``preset``."""
    if pattern.startswith(("^", ":", "_", "-", r"\b")):
        return pattern
    return _WORD_PREFIX + pattern


# أزرار التنقل تبقى بالنمط الافتراضي (رمادي).
NAV_EXACT = {
    "back_to_main",
    "noop",
    "ignore",
    "close",
    "store:home",
}
NAV_PATTERNS = tuple(
    _bounded(p)
    for p in (
        r"^back",
        r":back$",
        r":back:",
        r"^page:",
        r":page:",
        r"_page:",
        r"^nav:",
        r"page",
    )
)

DANGER_PATTERNS = tuple(
    _bounded(p)
    for p in (
        r"terms",
        r"cancel",
        r"delete",
        r"del:",
        r"_del",
        r"remove",
        r"revoke",
        r"void",
        r"ban",
        r"reject",
        r"refund",
        r"reset",
        r"maintenance_on",
        r"warn",
    )
)

PRIMARY_PATTERNS = tuple(
    _bounded(p)
    for p in (
        r"account",
        r"balance",
        r"deposit",
        r"transfer",
        r"points",
        r"loyalty",
        r"orders",
        r"order",
        r"my_",
        r"wallet",
        r"pay",
        r"coupon",
        r"gift",
        r"redeem",
        r"promo",
        r"referral",
        r"agent",
        r"fund",
        r"invoice",
        r"rates",
        r"margin",
        r"stats",
        r"cart",
        r"checkout",
        r"buy",
        r"confirm",
    )
)

SUCCESS_PATTERNS = tuple(
    _bounded(p)
    for p in (
        r"^store",
        r"^shop",
        r"^cat",
        r"^prod",
        r"^subcat",
        r"^num",
        r"^svc",
        r"^service",
        r"^games",
        r"^smm",
        r"^market",
        r"^extras",
        r"^menu:search",
        r"special",
        r"request",
        r"add$",
        r"_add",
        r":add",
        r"create",
        r"enable",
        # «preset» = زر إنشاء بقوالب جاهزة، وليس حذفاً (قريب من "reset").
        r"preset",
        r"^admin:main$",
    )
)

NAV_TEXT_MARKERS = (
    "🔙", "🏠", "◀", "▶", "⬅", "➡", "⏮", "⏭", "«", "»",
    "رجوع", "عودة", "السابق", "التالي",
    "back", "next", "prev", "home",
)

# Semantically-confirmed actions whose color differs from the raw patterns
# (e.g. "mkt_deliver" matches "_del" but means delivery confirmation).
STYLE_OVERRIDES = {
    "mkt_deliver:": "primary",
}


def is_nav_text(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(marker in low for marker in NAV_TEXT_MARKERS)


def classify(callback: str, text_hint: str = "") -> str | None:
    """يصنّف callback_data إلى لون زر، أو None لترك الزر بالنمط الافتراضي."""
    if text_hint and is_nav_text(text_hint):
        return None
    cb = (callback or "").strip().lower()
    if not cb:
        return None
    if cb in NAV_EXACT and cb != "store:home":
        return None
    for prefix, style in STYLE_OVERRIDES.items():
        if cb.startswith(prefix):
            return style
    for pat in NAV_PATTERNS:
        if re.search(pat, cb):
            return None
    for pat in DANGER_PATTERNS:
        if re.search(pat, cb):
            return "danger"
    for pat in PRIMARY_PATTERNS:
        if re.search(pat, cb):
            return "primary"
    for pat in SUCCESS_PATTERNS:
        if re.search(pat, cb):
            return "success"
    return None


def style_for_callback(callback_data: str, text: str = "") -> str | None:
    """Alias لحظي لـ classify — للأزرار المبنية ديناميكياً وقت التشغيل."""
    return classify(callback_data or "", text or "")


def add_styled(builder, text: str, callback_data: str, style: str, **kwargs):
    """إضافة زر بنمط لوني موحّد عبر ``InlineKeyboardBuilder``."""
    builder.button(text=text, callback_data=callback_data, style=style, **kwargs)
    return builder
