"""
تعريب أسماء الخدمات المسحوبة من المزودين (رشق/اشتراكات/خدمات API/متاجر).

الخدمات تصل بالإنجليزية غالباً (مثل "TikTok Real Followers 1000").
هنا نبني اسماً عربياً سليماً:
- المنصة (تيك توك/إنستغرام/...) والنوع (متابعون/لايكات/...) من السجل
  المرجعي services.smm_catalog.
- بقية الكلمات المألوفة تُترجم (real/fast/hd/...) والباقي (أرقام وغير
  معروف) يبقى كما هو.
- أسماء المتاجر العامة (مثل Hyper Store: "Free Fire Diamonds 100"،
  "Netflix Premium 1 Month"، "Steam Wallet USD") تُترجم عبر سجل العلامات
  التجارية والكلمات الشائعة؛ إن لم يقع أي تغيير تبقى الكلمة الأصلية.
- الأسماء التي تحوي حروفاً عربية تُترك كما هي.
لا يُمس أي شيء لدى المزود — التعريب للعرض والبيع فقط.
"""

from __future__ import annotations

import re

from services.smm_catalog import (
    SMM_APP_SPECS,
    classify_smm_kind,
    normalize_label,
    resolve_smm_app,
)

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")

# عبارات/علامات تجارية شائعة في أسماء منتجات المتاجر (مثل Hyper Store)
# → نقل عربي. تُطابَق أطول عبارة أولاً (free fire max قبل free fire).
_STORE_BRAND_AR: dict[str, str] = {
    # ألعاب
    "free fire max": "فري فاير ماكس",
    "free fire": "فري فاير",
    "freefire": "فري فاير",
    "pubg mobile": "ببجي موبايل",
    "pubg": "ببجي",
    "mobile legends": "موبايل ليجندز",
    "mlbb": "موبايل ليجندز",
    "honor of kings": "هونر أوف كينجز",
    "clash of clans": "كلاش أوف كلانس",
    "clash royale": "كلاش رويال",
    "candy crush saga": "كاندي كرش",
    "candy crush": "كاندي كرش",
    "minecraft": "ماينكرافت",
    "roblox": "روبلوكس",
    "garena": "جورينا",
    "steam": "ستيم",
    "fortnite": "فورتنايت",
    "valorant": "فاليورانت",
    "counter strike": "كاونتر سترايك",
    "call of duty": "كول أوف ديوتي",
    "cod mobile": "كود موبايل",
    "league of legends": "ليج أوف ليجندز",
    "world of tanks": "وورلد أوف تانكس",
    "world of warcraft": "وورلد أوف ووركرافت",
    "grand theft auto": "جيتا",
    "gta sa": "جيتا",
    "gta 5": "جيتا 5",
    "gta": "جيتا",
    "dota 2": "دوتا 2",
    "dota": "دوتا",
    "cs go": "كاونتر سترايك",
    "fifa": "فيفا",
    "rocket league": "روكيت ليج",
    "brawl stars": "براول ستارز",
    "clash squad": "كلاش سكواد",
    # اشتراكات/تطبيقات
    "discord nitro": "ديسكورد نيترو",
    "chatgpt plus": "شات جي بي تي بلس",
    "chatgpt": "شات جي بي تي",
    "gemini pro": "جيميناي برو",
    "gemini": "جيميناي",
    "capcut": "كابكات",
    "claude": "كلود",
    "midjourney": "ميد جورني",
    "canva": "كانفا",
    "figma": "فيجما",
    "adobe": "أدوبي",
    "photoshop": "فوتوشوب",
    "premiere": "بريمير",
    "notion": "نوشن",
    "suno": "سونو",
    "eleven labs": "إليفن لابز",
    "gift cards": "بطاقات هدية",
    "gift card": "بطاقة هدية",
    "google play": "جوجل بلاي",
    "google one": "جوجل ون",
    "apple card": "أبل كارد",
    "paypal": "باي بال",
    "mastercard": "ماستر كارد",
    "visa": "فيزا",
    "netflix": "نتفليكس",
    "spotify": "سبوتيفاي",
    "youtube": "يوتيوب",
    "shazam": "شازام",
    "deezer": "ديزر",
    "soundcloud": "ساوند كلاود",
    "audible": "أودبل",
    "kindle": "كيندل",
    "hulu": "هولو",
    "disney plus": "ديزني بلس",
    "disney": "ديزني",
    "hotstar": "هوت ستار",
    "cricket": "كريكت",
    "twitch": "تويتش",
    "discord": "ديسكورد",
    "xbox": "إكس بوكس",
    "playstation": "بلايستيشن",
    "nintendo": "نينتندو",
    "switch": "سويتش",
    "nitro": "نيترو",
    "google": "جوجل",
    "apple": "أبل",
    "icloud": "آيكلاود",
    "iphone": "آيفون",
    "airpods": "إيربودز",
    "samsung": "سامسونج",
    "vivo": "فيفو",
    "oppo": "أوبو",
    "realme": "ريلمي",
    "xiaomi": "شاومي",
    "redmi": "ريدمي",
    "oneplus": "ون بلس",
    "honor": "هونر",
    "amazon": "أمازون",
    "ebay": "إيباي",
    "shopee": "شوبي",
    "lazada": "لازادا",
    "aliexpress": "علي إكسبرس",
    "temu": "تيمو",
    "etsy": "إيتسي",
    "binance": "بينانس",
    "coinbase": "كوين بيس",
    # مشغلو اتصال
    "vodafone": "فودافون",
    "etisalat": "اتصالات",
    "stc": "إس تي سي",
    "zain": "زين",
    "mobinil": "موبينيل",
    "airtel": "أيرتل",
    "jio": "جيو",
    "orange": "أورانج",
    # منصات (احتياط إن لم تتعرف كـ SMM app)
    "tiktok": "تيك توك",
    "instagram": "إنستغرام",
    "facebook": "فيسبوك",
    "whatsapp": "واتساب",
    "telegram": "تيليجرام",
}

# ترتيب مطابقة العبارات: الأطول (عدد كلمات ثم طول) أولاً.
_STORE_BRAND_ITEMS: list[tuple[str, str]] = sorted(
    _STORE_BRAND_AR.items(), key=lambda kv: (len(kv[0].split()), len(kv[0])), reverse=True
)
_STORE_BRAND_RES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b" + r"\s+".join(map(re.escape, brand.split())) + r"\b", re.IGNORECASE), arab)
    for brand, arab in _STORE_BRAND_ITEMS
]

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

# كلمات متاجر عامة (ألعاب/اشتراكات/شحن/بطاقات) غير مغطاة في _QUALIFIER_AR.
# تُستخدم في المسار العام (بلا منصة/نوع SMM معروف) وفي تعريب التصنيفات.
_STORE_WORD_AR: dict[str, str] = {
    # شحن وأرصدة
    "top": "أفضل",
    "recharge": "شحن",
    "recharging": "شحن",
    "wallet": "محفظة",
    "balance": "رصيد",
    "balances": "أرصدة",
    "cash": "نقدي",
    "credit": "رصيد",
    "airtime": "رصيد هاتف",
    "voucher": "قسيمة",
    "vouchers": "قسائم",
    "usd": "دولار",
    "usdt": "تيثر",
    "crypto": "كريبتو",
    # بطاقات وهدايا
    "card": "بطاقة",
    "cards": "بطاقات",
    "gift": "هدية",
    "gifts": "هدايا",
    "code": "كود",
    "codes": "أكواد",
    "coupon": "كوبون",
    "coupons": "كوبونات",
    # اشتراكات ومدد
    "sub": "اشتراك",
    "subs": "اشتراكات",
    "subscription": "اشتراك",
    "subscriptions": "اشتراكات",
    "vip": "VIP",
    "month": "شهر",
    "months": "أشهر",
    "week": "أسبوع",
    "weeks": "أسابيع",
    "day": "يوم",
    "days": "أيام",
    "hour": "ساعة",
    "hours": "ساعات",
    "year": "سنة",
    "years": "سنوات",
    # ألعاب
    "server": "سيرفر",
    "servers": "سيرفرات",
    "rank": "رانك",
    "boost": "بوست",
    "level": "مستوى",
    "levels": "مستويات",
    "gold": "ذهب",
    "silver": "فضة",
    "bronze": "برونز",
    "gem": "جوهرة",
    "gems": "جواهر",
    "robux": "روباكس",
    "skell": "سكيل",
    "skells": "سكيلات",
    "token": "توكن",
    "tokens": "توكنز",
    # «Call of Duty Mobile 80 CP» → «كول أوف ديوتي موبايل 80 CP»
    "mobile": "موبايل",
    # تواصل ورسائل
    "otp": "تأكيد",
    "verification": "توثيق",
    "verify": "توثيق",
    "message": "رسالة",
    "messages": "رسائل",
    "sms": "رسالة",
    "call": "مكالمة",
    "calls": "مكالمات",
    "number": "رقم",
    "numbers": "أرقام",
    "phone": "هاتف",
    "data": "بيانات",
    "internet": "إنترنت",
    "wifi": "واي فاي",
    "streaming": "بث",
    "stream": "بث",
    "photo": "صورة",
    "photos": "صور",
    "video": "فيديو",
    "videos": "فيديوهات",
    "music": "موسيقى",
    "song": "أغنية",
    "songs": "أغاني",
    "movie": "فيلم",
    "movies": "أفلام",
    "prime": "برايم",
    "store": "متجر",
    "play": "بلاي",
}

# تصنيفات المتاجر الشائعة → العربية.
_CATEGORY_AR: dict[str, str] = {
    "social media": "سوشيال ميديا",
    "social": "سوشيال",
    "gaming": "ألعاب",
    "games": "ألعاب",
    "game": "ألعاب",
    "e-commerce": "تجارة إلكترونية",
    "ecommerce": "تجارة إلكترونية",
    "shopping": "تسوق",
    "entertainment": "ترفيه",
    "subscriptions": "اشتراكات",
    "subscription": "اشتراكات",
    "top up": "شحن رصيد",
    "topup": "شحن رصيد",
    "recharge": "شحن رصيد",
    "gift cards": "بطاقات هدية",
    "gift card": "بطاقات هدية",
    "streaming": "بث",
    "music": "موسيقى",
    "movies": "أفلام",
    "tv": "تلفزيون",
    "accounts": "حسابات",
    "account": "حسابات",
    "vpn": "فبن",
    "otp": "رموز التحقق",
    "crypto": "عملات رقمية",
    "finance": "تمويل",
    "finance & business": "تمويل وأعمال",
    "business": "أعمال",
    "software": "برامج",
    "apps": "تطبيقات",
    "applications": "تطبيقات",
    "website": "مواقع",
    "domain": "دومين",
    "hosting": "استضافة",
    "email": "بريد إلكتروني",
    "phone": "هاتف",
    "mobile": "موبايل",
    "internet": "إنترنت",
    "lottery": "يانصيب",
    "sports": "رياضة",
    "travel": "سفر",
    "tickets": "تذاكر",
    "movie tickets": "تذاكر أفلام",
    "bills": "فواتير",
    "utilities": "فواتير",
    "books": "كتب",
    "education": "تعليم",
    "marketing": "تسويق",
    "design": "تصميم",
    "development": "تطوير",
    "fashion": "أزياء",
    "food": "طعام",
    "beauty": "تجميل",
    "health": "صحة",
    "auto": "سيارات",
    "home": "منزل",
}

# ترتيب بناء الاسم: النوع ثم البقية ثم المنصة
_STOPWORDS = {"and", "with", "the", "a", "an", "for", "in", "of", "on", "to", "up", "per", "each", "no", "or", "-", "_", "/", "&"}


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

    # اسم بلا منصة SMM معروفة (منتجات المتاجر/الألعاب العامة مثل "Free Fire
    # Diamonds 100" أو "Netflix Premium 1 Month") → ترجمة متاجر عامة:
    # علامات تجارية + كلمات مألوفة. إن لم يقع أي تغيير تُترك الكلمة
    # الأصلية كما هي (نحمي أسماء الباقات غير المعروفة).
    #
    # العلامة التجارية تتقدم على نوع SMM المصادَف: «Google Play Gift Card»
    # كانت تُصنَّف «مشاهدات» لأن ``play`` من أسماء نوع المشاهدات، فيخرج
    # للزبون اسم مشوّه («مشاهدات Google هدية Card USD (25)»).
    if app is None and (kind is None or _matches_store_brand(raw)):
        return _arabicize_store_name(raw)

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


def _matches_store_brand(raw: str) -> bool:
    """هل يحوي الاسم علامة تجارية معروفة من سجل المتاجر/الألعاب؟

    تُستخدم لتقديم مسار المتاجر على تصنيف SMM المصادَف (مثل ``play``
    في «Google Play» الذي كان يُحسب نوع «مشاهدات»).
    """
    return any(pattern.search(raw) for pattern, _arabic in _STORE_BRAND_RES)


def _arabicize_store_name(raw: str) -> str:
    """ترجمة أسماء منتجات المتاجر العامة (بلا منصة/نوع SMM معروف).

    - العبارات التجارية تُنقل (free fire → فري فاير، أطول عبارة أولاً).
    - الكلمات المألوفة تُترجم (diamonds → ماسات، month → شهر...).
    - الأرقام وغير المعروف يبقى كما هو وفي مكانه.
    - إن لم يقع أي تغيير تُعاد الكلمة الأصلية — نحمي أسماء الباقات
      غير المعروفة من ترجمة تفسدها.
    """
    text = " ".join(raw.split())
    changed = False
    for pattern, arab in _STORE_BRAND_RES:
        new_text, count = pattern.subn(arab, text)
        if count:
            text = new_text
            changed = True
    out: list[str] = []
    for word in text.split(" "):
        if not word:
            continue
        folded = normalize_label(word)
        arab = _STORE_WORD_AR.get(folded) or _QUALIFIER_AR.get(folded)
        if arab is not None:
            out.append(arab)
            changed = True
        elif word.lower() not in _STOPWORDS:
            out.append(word)
    if not changed:
        return raw
    return " ".join(p for p in out if p).strip()[:200]


def display_category_name(category: str | None) -> str:
    """التصنيف بالعربية: جدول تصنيفات شائعة، وإلا ترجمة كلمات مألوفة."""
    raw = str(category or "").strip()
    if not raw:
        return ""
    if is_arabic(raw):
        return raw
    folded = normalize_label(raw)
    for key, arab in _CATEGORY_AR.items():
        if normalize_label(key) == folded:
            return arab
    translated = _arabicize_store_name(raw)
    return translated if translated != raw else raw


def service_name_ar(service) -> str:
    """الاسم العربي لخدمة مزود محفوظة.

    يستخدم ``name_ar`` المحفوظ وقت السحب (كل الخدمات المسحوبة بعد
    الترقية تُعرَّب في لحظة السحب)، وللسجلات القديمة التي ما زالت بلا
    اسم عربي يبنى التعريب على الطايرة من الاسم الأصلي.
    """
    stored = str(getattr(service, "name_ar", None) or "").strip()
    if stored:
        return stored[:200]
    return display_service_name(
        str(getattr(service, "name", None) or ""),
        getattr(service, "category", None),
        getattr(service, "service_type", None),
    )


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
