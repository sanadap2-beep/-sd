"""اختبارات إيموجي تيليجرام المميز على أزرار البوت.

تغطي ثلاث طبقات:
1) الوحدة المساعدة (``keyboards.emoji_button``): استخراج المُعرّف من الرسالة،
   حذف الإيموجي النصي من بداية الاسم، وبناء الزر.
2) أزرار الواجهة الثابتة عبر ``ButtonCustomizationService`` (القائمة الرئيسية).
3) العناصر الديناميكية (أقسام، أقسام فرعية، خدمات أرقام، سيرفرات) — الحفظ من
   لوحة الأدمن ثم ظهور ``icon_custom_emoji_id`` في لوحة المفاتيح.

ملاحظة: تيليجرام يعرض الأيقونة فعلياً فقط إذا كان مالك البوت مشتركاً بـ
Premium (أو البوت اشترى يوزر من Fragment) — الاختبارات تتحقق من أن البوت
يرسل الحقل بالشكل الصحيح.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.types import Chat, Message, MessageEntity
from aiogram.types import User as TgUser
from pydantic import ConfigDict
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import Category, CategoryType, NumberService, SubCategory, User
from keyboards.emoji_button import (
    extract_custom_emoji,
    face,
    face_or,
    icon_button,
    strip_leading_emoji,
)
from keyboards.main_menu import build_main_menu
from keyboards.numbers import number_services_kb
from keyboards.store import store_home_kb
from services.button_customization_service import ButtonCustomizationService as BC

PREMIUM_ID = "5312536098750252863"


def _premium_message(text: str = "😎", emoji_id: str = PREMIUM_ID):
    """رسالة فيها إيموجي تيليجرام المميز (كما تصل من تيليجرام)."""
    from aiogram.types import Chat, Message, MessageEntity
    from aiogram.types import User as TgUser

    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=1, type="private"),
        from_user=TgUser(id=1, is_bot=False, first_name="admin"),
        text=text,
        entities=[
            MessageEntity(type="custom_emoji", offset=0, length=len(text), custom_emoji_id=emoji_id)
        ],
    )


# ══════════════════ الوحدة المساعدة ══════════════════


def test_extract_custom_emoji_from_premium_message():
    assert extract_custom_emoji(_premium_message()) == PREMIUM_ID


def test_extract_custom_emoji_returns_none_for_plain_text():
    from aiogram.types import Chat, Message
    from aiogram.types import User as TgUser

    plain = Message(
        message_id=2,
        date=0,
        chat=Chat(id=1, type="private"),
        from_user=TgUser(id=1, is_bot=False, first_name="admin"),
        text="🚀",
        entities=None,
    )
    assert extract_custom_emoji(plain) is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("📞 الأرقام", "الأرقام"),
        ("⭐ 50 نجمة", "50 نجمة"),
        ("أرقام 📞", "أرقام 📞"),  # الإيموجي في الوسط لا يُلمس
        ("الرشق", "الرشق"),
        ("", ""),
        ("📞", "📞"),  # نص كله إيموجي: يُترك كما هو (تيليجرام يرفض نصاً فارغاً)
        ("🇸🇾 سوريا", "سوريا"),  # علم (حرفان إقليميان)
        ("👨‍👩‍👧 عائلة", "عائلة"),  # تسلسل ZWJ
    ],
)
def test_strip_leading_emoji(raw, expected):
    assert strip_leading_emoji(raw) == expected


def test_face_keeps_plain_behaviour_without_premium_id():
    text, kwargs = face("الأرقام", "📞", None)
    assert text == "📞 الأرقام"
    assert kwargs == {}


def test_face_drops_text_emoji_when_premium_icon_exists():
    text, kwargs = face("الأرقام", "📞", PREMIUM_ID)
    assert text == "الأرقام"
    assert kwargs == {"icon_custom_emoji_id": PREMIUM_ID}


def test_face_or_keeps_status_prefix():
    # الأزرار التي تبدأ بحالة (🟢/⚪): لا يجوز حذف الحالة مع الإيموجي.
    assert face_or("🟢 📦 ببجي", "🟢 ببجي", None) == ("🟢 📦 ببجي", {})
    assert face_or("🟢 📦 ببجي", "🟢 ببجي", PREMIUM_ID) == (
        "🟢 ببجي",
        {"icon_custom_emoji_id": PREMIUM_ID},
    )


def test_icon_button_builds_button_with_icon():
    btn = icon_button(
        "ببجي · ID 3",
        emoji="🎮",
        custom_emoji_id=PREMIUM_ID,
        callback_data="mb:pick:subcat:3",
    )
    assert btn.text == "ببجي · ID 3"
    assert btn.icon_custom_emoji_id == PREMIUM_ID
    assert btn.callback_data == "mb:pick:subcat:3"


# ══════════════════ أزرار الواجهة الثابتة ══════════════════


@pytest.mark.asyncio
async def test_main_menu_button_uses_premium_icon_and_drops_text_emoji():
    async with async_session_maker() as session:
        await BC.set_field(session, "main.numbers", "custom_emoji_id", PREMIUM_ID)

    kb = build_main_menu([], [], balance_usd="1.18")
    flat = {btn.callback_data: btn for row in kb.inline_keyboard for btn in row}

    button = flat["num_hub"]
    assert button.icon_custom_emoji_id == PREMIUM_ID
    assert not button.text.startswith("📞")  # لم يبقَ إيموجي نصي مكرر

    # بقية الأزرار untouched
    assert flat["menu:deposit"].icon_custom_emoji_id in (None, "")


# ══════════════════ العناصر الديناميكية ══════════════════


@pytest.mark.asyncio
async def test_store_categories_render_premium_icon():
    async with async_session_maker() as session:
        plain_cat = Category(name_ar="رشق", emoji="🚀", type=CategoryType.SMM)
        premium_cat = Category(
            name_ar="ألعاب",
            emoji="🎮",
            type=CategoryType.GAMES,
            custom_emoji_id=PREMIUM_ID,
        )
        session.add_all([plain_cat, premium_cat])
        await session.commit()
        cats = (await session.execute(select(Category))).scalars().all()

    kb = store_home_kb(number_services=[], categories=cats)
    by_data = {btn.callback_data: btn for row in kb.inline_keyboard for btn in row}

    plain_btn = next(btn for key, btn in by_data.items() if key == f"cat:{plain_cat.id}")
    premium_btn = next(btn for key, btn in by_data.items() if key == f"cat:{premium_cat.id}")

    assert plain_btn.text == "🚀 رشق"
    assert plain_btn.icon_custom_emoji_id is None
    assert premium_btn.text == "ألعاب"
    assert premium_btn.icon_custom_emoji_id == PREMIUM_ID


@pytest.mark.asyncio
async def test_number_services_render_premium_icon():
    async with async_session_maker() as session:
        svc = NumberService(
            code="wa_premium_test",
            name_ar="واتساب",
            emoji="💬",
            custom_emoji_id=PREMIUM_ID,
        )
        session.add(svc)
        await session.commit()
        services = (await session.execute(select(NumberService))).scalars().all()

    kb = number_services_kb(services)
    flat = [btn for row in kb.inline_keyboard for btn in row]
    whatsapp = next(b for b in flat if b.callback_data == "num_svc:wa_premium_test")

    assert whatsapp.text == "أرقام واتساب"
    assert whatsapp.icon_custom_emoji_id == PREMIUM_ID


@pytest.mark.asyncio
async def test_subcategory_premium_icon_reaches_games_keyboard():
    from keyboards.games import sub_categories_kb

    async with async_session_maker() as session:
        cat = Category(name_ar="ألعاب", emoji="🎮", type=CategoryType.GAMES)
        session.add(cat)
        await session.flush()
        sub = SubCategory(
            category_id=cat.id,
            name_ar="ببجي",
            emoji="🔫",
            custom_emoji_id=PREMIUM_ID,
        )
        session.add(sub)
        await session.commit()
        cat_id, sub_id = cat.id, sub.id

    async with async_session_maker() as session:
        subs = (
            await session.execute(select(SubCategory).where(SubCategory.category_id == cat_id))
        ).scalars().all()
        kb = sub_categories_kb(cat_id, subs)

    flat = [btn for row in kb.inline_keyboard for btn in row]
    pubg = next(b for b in flat if b.callback_data == f"subcat:{sub_id}")
    assert pubg.text == "ببجي"
    assert pubg.icon_custom_emoji_id == PREMIUM_ID


@pytest.mark.asyncio
async def test_admin_can_set_premium_emoji_while_creating_category():
    """إرسال إيموجي مميز أثناء إنشاء قسم يحفظ مُعرّفه بدل الاكتفاء بالنص."""
    from handlers.admin.categories import cat_custom_emoji_received
    from states.states import AdminCategoryStates

    class FakeState:
        def __init__(self):
            self.data = {"name": "اشتراكات", "category_type": "subscriptions"}
            self.cleared = False

        async def get_data(self):
            return self.data

        async def update_data(self, **kw):
            self.data.update(kw)

        async def clear(self):
            self.cleared = True

        async def set_state(self, state):
            self.state = state

    answers: list[str] = []

    class FakeMessage(_PremiumMessage):
        async def answer(self, text: str, **kwargs):
            answers.append(text)
            return None

    message = FakeMessage(
        message_id=1,
        date=0,
        chat=Chat(id=1, type="private"),
        from_user=TgUser(id=1, is_bot=False, first_name="admin"),
        text="\U0001f60e",
        entities=[
            MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id=PREMIUM_ID)
        ],
    )

    async with async_session_maker() as session:
        await cat_custom_emoji_received(message, FakeState(), session, SimpleNamespace(id=1))

    async with async_session_maker() as session:
        created = (
            await session.execute(select(Category).where(Category.name_ar == "اشتراكات"))
        ).scalar_one_or_none()

    assert created is not None
    assert created.custom_emoji_id == PREMIUM_ID


# ══════════════════ مساعدات ══════════════════


class _PremiumMessage(Message):
    """نسخة قابلة للتوريث من ``Message`` لتجاوز ``answer`` في الاختبارات."""

    model_config = ConfigDict(frozen=False)
