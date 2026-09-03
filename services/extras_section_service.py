"""
تحكم كامل بعناصر «الخدمات الأخرى» (extras) من لوحة الأدمن.

قائمة العناصر ثابتة ومشتقة من الكود (كل عنصر له key ثابت)، وحالة التفعيل
تُخزَّن في settings كخريطة key→bool بدون هجرة قاعدة بيانات. العنصر
المفتاقد في الخريطة = مفعّل افتراضياً.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from services.feature_service import FeatureService
from services.settings_service import SettingsService

SETTING_KEY = "extras_sections_json"

# (key, label, action, feature_key|None)
# feature_key: الميزة التي يجب أن تكون مفعلة أصلاً كي يظهر العنصر،
# (الميزات الاختيارية تُطفأ من «مركز الإضافات» وهنا نُخفيها إن عطّلها الأدمن.)
EXTRAS_ENTRIES: tuple[tuple[str, str, str, str | None], ...] = (
    ("withdraw", "💸 سحب الرصيد", "withdraw:home", None),
    ("search", "🔎 البحث عن خدمة", "menu:search", None),
    ("favorites", "⭐ المفضلة", "menu:favorites", None),
    ("cart", "🛒 السلة", "menu:cart", None),
    ("loyalty", "🎁 الولاء والمكافآت", "menu:loyalty", None),
    ("promotions", "🔥 العروض الحية", "menu:promotions", None),
    ("request", "📣 اطلب خدمة", "menu:product_request", None),
    ("gift", "🎁 بطاقة هدية", "menu:gift", None),
    ("assistant", "🧠 المساعد الذكي", "menu:assistant", None),
    ("offers", "🔥 العروض الخاصة 24", "special:home", None),
    ("ads", "📢 إعلاناتي", "ads:home", None),
    ("notif", "🔔 الإشعارات", "notif:home", None),
    ("status", "📡 حالة الخدمات", "menu:status", None),
    ("challenges", "🎯 التحديات", "menu:challenges", None),
    ("num_packages", "📦 باقات أرقام", "num_packages", None),
    ("number_exchange", "📈 بورصة الأرقام", "extras:exchange", "number_exchange"),
    ("number_portability", "🔁 أرقامي المحفوظة", "extras:portability", "number_portability"),
    ("vip_number_certificates", "👑 شهادات VIP", "extras:vip", "vip_number_certificates"),
    ("pooled_rooms", "👥 غرف الشراء الجماعي", "extras:rooms", "pooled_rooms"),
    ("revenue_sharing_tokens", "💹 أسهم حصة الإحالة", "extras:revshare", "revenue_sharing_tokens"),
    ("task_to_credit", "🧾 مهام مقابل رصيد", "extras:task2credit", "task_to_credit"),
    ("game_price_tracker", "🎮 أسعار الألعاب", "extras:gameprices", "game_price_tracker"),
    ("ai_agent_layer", "🤖 الوكيل الذكي", "extras:ai", "ai_agent_layer"),
    ("peer_marketplace", "🏪 سوق المستخدمين", "market:home", "peer_marketplace"),
    ("tasks_system", "🎯 المهام والنقاط", "tasks:home", "tasks_system"),
    ("points_currency", "⭐ نقاطي", "points:home", "points_currency"),
)

EXTRAS_KEYS = {key for key, _l, _a, _f in EXTRAS_ENTRIES}


@dataclass
class ExtrasEntry:
    key: str
    label: str
    action: str
    feature_key: str | None
    is_active: bool


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
        _key, _label, _action, feature_key = meta
        if feature_key and not await FeatureService.enabled(feature_key):
            return False
        state = _parse(await SettingsService.get(SETTING_KEY, None))
        return state.get(key, True)

    @staticmethod
    async def list_entries() -> list[ExtrasEntry]:
        state = _parse(await SettingsService.get(SETTING_KEY, None))
        entries = []
        for key, label, action, feature_key in EXTRAS_ENTRIES:
            entries.append(
                ExtrasEntry(
                    key=key,
                    label=label,
                    action=action,
                    feature_key=feature_key,
                    is_active=state.get(key, True),
                )
            )
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
        )
