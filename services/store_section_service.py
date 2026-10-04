"""
تحكم كامل بأزرار صفحة المتجر من لوحة الأدمن.

يُخزَّن كل شيء في جدول الإعدادات (settings) كـ JSON بدون هجرة قاعدة بيانات:
- أزرار ثابتة قابلة للتفعيل/التعطيل: قسم الأرقام، العروض، الأقسام الذكية،
  البحث، السلة، طلب خدمة، الـ Mini App.
- أقسام مخصصة يضيفها الأدمن بنفسه (تفتح أي قسم/قسم فرعي/منتج/صفحة/رابط).
- الأقسام الديناميكية (categories) والخدمات (numbers) تبقى مرتبطة بملفها
  في «إدارة الأقسام» و«إدارة خدمات الأرقام» — تعطيل القسم من هناك يعطله
  في المتجر تلقائيا.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from uuid import uuid4

from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

SETTING_KEY = "store_sections_json"

# ─────────── الأزرار الثابتة المدمجة ───────────
# (key, label, action, sort_order)
#
# الأقسام الخمسة الرئيسية للمتجر (الرشق، شحن الألعاب، شحن البرامج،
# شحن الرصيد، الاشتراكات الرقمية) تأتي أولاً بعد الأرقام، وكل واحد
# منها قابل للإطفاء/التحريك/التسمية من «🛍 التحكم بالمتجر» في لوحة الأدمن.
# ملاحظة: قسم الأرقام **ليس** هنا عن قصد — له زر عريض مستقل في الشاشة
# الأولى، فلا يظهر داخل المتجر. إن أراده الأدمن داخل المتجر يضيفه بنفسه
# من «إضافة قسم» (الإجراء الجاهز: 📱 الأرقام (القسم الموحد)).
BUILTIN_ENTRIES: tuple[tuple[str, str, str, int], ...] = (
    ("smart_smm", "🚀 الرشق", "store:section:smm", 20),
    ("smart_games", "🎮 شحن الألعاب", "store:section:games", 21),
    ("smart_apps", "📱 شحن البرامج", "store:section:apps", 22),
    ("smart_balances", "💳 شحن الرصيد", "store:section:balances", 23),
    ("smart_subscriptions", "✨ الاشتراكات الرقمية", "store:section:subscriptions", 24),
    ("offers", "🔥 العروض الخاصة 24", "special:home", 30),
    ("smart_featured", "⭐ مختارات المتجر", "store:section:featured", 40),
    ("smart_deals", "🔥 عروض اليوم", "store:section:deals", 41),
    ("smart_bestsellers", "🏆 الأكثر مبيعاً", "store:section:bestsellers", 42),
    ("smart_instant", "⚡ تسليم فوري", "store:section:instant", 43),
    ("smart_cheap", "💸 أقل من 2$", "store:section:cheap", 44),
    ("search", "🔎 البحث عن خدمة", "menu:search", 100),
    ("cart", "🛒 السلة", "menu:cart", 110),
    ("request", "➕ اطلب منتج غير موجود", "menu:product_request", 120),
    ("webapp", "🌐 متجرك الكامل", "webapp", 130),
)

# الأقسام الخمسة الرئيسية: (مفتاح الإدارة، نوع الفئة = اسم القسم الذكي، التسمية).
# إن وُجدت فئة مفعّلة من هذا النوع يفتح الزر الفئة نفسها (فيحفظ تنقّل
# التطبيقات/الأقسام الداخلية)، وإلا يفتح القسم الذكي (قائمة منتجات مسطّحة).
PRIMARY_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("smart_smm", "smm", "🚀 الرشق"),
    ("smart_games", "games", "🎮 شحن الألعاب"),
    ("smart_apps", "apps", "📱 شحن البرامج"),
    ("smart_balances", "balances", "💳 شحن الرصيد"),
    ("smart_subscriptions", "subscriptions", "✨ الاشتراكات الرقمية"),
)

BUILTIN_KEYS = {key for key, _label, _action, _order in BUILTIN_ENTRIES}
BUILTIN_META = {key: (label, action, order) for key, label, action, order in BUILTIN_ENTRIES}


@dataclass
class StoreEntry:
    key: str
    label: str
    action: str
    is_active: bool
    sort_order: int
    is_builtin: bool = True
    # أيقونة إيموجي مميز لزر الدخول (تُعرض بدل بادئة الإيموجي العادي).
    icon_custom_emoji_id: str | None = None
    # مفتاح تخصيص الزر في لوحة الأدمن (لون/نص/إيموجي). يُستخدم عندما يُحَلّ
    # القسم إلى فئة حتى لا يفقد الزر إعدادات التخصيص.
    bc_key: str | None = None
    # أقسام المتجر الرئيسية (الرشق/الألعاب/البرامج/الرصيد/الاشتراكات):
    # تُرسم مباشرة بعد زر الأرقام قبل بقية الأقسام.
    is_primary: bool = False

    @property
    def is_url(self) -> bool:
        return self.action.startswith("http://") or self.action.startswith("https://")


def _builtin_entries() -> list[StoreEntry]:
    return [
        StoreEntry(key, label, action, True, order, is_builtin=True)
        for key, label, action, order in BUILTIN_ENTRIES
    ]


def _parse(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data.get("entries", {}) if isinstance(data.get("entries"), dict) else {}


class StoreSectionService:
    """CRUD لأزرار صفحة المتجر."""

    # ─────────── قراءة ───────────

    @staticmethod
    async def list_entries(include_inactive: bool = True) -> list[StoreEntry]:
        """كل الأزرار: المدمجة (مع أي تجاوزات محفوظة) + المخصصة، مرتبة."""
        entries = _builtin_entries()
        data = _parse(await SettingsService.get(SETTING_KEY, None))

        for entry in entries:
            saved = data.get(entry.key)
            if isinstance(saved, dict):
                entry.is_active = bool(saved.get("is_active", entry.is_active))
                try:
                    entry.sort_order = int(saved.get("sort_order", entry.sort_order))
                except (TypeError, ValueError):
                    pass

        for key, saved in data.items():
            if key in BUILTIN_KEYS or not isinstance(saved, dict):
                continue
            action = str(saved.get("action") or "").strip()
            label = str(saved.get("label") or "").strip()
            if not action or not label:
                continue
            entry = StoreEntry(
                key=key,
                label=label[:48],
                action=action[:256],
                is_active=bool(saved.get("is_active", True)),
                sort_order=int(saved.get("sort_order", 200) or 200),
                is_builtin=False,
            )
            entries.append(entry)

        entries.sort(key=lambda item: (item.sort_order, item.key))
        if include_inactive:
            return entries
        return [entry for entry in entries if entry.is_active]

    @staticmethod
    async def get(key: str) -> StoreEntry | None:
        for entry in await StoreSectionService.list_entries(include_inactive=True):
            if entry.key == key:
                return entry
        return None

    @staticmethod
    async def is_active(key: str) -> bool:
        entry = await StoreSectionService.get(key)
        if entry is None:
            return False
        return entry.is_active

    # ─────────── كتابة ───────────

    @staticmethod
    async def _save(session, entries: list[StoreEntry]) -> None:
        payload = {
            "entries": {
                entry.key: {
                    "label": entry.label,
                    "action": entry.action,
                    "is_active": entry.is_active,
                    "sort_order": entry.sort_order,
                }
                for entry in entries
            }
        }
        await SettingsService.set(
            session, SETTING_KEY, json.dumps(payload, ensure_ascii=False)
        )

    @staticmethod
    async def toggle(session, key: str) -> StoreEntry | None:
        entries = await StoreSectionService.list_entries(include_inactive=True)
        found = next((item for item in entries if item.key == key), None)
        if found is None:
            return None
        found.is_active = not found.is_active
        await StoreSectionService._save(session, entries)
        return found

    @staticmethod
    async def set_active(session, key: str, active: bool) -> StoreEntry | None:
        entries = await StoreSectionService.list_entries(include_inactive=True)
        found = next((item for item in entries if item.key == key), None)
        if found is None:
            return None
        found.is_active = active
        await StoreSectionService._save(session, entries)
        return found

    @staticmethod
    async def move(session, key: str, direction: int) -> StoreEntry | None:
        """يتحرك الزر فوق/تحت في ترتيب صفحة المتجر."""
        entries = sorted(
            await StoreSectionService.list_entries(include_inactive=True),
            key=lambda item: (item.sort_order, item.key),
        )
        index = next((i for i, item in enumerate(entries) if item.key == key), None)
        if index is None:
            return None
        new_index = index + direction
        if new_index < 0 or new_index >= len(entries):
            return entries[index]
        entries[index].sort_order, entries[new_index].sort_order = (
            entries[new_index].sort_order,
            entries[index].sort_order,
        )
        await StoreSectionService._save(session, entries)
        return entries[new_index]

    @staticmethod
    async def add_custom(session, label: str, action: str) -> StoreEntry:
        entries = await StoreSectionService.list_entries(include_inactive=True)
        max_order = max((item.sort_order for item in entries), default=0)
        entry = StoreEntry(
            key=f"custom_{uuid4().hex[:8]}",
            label=label.strip()[:48],
            action=action.strip()[:256],
            is_active=True,
            sort_order=max_order + 10,
            is_builtin=False,
        )
        entries.append(entry)
        await StoreSectionService._save(session, entries)
        return entry

    @staticmethod
    async def delete(session, key: str) -> bool:
        entries = await StoreSectionService.list_entries(include_inactive=True)
        remaining = [item for item in entries if item.key != key or item.is_builtin]
        if len(remaining) == len(entries):
            return False
        await StoreSectionService._save(session, remaining)
        return True

    @staticmethod
    async def reset_defaults(session) -> None:
        await StoreSectionService._save(session, _builtin_entries())

    # ─────────── بناء صفحة المتجر ───────────

    @staticmethod
    def resolve_primary_sections(
        entries: list[StoreEntry],
        categories: list,
    ) -> tuple[list[StoreEntry], set]:
        """اربط الأقسام الخمسة الرئيسية بالفئات الحقيقية إن وُجدت.

        - إن وُجدت فئة مفعّلة من نوع القسم (مثلاً ``smm``) صار الزر يفتحها،
          فيستفيد المستخدم من تنقّل التطبيقات/الأقسام الداخلية داخلها.
        - وإلا يبقى الزر يفتح القسم الذكي (قائمة منتجات مسطّحة من نفس النوع).
        - الأقسام التي يعطّلها الأدمن من «التحكم بالمتجر» تختفي تماماً.

        يُعاد: (قائمة الأقسام النهائية، أنواع الفئات التي غطّاها قسم رئيسي)
        حتى لا تتكرر نفس الفئة مرتين تحت القسم الذكي وكرر كفئة عادية.
        """
        by_key = {entry.key: entry for entry in entries}
        covered_types: set[str] = set()
        resolved: list[StoreEntry] = []

        for key, type_value, default_label in PRIMARY_SECTIONS:
            entry = by_key.get(key)
            if entry is not None and not entry.is_active:
                continue  # الأدمن أطفأ هذا القسم
            category = next(
                (
                    cat
                    for cat in sorted(
                        categories or [],
                        key=lambda item: (
                            getattr(item, "sort_order", 0) or 0,
                            getattr(item, "id", 0) or 0,
                        ),
                    )
                    if getattr(getattr(cat, "type", None), "value", None) == type_value
                ),
                None,
            )
            covered_types.add(type_value)
            resolved.append(
                StoreEntry(
                    key=key,
                    label=(entry.label if entry is not None else default_label) or default_label,
                    action=f"cat:{category.id}" if category is not None else f"store:section:{type_value}",
                    is_active=True,
                    sort_order=entry.sort_order if entry is not None else 20,
                    is_builtin=True,
                    bc_key=f"store.{type_value}",
                    is_primary=True,
                )
            )

        return resolved, covered_types

    @staticmethod
    def build_page_entries(
        entries: list[StoreEntry],
        categories: list,
    ) -> list[StoreEntry]:
        """القائمة النهائية لأزرار صفحة المتجر.

        تحافظ على كل ما يضيفه الأدمن (أقسام مخصصة، فئات ديناميكية) وتستبدل
        الأقسام الخمسة الرئيسية بنسختها المحلولة، مع حذف الفئات المكررة.
        """
        primary, covered_types = StoreSectionService.resolve_primary_sections(entries, categories)
        primary_keys = {key for key, _type, _label in PRIMARY_SECTIONS}

        rest = [
            entry
            for entry in entries
            if entry.key not in primary_keys
        ]
        # الفئات الديناميكية: تُضاف تلقائياً كما كانت، باستثناء ما غطّاه
        # قسم رئيسي لتجنّب تكرار «الرشق» مرتين مثلاً.
        for category in categories or []:
            type_value = getattr(getattr(category, "type", None), "value", None)
            if type_value in covered_types:
                continue
            cid = getattr(category, "custom_emoji_id", None)
            rest.append(
                StoreEntry(
                    key=f"cat:{category.id}",
                    label=category.name_ar if cid else f"{category.emoji} {category.name_ar}",
                    action=f"cat:{category.id}",
                    is_active=True,
                    sort_order=40 + min(max(getattr(category, "sort_order", 0) or 0, 0), 55),
                    is_builtin=True,
                    icon_custom_emoji_id=cid,
                )
            )

        page = primary + rest
        page.sort(key=lambda item: (item.sort_order, item.key))
        return page
