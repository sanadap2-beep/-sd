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
BUILTIN_ENTRIES: tuple[tuple[str, str, str, int], ...] = (
    ("numbers", "📱 الأرقام", "num_hub", 10),
    ("offers", "🔥 العروض الخاصة 24", "special:home", 20),
    ("smart_featured", "⭐ مختارات المتجر", "store:section:featured", 30),
    ("smart_deals", "🔥 عروض اليوم", "store:section:deals", 31),
    ("smart_bestsellers", "🏆 الأكثر مبيعاً", "store:section:bestsellers", 32),
    ("smart_instant", "⚡ تسليم فوري", "store:section:instant", 33),
    ("smart_cheap", "💸 أقل من 2$", "store:section:cheap", 34),
    ("smart_games", "🎮 ألعاب", "store:section:games", 35),
    ("smart_smm", "📈 سوشيال ميديا", "store:section:smm", 36),
    ("smart_apps", "📦 تطبيقات واشتراكات", "store:section:apps", 37),
    ("search", "🔎 البحث عن خدمة", "menu:search", 100),
    ("cart", "🛒 السلة", "menu:cart", 110),
    ("request", "➕ اطلب منتج غير موجود", "menu:product_request", 120),
    ("webapp", "🌐 متجرك الكامل", "webapp", 130),
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
