"""
تحكم كامل بعناصر «الخدمات الأخرى» (extras) من لوحة الأدمن.

بدلاً من عرض 15+ زر عمودي للمستخدم، تُجمَّع العناصر في 3 أقسام رئيسية:
- المكافآت والولاء
- السوق والإعلانات
- أدوات وخدمات متقدمة

حالة تفعيل كل عنصر ما زالت تُدار من لوحة «🧩 التحكم بخدمات الأخرى» عبر
settings بدون هجرة قاعدة بيانات. العنصر المفقود في الخريطة = مفعّل افتراضياً.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from services.feature_service import FeatureService
from services.settings_service import SettingsService

SETTING_KEY = "extras_sections_json"

REWARDS = "rewards"
MARKET = "market"
TOOLS = "tools"

EXTRAS_SECTIONS: dict[str, tuple[str, str]] = {
    REWARDS: ("🎁 المكافآت والولاء", "Loyalty & rewards"),
    MARKET: ("📢 السوق والإعلانات", "Marketplace & ads"),
    TOOLS: ("🛠 أدوات وخدمات متقدمة", "Advanced tools & services"),
}

# (key, label, action, feature_key|None, section)
# feature_key: الميزة التي يجب أن تكون مفعلة أصلاً كي يظهر العنصر،
# (الميزات الاختيارية تُطفأ من «مركز الإضافات» وهنا نُخفيها إن عطّلها الأدمن.)
EXTRAS_ENTRIES: tuple[tuple[str, str, str, str | None, str], ...] = (
    # 🎁 المكافآت والولاء
    ("loyalty", "🎁 برنامج الولاء", "menu:loyalty", None, REWARDS),
    ("daily_spin", "🎰 عجلة الحظ اليومية", "engage:spin", "daily_spin", REWARDS),
    ("weekly_challenges", "🏅 تحديات أسبوعية", "engage:challenges", "weekly_challenges", REWARDS),
    ("top_buyers", "🏆 أبطال الأسبوع", "engage:leaderboard", "top_buyers", REWARDS),
    ("challenges", "🎯 التحديات", "menu:challenges", None, REWARDS),
    ("gift", "🎁 بطاقة هدية", "menu:gift", None, REWARDS),
    ("points_currency", "⭐ نقاطي", "points:home", "points_currency", REWARDS),
    ("tasks_system", "🎯 المهام", "tasks:home", "tasks_system", REWARDS),
    ("task_to_credit", "🧾 مهام مقابل رصيد", "extras:task2credit", "task_to_credit", REWARDS),
    ("revenue_sharing_tokens", "💹 أسهم حصة الإحالة", "extras:revshare", "revenue_sharing_tokens", REWARDS),
    ("topup_gift", "🎁 اشحن لأهلك", "extras:topup_gift", "mobile_topup_gift", REWARDS),

    # 📢 السوق والإعلانات
    ("peer_marketplace", "🏪 سوق المستخدمين Escrow", "market:home", "peer_marketplace", MARKET),
    ("ads", "📢 إعلاناتي المدفوعة", "ads:home", None, MARKET),
    ("offers", "🔥 العروض الخاصة 24", "special:home", None, MARKET),
    ("promotions", "🔥 العروض الحية", "menu:promotions", None, MARKET),
    ("request", "📣 اطلب خدمة", "menu:product_request", None, MARKET),
    ("pooled_rooms", "👥 غرف الشراء الجماعي", "extras:rooms", "pooled_rooms", MARKET),

    # 🛠 أدوات وخدمات متقدمة
    ("search", "🔎 البحث المباشر", "menu:search", None, TOOLS),
    ("favorites", "⭐ المفضلة", "menu:favorites", None, TOOLS),
    ("cart", "🛒 السلة", "menu:cart", None, TOOLS),
    ("status", "📡 حالة الخدمات", "menu:status", None, TOOLS),
    ("withdraw", "💸 سحب الرصيد", "withdraw:home", None, TOOLS),
    ("notif", "🔔 الإشعارات", "notif:home", None, TOOLS),
    ("assistant", "🧠 المساعد الذكي", "menu:assistant", None, TOOLS),
    ("num_packages", "📦 باقات أرقام", "num_packages", None, TOOLS),
    ("number_exchange", "📈 بورصة الأرقام", "extras:exchange", "number_exchange", TOOLS),
    ("number_portability", "🔁 أرقامي المحفوظة", "extras:portability", "number_portability", TOOLS),
    ("vip_number_certificates", "👑 شهادات VIP", "extras:vip", "vip_number_certificates", TOOLS),
    ("game_price_tracker", "🎮 أسعار الألعاب", "extras:gameprices", "game_price_tracker", TOOLS),
    ("ai_agent_layer", "🤖 الوكيل الذكي", "extras:ai", "ai_agent_layer", TOOLS),
    ("price_alerts", "🔔 إنذارات السعر", "engage:price_alerts", "price_alerts", TOOLS),
    ("number_resale_market", "🔄 سوق الأرقام المستعملة", "engage:resale", "number_resale_market", TOOLS),
    ("provider_reviews", "⭐ تقييم المزودين", "engage:reviews", "provider_reviews", TOOLS),
    ("enhanced_user_profile", "👤 ملفي المفصل", "engage:profile", "enhanced_user_profile", TOOLS),
    ("monthly_report", "📊 التقرير الشهري", "engage:report", "monthly_report", TOOLS),
)

EXTRAS_KEYS = {key for key, _l, _a, _f, _s in EXTRAS_ENTRIES}


@dataclass
class ExtrasEntry:
    key: str
    label: str
    action: str
    feature_key: str | None
    is_active: bool
    section: str = TOOLS


def _parse(raw: str | None) -> dict[str, bool]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: bool(v) for k, v in data.items() if k in EXTRAS_KEYS}


class ExtrasSectionService:
    """حالة إظهار/إخفاء عناصر «خدمات البوت الأخرى»."""

    @staticmethod
    async def is_visible(key: str) -> bool:
        """العنصر يظهر إذا كان مفعّلاً هنا والميزة المرتبطة به مفعّلة."""
        meta = next((item for item in EXTRAS_ENTRIES if item[0] == key), None)
        if meta is None:
            return False
        _key, _label, _action, feature_key, _section = meta
        if feature_key and not await FeatureService.enabled(feature_key):
            return False
        state = _parse(await SettingsService.get(SETTING_KEY, None))
        return state.get(key, True)

    @staticmethod
    async def list_entries(section: str | None = None) -> list[ExtrasEntry]:
        state = _parse(await SettingsService.get(SETTING_KEY, None))
        entries = []
        for key, label, action, feature_key, entry_section in EXTRAS_ENTRIES:
            if section is not None and entry_section != section:
                continue
            entries.append(
                ExtrasEntry(
                    key=key,
                    label=label,
                    action=action,
                    feature_key=feature_key,
                    is_active=state.get(key, True),
                    section=entry_section,
                )
            )
        return entries

    @staticmethod
    async def visible_entries(section: str | None = None) -> list[ExtrasEntry]:
        entries = []
        for entry in await ExtrasSectionService.list_entries(section=section):
            if await ExtrasSectionService.is_visible(entry.key):
                entries.append(entry)
        return entries

    @staticmethod
    async def toggle(session, key: str) -> ExtrasEntry | None:
        if key not in EXTRAS_KEYS:
            return None
        state = _parse(await SettingsService.get(SETTING_KEY, None))
        state[key] = not state.get(key, True)
        await SettingsService.set(
            session, SETTING_KEY, json.dumps(state, ensure_ascii=False)
        )
        entry = next(item for item in EXTRAS_ENTRIES if item[0] == key)
        return ExtrasEntry(
            key=key,
            label=entry[1],
            action=entry[2],
            feature_key=entry[3],
            is_active=state[key],
            section=entry[4],
        )
