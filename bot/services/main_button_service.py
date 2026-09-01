"""Dynamic main-menu buttons controlled by admin settings.

No database migration is needed: buttons are stored as JSON in the existing
settings table. This keeps the storefront flexible: the admin can add/remove
main buttons that open sections, products, internal bot pages, or external URLs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from uuid import uuid4

from database.models import Category, Product, ProductStatus, SubCategory
from services.settings_service import SettingsService

SETTING_KEY = "main_menu_buttons_json"


@dataclass
class MainMenuButton:
    id: str
    label: str
    action: str
    is_active: bool = True
    sort_order: int = 100

    @property
    def is_url(self) -> bool:
        return self.action.startswith("http://") or self.action.startswith("https://")


class MainButtonService:
    """CRUD for admin-created main menu buttons."""

    DEFAULT_BUTTONS = [
        MainMenuButton("store", "🛍 المتجر الشامل", "store:home", True, 10),
        MainMenuButton("number_packages", "📦 باقات أرقام جاهزة", "num_packages", True, 20),
    ]

    PRESETS = {
        "store": ("🛍 المتجر الشامل", "store:home"),
        "deals": ("🔥 عروض اليوم", "store:section:deals"),
        "instant": ("⚡ تسليم فوري", "store:section:instant"),
        "games": ("🎮 ألعاب", "store:section:games"),
        "smm": ("📈 سوشيال ميديا", "store:section:smm"),
        "apps": ("📦 تطبيقات واشتراكات", "store:section:apps"),
        "cheap": ("💸 أقل من 2$", "store:section:cheap"),
        "number_packages": ("📦 باقات أرقام جاهزة", "num_packages"),
        "product_request": ("➕ اطلب منتج غير موجود", "menu:product_request"),
        "cart": ("🛒 السلة", "menu:cart"),
    }

    @staticmethod
    def _serialize(buttons: list[MainMenuButton]) -> str:
        return json.dumps([asdict(button) for button in buttons], ensure_ascii=False)

    @staticmethod
    def _parse(raw: str | None) -> list[MainMenuButton]:
        if not raw:
            return list(MainButtonService.DEFAULT_BUTTONS)
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return list(MainButtonService.DEFAULT_BUTTONS)
        buttons = []
        if not isinstance(data, list):
            return list(MainButtonService.DEFAULT_BUTTONS)
        for item in data:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            action = str(item.get("action") or "").strip()
            if not label or not action:
                continue
            buttons.append(
                MainMenuButton(
                    id=str(item.get("id") or uuid4().hex[:8]),
                    label=label[:48],
                    action=action[:256],
                    is_active=bool(item.get("is_active", True)),
                    sort_order=int(item.get("sort_order", 100)),
                )
            )
        buttons.sort(key=lambda button: (button.sort_order, button.id))
        return buttons

    @staticmethod
    async def list_buttons(include_inactive: bool = True) -> list[MainMenuButton]:
        buttons = MainButtonService._parse(await SettingsService.get(SETTING_KEY, None))
        if include_inactive:
            return buttons
        return [button for button in buttons if button.is_active]

    @staticmethod
    async def save(session, buttons: list[MainMenuButton]) -> None:
        buttons.sort(key=lambda button: (button.sort_order, button.id))
        await SettingsService.set(session, SETTING_KEY, MainButtonService._serialize(buttons))

    @staticmethod
    async def add(session, label: str, action: str) -> MainMenuButton:
        buttons = await MainButtonService.list_buttons()
        next_order = max((button.sort_order for button in buttons), default=0) + 10
        button = MainMenuButton(
            id=uuid4().hex[:10],
            label=label.strip()[:48],
            action=action.strip()[:256],
            is_active=True,
            sort_order=next_order,
        )
        buttons.append(button)
        await MainButtonService.save(session, buttons)
        return button

    @staticmethod
    async def add_preset(session, preset: str) -> MainMenuButton | None:
        item = MainButtonService.PRESETS.get(preset)
        if item is None:
            return None
        label, action = item
        return await MainButtonService.add(session, label, action)

    @staticmethod
    async def toggle(session, button_id: str) -> MainMenuButton | None:
        buttons = await MainButtonService.list_buttons()
        found = None
        for button in buttons:
            if button.id == button_id:
                button.is_active = not button.is_active
                found = button
                break
        if found is None:
            return None
        await MainButtonService.save(session, buttons)
        return found

    @staticmethod
    async def delete(session, button_id: str) -> bool:
        buttons = await MainButtonService.list_buttons()
        remaining = [button for button in buttons if button.id != button_id]
        if len(remaining) == len(buttons):
            return False
        await MainButtonService.save(session, remaining)
        return True

    @staticmethod
    async def move(session, button_id: str, direction: int) -> MainMenuButton | None:
        buttons = await MainButtonService.list_buttons()
        index = next((i for i, button in enumerate(buttons) if button.id == button_id), None)
        if index is None:
            return None
        new_index = index + direction
        if new_index < 0 or new_index >= len(buttons):
            return buttons[index]
        buttons[index].sort_order, buttons[new_index].sort_order = (
            buttons[new_index].sort_order,
            buttons[index].sort_order,
        )
        await MainButtonService.save(session, buttons)
        return buttons[new_index]

    @staticmethod
    async def validate_action(session, action: str) -> tuple[bool, str]:
        """Check whether a button target still exists."""
        action = (action or "").strip()
        if action.startswith("https://") or action.startswith("http://"):
            return True, "رابط خارجي"
        if action in {"store:home", "menu:cart", "menu:search", "menu:product_request", "num_packages"}:
            return True, "صفحة داخلية"
        if action.startswith("store:section:"):
            section = action.rsplit(":", 1)[1]
            if section in {"featured", "deals", "bestsellers", "instant", "cheap", "games", "smm", "apps"}:
                return True, "قسم متجر سريع"
            return False, "قسم متجر سريع غير معروف"
        if action.startswith("cat:"):
            try:
                category = await session.get(Category, int(action.split(":", 1)[1]))
            except (TypeError, ValueError):
                return False, "رقم قسم غير صالح"
            if category is None:
                return False, "القسم الرئيسي غير موجود"
            if not category.is_active:
                return False, "القسم الرئيسي معطّل"
            return True, f"قسم رئيسي: {category.name_ar}"
        if action.startswith("subcat:"):
            try:
                subcategory = await session.get(SubCategory, int(action.split(":", 1)[1]))
            except (TypeError, ValueError):
                return False, "رقم قسم فرعي غير صالح"
            if subcategory is None:
                return False, "القسم الفرعي غير موجود"
            if not subcategory.is_active:
                return False, "القسم الفرعي معطّل"
            return True, f"قسم فرعي: {subcategory.name_ar}"
        if action.startswith("prod:"):
            try:
                product = await session.get(Product, int(action.split(":", 1)[1]))
            except (TypeError, ValueError):
                return False, "رقم منتج غير صالح"
            if product is None:
                return False, "المنتج غير موجود"
            if product.status != ProductStatus.ACTIVE:
                return False, "المنتج معطّل"
            return True, f"منتج: {product.name_ar}"
        if action.startswith(("menu:", "market:", "tasks:", "points:", "extras:")):
            return True, "مسار داخلي"
        return False, "إجراء غير معروف"

    @staticmethod
    async def broken_report(session) -> list[dict]:
        report = []
        for button in await MainButtonService.list_buttons(include_inactive=True):
            ok, reason = await MainButtonService.validate_action(session, button.action)
            if not ok:
                report.append(
                    {
                        "id": button.id,
                        "label": button.label,
                        "action": button.action,
                        "reason": reason,
                        "is_active": button.is_active,
                    }
                )
        return report

    @staticmethod
    async def reset_defaults(session) -> None:
        await MainButtonService.save(session, list(MainButtonService.DEFAULT_BUTTONS))
