"""Canonical SMM (رشق) apps: names, emojis, and text matching.

The storefront shows these ten platforms as sub-categories. Older databases
sometimes stored English names, mixed the emoji into ``name_ar``, or left the
default 📱 emoji. Matching is used both when seeding/updating rows and when a
user taps a leftover reply-keyboard button such as ``تيك توك 🎵``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class SmmApp:
    name_ar: str
    emoji: str
    aliases: tuple[str, ...]


# Ten social apps shown under قسم الرشق. Names are Arabic; emojis sit beside
# the name on buttons (never inside name_ar).
SMM_APP_SPECS: tuple[SmmApp, ...] = (
    SmmApp("تيك توك", "🎵", ("tiktok", "tik tok", "tik-tok", "تيكتوك", "tik_tok")),
    SmmApp("إنستغرام", "📸", ("instagram", "insta", "انستغرام", "انستقرام", "إنستقرام")),
    SmmApp("يوتيوب", "▶️", ("youtube", "you tube", "yt")),
    SmmApp("تيليجرام", "✈️", ("telegram", "tg", "تليجرام", "تلغرام")),
    SmmApp("فيسبوك", "👍", ("facebook", "fb", "فيس بوك")),
    SmmApp("واتساب", "💬", ("whatsapp", "whats app", "wa", "واتس", "واتس اب", "واتسآب")),
    SmmApp("سناب شات", "👻", ("snapchat", "snap chat", "snap", "سناب")),
    SmmApp("تويتر", "🐦", ("twitter", "x.com", "إكس", "اكس", "إكس (تويتر)", "اكس (تويتر)")),
    SmmApp("ثريدز", "🧵", ("threads", "thread")),
    SmmApp("سبوتيفاي", "🎧", ("spotify", "spoti")),
)


# Backward-compatible (name, emoji) pairs used by seed and tests.
SMM_APPS: list[tuple[str, str]] = [(app.name_ar, app.emoji) for app in SMM_APP_SPECS]


_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002700-\U000027bf"
    "\U00002600-\U000026ff"
    "\U0000fe00-\U0000fe0f"
    "\U0001f1e6-\U0001f1ff"
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """Remove emoji / pictographs so ``تيك توك 🎵`` compares equal to ``تيك توك``."""
    cleaned = _EMOJI_RE.sub(" ", text or "")
    cleaned = "".join(
        ch for ch in cleaned if unicodedata.category(ch) not in {"So", "Sk"}
    )
    return " ".join(cleaned.split())


def normalize_label(text: str) -> str:
    """Fold Arabic letter variants and spacing for fuzzy matching."""
    text = strip_emoji(text).casefold()
    text = (
        text.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ة", "ه")
        .replace("ى", "ي")
        .replace("_", " ")
        .replace("-", " ")
        .replace("(", " ")
        .replace(")", " ")
    )
    return " ".join(text.split())


def button_label(name_ar: str, emoji: str | None) -> str:
    """``🎵 تيك توك`` without duplicating an emoji already stored in the name."""
    name = (name_ar or "").strip()
    mark = (emoji or "").strip()
    if mark and (name.startswith(mark) or name.endswith(mark)):
        return name
    if mark:
        return f"{mark} {name}".strip()
    return name


def labels_for(app: SmmApp) -> tuple[str, ...]:
    return (
        app.name_ar,
        f"{app.emoji} {app.name_ar}",
        f"{app.name_ar} {app.emoji}",
        *app.aliases,
    )


def resolve_smm_app(text: str) -> SmmApp | None:
    """Return the canonical app if ``text`` is one of the ten platforms."""
    raw = (text or "").strip()
    if not raw:
        return None
    folded = normalize_label(raw)
    if not folded:
        return None

    for app in SMM_APP_SPECS:
        candidates = [normalize_label(label) for label in labels_for(app)]
        for candidate in candidates:
            if not candidate:
                continue
            if folded == candidate:
                return app
            # Provider categories look like "TikTok Followers" / "متابعين تيك توك".
            if len(candidate) >= 4 and (
                candidate in folded.split()
                or f" {candidate} " in f" {folded} "
                or folded.startswith(candidate)
                or folded.endswith(candidate)
            ):
                return app
    return None


@dataclass(frozen=True)
class SmmKind:
    key: str
    name_ar: str
    emoji: str
    aliases: tuple[str, ...]


SMM_KIND_SPECS: tuple[SmmKind, ...] = (
    SmmKind("followers", "متابعون", "👤", ("follower", "followers", "متابع", "متابعين", "subscribe", "subscribers")),
    SmmKind("likes", "لايكات", "❤️", ("like", "likes", "لايك", "لايكات", "إعجاب", "اعجاب", "heart")),
    SmmKind("views", "مشاهدات", "👁", ("view", "views", "مشاهدة", "مشاهدات", "play", "plays")),
    SmmKind("comments", "تعليقات", "💬", ("comment", "comments", "تعليق", "تعليقات")),
    SmmKind("shares", "مشاركات", "🔁", ("share", "shares", "repost", "reposts", "retweet", "مشاركة", "مشاركات", "ريبوست")),
    SmmKind("saves", "حفظ", "🔖", ("save", "saves", "bookmark", "حفظ")),
    SmmKind("story", "ستوري", "📱", ("story", "stories", "ستوري", "قصص")),
    SmmKind("live", "بث مباشر", "🔴", ("live", "livestream", "بث", "لايف")),
    SmmKind("members", "أعضاء", "👥", ("member", "members", "أعضاء", "اعضاء", "group")),
    SmmKind("reactions", "تفاعلات", "😍", ("reaction", "reactions", "تفاعل", "تفاعلات")),
)

OTHER_PLATFORM_KEY = "other"
OTHER_KIND_KEY = "other"

# Short ASCII keys for Telegram callback_data (max 64 bytes).
PLATFORM_SHORT_KEYS: dict[str, str] = {
    "تيك توك": "tt",
    "إنستغرام": "ig",
    "يوتيوب": "yt",
    "تيليجرام": "tg",
    "فيسبوك": "fb",
    "واتساب": "wa",
    "سناب شات": "sc",
    "تويتر": "tw",
    "ثريدز": "th",
    "سبوتيفاي": "sp",
}
SHORT_TO_PLATFORM: dict[str, str] = {short: name for name, short in PLATFORM_SHORT_KEYS.items()}


def classify_smm_kind(text: str) -> SmmKind | None:
    """Return likes/views/followers/... if the provider name/category mentions it."""
    folded = normalize_label(text)
    if not folded:
        return None
    best: SmmKind | None = None
    best_len = 0
    for kind in SMM_KIND_SPECS:
        for alias in (kind.key, kind.name_ar, *kind.aliases):
            candidate = normalize_label(alias)
            if not candidate:
                continue
            if candidate == folded or f" {candidate} " in f" {folded} " or folded.startswith(candidate) or folded.endswith(candidate):
                if len(candidate) > best_len:
                    best = kind
                    best_len = len(candidate)
    return best


def classify_smm_service(
    name: str | None = None,
    category: str | None = None,
    service_type: str | None = None,
) -> tuple[str, str]:
    """Return ``(platform_key, kind_key)`` for a pulled provider service.

    platform_key is the canonical SMM app ``name_ar`` (إنستغرام, تيك توك, ...)
    or ``other``. kind_key is likes/followers/views/... or ``other``.
    """
    haystack = " ".join(part for part in (category, service_type, name) if part)
    app = (
        resolve_smm_app(category or "")
        or resolve_smm_app(service_type or "")
        or resolve_smm_app(name or "")
        or resolve_smm_app(haystack)
    )
    kind = (
        classify_smm_kind(category or "")
        or classify_smm_kind(service_type or "")
        or classify_smm_kind(name or "")
        or classify_smm_kind(haystack)
    )
    if app:
        platform_key = PLATFORM_SHORT_KEYS.get(app.name_ar, OTHER_PLATFORM_KEY)
    else:
        platform_key = OTHER_PLATFORM_KEY
    kind_key = kind.key if kind else OTHER_KIND_KEY
    return platform_key, kind_key


def platform_meta(platform_key: str) -> tuple[str, str]:
    """``(emoji, label)`` for a platform key (short ASCII or Arabic name)."""
    if platform_key == OTHER_PLATFORM_KEY:
        return "📦", "خدمات أخرى"
    name_ar = SHORT_TO_PLATFORM.get(platform_key, platform_key)
    for app in SMM_APP_SPECS:
        if app.name_ar == name_ar or PLATFORM_SHORT_KEYS.get(app.name_ar) == platform_key:
            return app.emoji, app.name_ar
    return "📦", platform_key


def kind_meta(kind_key: str) -> tuple[str, str]:
    if kind_key == OTHER_KIND_KEY:
        return "📂", "أنواع أخرى"
    for kind in SMM_KIND_SPECS:
        if kind.key == kind_key:
            return kind.emoji, kind.name_ar
    return "📂", kind_key


def is_smm_app_label(text: str) -> bool:
    """True only when the whole message is an app button label (not a sentence)."""
    raw = (text or "").strip()
    if not raw:
        return False
    folded = normalize_label(raw)
    if not folded:
        return False
    for app in SMM_APP_SPECS:
        for label in labels_for(app):
            if folded == normalize_label(label):
                return True
    return False
