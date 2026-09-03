"""
تعريب أسماء الخدمات المسحوبة من المزودين (رشق/اشتراكات/خدمات API).

الخدمات تصل بالإنجليزية غالباً (مثل "TikTok Real Followers 1000").
هنا نبني اسماً عربياً سليماً:
- المنصة (تيك توك/إنستغرام/...) والنوع (متابعون/لايكات/...) من السجل
  المرجعي services.smm_catalog.
- بقية الكلمات المألوفة تُترجم (real/fast/hd/...) والباقي (أرقام وغير
  معروف) يبقى كما هو.
- الأسماء التي تحوي حروفاً عربية تُترك كما هي.
لا يُمس أي شيء لدى المزود — التعريب للعرض والبيع فقط.
"""

from __future__ import annotations

import re

from services.smm_catalog import (
    SMM_APP_SPECS,
    SMM_KIND_SPECS,
    classify_smm_kind,
    normalize_label,
    resolve_smm_app,
)

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

# كلمات مألوفة في أسماء خدمات الرشق → العربية
_QUALIFIER_AR = {
    "real": "حقيقي",
    "reel": "ريلز",
    "reels": "ريلز",
    "fast": "سريع",
    "instant": "فوري",
    "instantly": "فوري",
    "hd": "HD",
    "premium": "مميز",
    "top": "أفضل",
    "high": "عالي",
    "quality": "جودة",
    "guaranteed": "مضمون",
    "guarantee": "مضمون",
    "refund": "مسترد",
    "refill": "مسترد",
    "cheapest": "أرخص",
    "cheap": "رخيص",
    "best": "أفضل",
    "popular": "شائع",
    "official": "رسمي",
    "normal": "عادي",
    "standard": "عادي",
    "basic": "أساسي",
    "new": "جديد",
    "daily": "يومي",
    "weekly": "أسبوعي",
    "monthly": "شهري",
    "yearly": "سنوي",
    "permanent": "دائم",
    "temp": "مؤقت",
    "unlimited": "غير محدود",
    "worldwide": "عالمي",
    "global": "عالمي",
    "abroad": "أجنبي",
    "foreign": "أجنبي",
    "local": "محلي",
    "arabic": "عربي",
    "english": "إنجليزي",
    "indian": "هندي",
    "american": "أمريكي",
    "uk": "بريطاني",
    "usa": "أمريكي",
    "us": "أمريكي",
    "active": "نشط",
    "live": "مباشر",
    "verified": "موثق",
    "plus": "بلس",
    "pro": "برو",
    "channel": "قناة",
    "channels": "قنوات",
    "broadcast": "نشر",
    "story": "ستوري",
    "stories": "قصص",
    "post": "منشور",
    "posts": "منشورات",
    "post": "منشور",
    "follow": "متابعة",
    "join": "انضمام",
    "download": "تحميل",
    "downloads": "تحميلات",
    "game": "لعبة",
    "games": "ألعاب",
    "account": "حساب",
    "accounts": "حسابات",
    "gift": "هدية",
    "gifts": "هدايا",
    "coin": "عملة",
    "coins": "عملات",
    "diamond": "ماسة",
    "diamonds": "ماسات",
    "point": "نقطة",
    "points": "نقاط",
}

# ترتيب بناء الاسم: النوع ثم البقية ثم المنصة
_STOPWORDS = {"and", "with", "the", "a", "an", "for", "in", "of", "on", "to", "-", "_", "/", "&"}


def is_arabic(text: str) -> bool:
    return bool(_ARABIC_RE.search(text or ""))


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9%]+|[^\sA-Za-z0-9%]+", text or "")


def _ar_platform_label(platform_key: str) -> str | None:
    for app in SMM_APP_SPECS:
        if app.name_ar == platform_key:
            return app.name_ar
    return None


def _strip_terms(text: str, terms: list[str]) -> str:
    """يحذف كلمات/مسميات معروفة (منصة/نوع) من الاسم النصي.

    الأطول أولاً حتى لا يترك "followers" حرف "s" بعد حذف "follower"،
    مع مراعاة حدود الكلمة (كلمة كاملة أو بـ s/ies).
    """
    result = text
    for term in sorted(
        (t for t in terms if t and any(ch.isalnum() for ch in t)),
        key=len,
        reverse=True,
    ):
        pattern = re.compile(
            r"\b" + re.escape(term) + r"(?:s|es)?\b", flags=re.IGNORECASE
        )
        result = pattern.sub(" ", result)
    return " ".join(result.split())


def arabicize_service_name(
    name: str,
    category: str | None = None,
    service_type: str | None = None,
) -> str:
    """
    يبني اسماً عربياً لخدمة مزود.

    أمثلة:
    - "TikTok Real Followers 1000" → "متابعون حقيقي — تيك توك (1000)"
    - "YouTube Views HD 10000"     → "مشاهدات HD — يوتيوب (10000)"
    - "متابعين حقيقيين"              → كما هي (عربي)
    - اسم بلا منصة/نوع معروف         → تُترجم كلماته المألوفة فقط
    """
    raw = (name or "").strip()
    if not raw:
        return "خدمة"
    if is_arabic(raw):
        return raw

    haystack = " ".join(part for part in (category, service_type, raw) if part)
    app = resolve_smm_app(category or "") or resolve_smm_app(
        service_type or ""
    ) or resolve_smm_app(raw) or resolve_smm_app(haystack)
    kind = (
        classify_smm_kind(category or "")
        or classify_smm_kind(service_type or "")
        or classify_smm_kind(raw)
        or classify_smm_kind(haystack)
    )

    # اسم بلا منصة/نوع معروف (مثل "Gemini Pro — 30 days") → نتركه كما هو:
    # الترجمة كلمة-كلمة لأسماء غير معروفة تفسد أسماء المنتجات/الباقات.
    if app is None and kind is None:
        return raw[:200]

    # نحذف مسميات المنصة والنوع من النص قبل ترجمة ما تبقى
    strip_terms: list[str] = []
    if app is not None:
        strip_terms.extend(list(app.aliases) + [app.name_ar])
    if kind is not None:
        strip_terms.extend(list(kind.aliases) + [kind.key, kind.name_ar])
    rest = _strip_terms(raw, strip_terms)

    # نقلب الكلمات المألوفة للعربية، الأرقام تبقى، وغير المعروف يبقى
    translated: list[str] = []
    quantity: list[str] = []
    for token in _tokenize(rest):
        folded = normalize_label(token)
        if token.isdigit():
            quantity.append(token)
            continue
        arab = _QUALIFIER_AR.get(folded)
        if arab is not None:
            translated.append(arab)
        elif token.lower() not in _STOPWORDS:
            translated.append(token)

    parts: list[str] = []
    if kind is not None:
        parts.append(kind.name_ar)
    parts.extend(translated)
    if app is not None:
        parts.append(app.name_ar)
    if quantity:
        parts.append("(" + " ".join(quantity) + ")")

    # (app or kind) مضمون الوجود هنا، فلا يكون الناتج فارغاً
    return " ".join(p for p in parts if p).strip()[:200]


def display_service_name(
    name: str,
    category: str | None = None,
    service_type: str | None = None,
) -> str:
    """الاسم المعروض للعربية (يُستخدم في شاشات الإدارة)."""
    raw = (name or "").strip()
    if not raw:
        return "خدمة"
    if is_arabic(raw):
        return raw
    return arabicize_service_name(raw, category, service_type)
