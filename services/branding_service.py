"""هوية المتجر ونص الشاشة الأولى (رأس القائمة الرئيسية).

الهدف: بدلاً من رأس جاف مثل «🏠 القائمة الرئيسية»، نعرض شاشة أولى
تحمل هوية المتجر وتشرح للمستخدم ما يحصل عليه فعلاً، مع إبقاء كل
النصوص قابلة للتعديل من لوحة الإدارة عبر إعدادات:

- ``store_name``        — اسم المتجر، الافتراضي ``LUX STORE``.
- ``store_tagline``     — سطر تعريفي قصير أسفل الاسم.
- ``main_menu_features``— ميزات المتجر، سطر لكل ميزة (اختياري).

الإعدادات غير موجودة؟ تُستخدم النصوص الافتراضية في هذا الملف، ولا
يتغيّر سلوك البوت.
"""

from __future__ import annotations

from services.html_guard import esc

STORE_NAME_KEY = "store_name"
STORE_TAGLINE_KEY = "store_tagline"
MENU_FEATURES_KEY = "main_menu_features"

DEFAULT_STORE_NAME = "LUX STORE"

SEPARATOR = "────────────────"

_DEFAULTS: dict[str, dict[str, object]] = {
    "ar": {
        "tagline": "متجرك الرقمي للألعاب، الأرقام، الرشق والاشتراكات",
        "features": [
            "⚡ شحن سريع وآمن لجميع الخدمات",
            "💳 طرق دفع متعددة وسهلة",
            "🚀 تنفيذ طلباتك بأسرع وقت ممكن",
            "🛡 خدمة موثوقة ودعم جاهز لمساعدتك",
        ],
        "greeting": "أهلاً وسهلاً بك في بوت {store}",
        "balance": "💰 رصيدك",
        "choose": "اختر الخدمة التي تريدها من القائمة بالأسفل 👇",
    },
    "en": {
        "tagline": "Your digital store for games, numbers, SMM & subscriptions",
        "features": [
            "⚡ Fast & secure top-ups for every service",
            "💳 Multiple easy payment methods",
            "🚀 Orders executed as fast as possible",
            "🛡 Reliable service with ready support",
        ],
        "greeting": "Welcome to {store} bot",
        "balance": "💰 Your balance",
        "choose": "Choose the service you want from the menu below 👇",
    },
}


def _lang(language: str | None) -> str:
    return "en" if str(language or "").lower().startswith("en") else "ar"


def _pack(language: str | None) -> dict[str, object]:
    return _DEFAULTS[_lang(language)]


class BrandingService:
    """نصوص الهوية والشاشة الأولى — قابلة للتخصيص من الإعدادات."""

    # ── الاسم والسطر التعريفي ────────────────────────────────────
    @staticmethod
    async def _setting(key: str) -> str | None:
        """قراءة إعداد بأمان: أي فشل في قاعدة البيانات يُرجع ``None``."""
        try:
            from services.settings_service import SettingsService

            return await SettingsService.get(key)
        except Exception:  # noqa: BLE001 — نص تجميلي: لا يُسقط الشاشة الأولى
            return None

    @staticmethod
    async def store_name() -> str:
        name = ((await BrandingService._setting(STORE_NAME_KEY)) or "").strip()
        return name or DEFAULT_STORE_NAME

    @staticmethod
    async def tagline(language: str | None = "ar") -> str:
        value = await BrandingService._setting(STORE_TAGLINE_KEY)
        if value is None or not value.strip():
            return str(_pack(language)["tagline"])
        return value.strip()

    @staticmethod
    async def feature_lines(language: str | None = "ar") -> list[str]:
        """ميزات المتجر: من الإعدادات إن ضبطها الأدمن، وإلا الافتراضية."""
        raw = await BrandingService._setting(MENU_FEATURES_KEY)
        if raw and raw.strip():
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            if lines:
                return lines[:6]
        return list(_pack(language)["features"])  # type: ignore[arg-type]

    # ── رأس القائمة الرئيسية ─────────────────────────────────────
    @staticmethod
    async def main_menu_header(balance_display: str, language: str | None = "ar") -> str:
        """نص الشاشة الأولى: هوية المتجر + الميزات + الرصيد + سطر التوجيه."""
        lang = _lang(language)
        pack = _pack(lang)
        store = await BrandingService.store_name()
        tagline = await BrandingService.tagline(lang)
        greeting = str(pack["greeting"]).format(store=esc(store))

        lines: list[str] = [f"✨ <b>{greeting}</b> ⚡️"]
        if tagline:
            lines.append(f"<i>{esc(tagline)}</i>")
        lines.append("")
        for feature in await BrandingService.feature_lines(lang):
            lines.append(esc(feature))
        lines.append("")
        lines.append(f"{pack['balance']}: <b>{balance_display}</b>")
        lines.append(SEPARATOR)
        lines.append(str(pack["choose"]))
        return "\n".join(lines)

    # ── ترويسة ترحيبية قصيرة (للاستخدام في شاشات أخرى عند الحاجة) ─
    @staticmethod
    async def welcome_text(name: str, language: str | None = "ar") -> str:
        """رسالة ترحيب افتراضية بنفس هوية الشاشة الأولى."""
        custom = await BrandingService._setting("welcome_message")
        if custom and custom.strip():
            return custom.replace("{name}", name)

        lang = _lang(language)
        pack = _pack(lang)
        store = await BrandingService.store_name()
        lines = [
            f"✨ <b>{str(pack['greeting']).format(store=esc(store))}</b> ⚡️",
            "",
            f"👋 {'Hello' if lang == 'en' else 'أهلاً'} <b>{esc(name or '')}</b>!",
            f"<i>{esc(await BrandingService.tagline(lang))}</i>",
            "",
        ]
        for feature in await BrandingService.feature_lines(lang):
            lines.append(esc(feature))
        lines.append("")
        lines.append(SEPARATOR)
        lines.append(str(pack["choose"]))
        return "\n".join(lines)
