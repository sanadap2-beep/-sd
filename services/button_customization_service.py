"""Admin-managed customization for labels, emojis, and colors of buttons.

Every button key that appears in the registry can be overridden at runtime:
- label: full replacement of the visible text (including its emoji prefix)
- custom_emoji_id: a Telegram premium custom emoji rendered on the button
- style: one of success / primary / danger / gray

Overrides are stored as JSON in the settings table (no migration needed),
mirroring ``MainButtonService``. Builders call ``resolve`` / ``apply`` and
fall back to the registry defaults when nothing is overridden.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from aiogram.types import InlineKeyboardButton

from keyboards.admin import ADMIN_TABS
from services.settings_service import SettingsService

SETTING_KEY = "button_overrides_json"

STYLES = ("success", "primary", "danger", "gray")


@dataclass
class ButtonSpec:
    key: str
    label: str
    style: str | None = None
    callback_data: str | None = None


@dataclass
class ButtonSection:
    key: str
    title: str
    buttons: list[ButtonSpec] = field(default_factory=list)


# ══════════════ شجرة الأزرار ══════════════

MAIN_MENU_BUTTONS = [
    ButtonSpec("main.numbers", "📞 الأرقام", "success", "num_hub"),
    ButtonSpec("main.store", "🛍 المتجر", "success", "store:home"),
    ButtonSpec("main.market", "🏪 سوق المستخدمين", "success", "market:home"),
    ButtonSpec("main.completed_orders", "🛡 طلبات منجزة ({count})", "success", "info:stats"),
    ButtonSpec("main.account", "👤 حسابي ({balance})", "primary", "menu:account"),
    ButtonSpec("main.deposit", "💰 شحن الرصيد", "primary", "menu:deposit"),
    ButtonSpec("main.transfer", "💱 تحويل رصيد", "primary", "menu:transfer"),
    ButtonSpec("main.referral", "💎 النقاط والإحالة", "primary", "menu:referral"),
    ButtonSpec("main.extras", "🧩 خدمات إضافية", "success", "extras:home"),
    ButtonSpec("main.ai", "🤖 الذكاء الاصطناعي", "success", "ai:home"),
    ButtonSpec("main.whatsapp", "📱 واتساب", "success", "wa:home"),
    ButtonSpec("main.agent", "💼 كن وكيلاً ({percent}%)", "primary", "agent:home"),
    ButtonSpec("main.terms", "🔺 شروط الاستخدام", "danger", "info:terms"),
    ButtonSpec("main.support", "🛠 التواصل مع الدعم", None, "menu:support"),
]

NUMBERS_BUTTONS = [
    ButtonSpec("numbers.tg_ready", "⚡ أرقام تليجرام جاهزة", "success", "tgready:list"),
    ButtonSpec("numbers.packages", "📦 باقات أرقام جاهزة", "primary", "num_packages"),
]

STORE_BUTTONS = [
    ButtonSpec("store.numbers", "📞 الأرقام", "success", "num_hub"),
    ButtonSpec("store.smm", "🚀 الرشق", "primary", "store:section:smm"),
    ButtonSpec("store.games", "🎮 شحن الألعاب", "success", "store:section:games"),
    ButtonSpec("store.apps", "📱 شحن البرامج", "primary", "store:section:apps"),
    ButtonSpec("store.balances", "💳 شحن الرصيد", "primary", "store:section:balances"),
    ButtonSpec("store.subscriptions", "✨ الاشتراكات الرقمية", "success", "store:section:subscriptions"),
    ButtonSpec("store.featured", "⭐ مختارات المتجر", "primary", "store:section:featured"),
    ButtonSpec("store.deals", "🔥 عروض اليوم", "danger", "store:section:deals"),
    ButtonSpec("store.bestsellers", "🏆 الأكثر مبيعاً", "primary", "store:section:bestsellers"),
    ButtonSpec("store.instant", "⚡ تسليم فوري", "success", "store:section:instant"),
    ButtonSpec("store.cheap", "💸 أقل من 2$", "primary", "store:section:cheap"),
    ButtonSpec("store.webapp", "🌐 المتجر الإلكتروني", "success", "webapp"),
    ButtonSpec("store.search", "🔎 بحث بالمتجر", None, "menu:search"),
    ButtonSpec("store.cart", "🛒 السلة", "primary", "menu:cart"),
    ButtonSpec("store.product_request", "➕ اطلب منتج غير موجود", None, "menu:product_request"),
]

EXTRAS_BUTTONS = [
    ButtonSpec("extras.rewards", "🎁 المكافآت والولاء", "success", "extras:section:rewards"),
    ButtonSpec("extras.market", "📢 السوق والإعلانات", "success", "extras:section:market"),
    ButtonSpec("extras.tools", "🛠 أدوات وخدمات متقدمة", "success", "extras:section:tools"),
]

DEPOSIT_BUTTONS = [
    ButtonSpec("deposit.shamcash_manual", "💵 شام كاش يدوي", "primary", "deposit:shamcash_manual"),
    ButtonSpec("deposit.stars", "⭐ نجوم تليجرام", "primary", "deposit:stars"),
    ButtonSpec("deposit.shamcash_auto", "💳 شام كاش تلقائي", "primary", "deposit:shamcash_auto"),
    ButtonSpec("deposit.usdt_auto", "₮ USDT تلقائي", "primary", "deposit:usdt_auto"),
    ButtonSpec("deposit.usdt_manual", "₮ USDT يدوي", "primary", "deposit:usdt_manual"),
    ButtonSpec("deposit.mobile_credit", "📲 رصيد جوال", "primary", "deposit:mobile_credit"),
    ButtonSpec("deposit.other", "📞 طرق دفع أخرى", "primary", "deposit:other"),
]

ACCOUNT_BUTTONS = [
    ButtonSpec("account.number_orders", "📅 طلبات الأرقام", "primary", "my_num_orders:0"),
    ButtonSpec("account.other_orders", "🧾 طلباتي الأخرى", "primary", "my_uni_orders:0"),
    ButtonSpec("account.transactions", "📊 سجل المعاملات", "primary", "my_transactions:0"),
    ButtonSpec("account.watches", "🔔 تنبيهاتي", "danger", "my_watches"),
    ButtonSpec("account.currency", "💱 العملة", None, "menu:currency"),
    ButtonSpec("account.language", "🌐 اللغة", None, "menu:language"),
]


def _admin_sections() -> list[ButtonSection]:
    sections: list[ButtonSection] = []
    for tab_key, (title, items) in ADMIN_TABS.items():
        sections.append(
            ButtonSection(
                key=f"admin.tab.{tab_key}",
                title=f"🎛 أدمن: {title}",
                buttons=[
                    ButtonSpec(f"admin.item.{callback}", label, None, callback)
                    for label, callback in items
                ],
            )
        )
    return sections


def button_tree() -> list[ButtonSection]:
    return [
        ButtonSection("main", "🏠 القائمة الرئيسية", MAIN_MENU_BUTTONS),
        ButtonSection("numbers", "📞 قسم الأرقام", NUMBERS_BUTTONS),
        ButtonSection("store", "🛍 المتجر", STORE_BUTTONS),
        ButtonSection("extras", "🧩 الإضافات", EXTRAS_BUTTONS),
        ButtonSection("deposit", "💰 طرق الشحن", DEPOSIT_BUTTONS),
        ButtonSection("account", "👤 الحساب", ACCOUNT_BUTTONS),
        *_admin_sections(),
    ]


def find_spec(key: str) -> ButtonSpec | None:
    for section in button_tree():
        for spec in section.buttons:
            if spec.key == key:
                return spec
    return None


def find_section(section_key: str) -> ButtonSection | None:
    for section in button_tree():
        if section.key == section_key:
            return section
    return None


class ButtonCustomizationService:
    """Read/write overrides stored in settings JSON."""

    @staticmethod
    async def _load() -> dict:
        raw = await SettingsService.get(SETTING_KEY, "{}")
        try:
            data = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    async def _save(session, data: dict) -> None:
        await SettingsService.set(session, SETTING_KEY, json.dumps(data, ensure_ascii=False))

    @staticmethod
    async def overrides() -> dict:
        return await ButtonCustomizationService._load()

    @staticmethod
    async def set_field(session, key: str, field: str, value) -> None:
        data = await ButtonCustomizationService._load()
        entry = data.get(key, {})
        if value is None:
            entry.pop(field, None)
        else:
            entry[field] = value
        if entry:
            data[key] = entry
        else:
            data.pop(key, None)
        await ButtonCustomizationService._save(session, data)

    @staticmethod
    async def reset(session, key: str) -> None:
        data = await ButtonCustomizationService._load()
        data.pop(key, None)
        await ButtonCustomizationService._save(session, data)

    @staticmethod
    def overrides_sync() -> dict:
        raw = SettingsService._cache.get(SETTING_KEY, "{}")
        try:
            data = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def resolve(key: str, default_label: str, default_style: str | None):
        """Return (label, style, custom_emoji_id) with overrides applied.

        عند ضبط إيموجي مميز نحذف الإيموجي النصي من بداية الاسم حتى لا يظهر
        مرتين (الأيقونة المميزة + الإيموجي داخل النص). الحذف يتم وقت العرض لا
        عند الحفظ، فتبقى التسميات الديناميكية (مثل رصيد الزبون) حيّة.
        """
        entry = ButtonCustomizationService.overrides_sync().get(key, {}) or {}
        label = entry.get("label") or default_label
        style = entry.get("style", default_style)
        custom_emoji_id = entry.get("custom_emoji_id")
        if custom_emoji_id:
            from keyboards.emoji_button import strip_leading_emoji

            label = strip_leading_emoji(label) or label
        return label, style, custom_emoji_id

    @staticmethod
    def apply(
        key: str,
        default_label: str,
        callback_data: str | None = None,
        default_style: str | None = None,
        url: str | None = None,
        web_app=None,
    ) -> InlineKeyboardButton:
        label, style, custom_emoji_id = ButtonCustomizationService.resolve(
            key, default_label, default_style
        )
        kwargs = {}
        if style and style not in ("gray", "default"):
            kwargs["style"] = style
        if custom_emoji_id:
            kwargs["icon_custom_emoji_id"] = custom_emoji_id
        return InlineKeyboardButton(
            text=label,
            callback_data=callback_data,
            url=url,
            web_app=web_app,
            **kwargs,
        )
