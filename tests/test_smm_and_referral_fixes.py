"""Regression tests for رشق app names, TikTok text crash, and referral links."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    SubCategory,
    User,
)
from database.seed import init_db
from keyboards.games import sub_categories_kb
from middlewares.user_middleware import extract_referrer_telegram_id
from services.bot_identity import (
    normalize_bot_username,
    referral_share_url,
    referral_start_link,
    reset_bot_username_cache,
    resolve_bot_username,
)
from services.dynamic_service import DynamicService
from services.smm_catalog import (
    SMM_APPS,
    button_label,
    is_smm_app_label,
    resolve_smm_app,
)


CANONICAL_APPS = [
    ("تيك توك", "🎵"),
    ("إنستغرام", "📸"),
    ("يوتيوب", "▶️"),
    ("تيليجرام", "✈️"),
    ("فيسبوك", "👍"),
    ("واتساب", "💬"),
    ("سناب شات", "👻"),
    ("تويتر", "🐦"),
    ("ثريدز", "🧵"),
    ("سبوتيفاي", "🎧"),
]


def test_canonical_smm_apps_have_arabic_names_and_emojis():
    assert SMM_APPS == CANONICAL_APPS
    assert all(name and " " not in emoji for name, emoji in SMM_APPS)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("تيك توك 🎵", "تيك توك"),
        ("🎵 تيك توك", "تيك توك"),
        ("tiktok", "تيك توك"),
        ("إكس (تويتر)", "تويتر"),
        ("Facebook", "فيسبوك"),
        ("TikTok Followers", "تيك توك"),
        ("متابعين تيك توك", "تيك توك"),
    ],
)
def test_resolve_smm_app_aliases(text, expected):
    app = resolve_smm_app(text)
    assert app is not None
    assert app.name_ar == expected


def test_smm_app_label_is_exact_not_a_sentence():
    assert is_smm_app_label("تيك توك 🎵") is True
    assert is_smm_app_label("🎵 تيك توك") is True
    assert is_smm_app_label("tiktok") is True
    assert is_smm_app_label("بدي 1000 متابع تيك توك") is False
    assert is_smm_app_label("مرحبا") is False


def test_button_label_does_not_duplicate_emoji():
    assert button_label("تيك توك", "🎵") == "🎵 تيك توك"
    assert button_label("تيك توك 🎵", "🎵") == "تيك توك 🎵"


def test_normalize_bot_username_strips_at_and_tme():
    assert normalize_bot_username("@MyShop_bot") == "MyShop_bot"
    assert normalize_bot_username("https://t.me/MyShop_bot") == "MyShop_bot"
    assert normalize_bot_username("https://t.me/@MyShop_bot?start=ref_1") == "MyShop_bot"
    assert normalize_bot_username("t.me/MyShop_bot/") == "MyShop_bot"
    assert normalize_bot_username("") == ""
    assert normalize_bot_username("ab") == ""
    assert normalize_bot_username("not a username") == ""


def test_referral_start_link_never_embeds_at_or_url():
    assert referral_start_link("@MyShop_bot", 6707747395) == (
        "https://t.me/MyShop_bot?start=ref_6707747395"
    )
    assert referral_start_link("https://t.me/MyShop_bot", 1) == (
        "https://t.me/MyShop_bot?start=ref_1"
    )
    assert referral_start_link("", 1) == ""
    assert referral_start_link("@x", 1) == ""


def test_referral_share_url_points_at_telegram_share():
    link = "https://t.me/MyShop_bot?start=ref_1"
    url = referral_share_url(link, "hello")
    assert url.startswith("https://t.me/share/url?url=")
    assert "MyShop_bot" in url


@pytest.mark.asyncio
async def test_resolve_bot_username_prefers_get_me(monkeypatch):
    reset_bot_username_cache()

    class Me:
        username = "LiveShop_bot"

    class Bot:
        async def get_me(self):
            return Me()

    monkeypatch.setattr(
        "services.bot_identity.settings",
        SimpleNamespace(BOT_USERNAME="@WrongBot"),
    )
    assert await resolve_bot_username(Bot()) == "LiveShop_bot"
    reset_bot_username_cache()


@pytest.mark.asyncio
async def test_resolve_bot_username_falls_back_to_env(monkeypatch):
    reset_bot_username_cache()

    class Bot:
        async def get_me(self):
            raise RuntimeError("offline")

    monkeypatch.setattr(
        "services.bot_identity.settings",
        SimpleNamespace(BOT_USERNAME="https://t.me/@EnvShop_bot"),
    )
    assert await resolve_bot_username(Bot()) == "EnvShop_bot"
    reset_bot_username_cache()


@pytest.mark.parametrize(
    "text, expected",
    [
        ("/start ref_6707747395", 6707747395),
        ("/start@MyShop_bot ref_42", 42),
        ("/start ref_7 extra", 7),
        ("/start", None),
        ("/start buy_tg__tr", None),
        (None, None),
    ],
)
def test_extract_referrer_telegram_id(text, expected):
    assert extract_referrer_telegram_id(text) == expected


@pytest.mark.asyncio
async def test_seeded_smm_apps_use_canonical_names_and_emojis():
    async with async_session_maker() as session:
        category = (
            await session.execute(select(Category).where(Category.type == CategoryType.SMM))
        ).scalars().first()
        assert category is not None
        subs = list(
            (
                await session.execute(
                    select(SubCategory)
                    .where(SubCategory.category_id == category.id)
                    .order_by(SubCategory.sort_order, SubCategory.id)
                )
            ).scalars().all()
        )
        pairs = [(sub.name_ar, sub.emoji) for sub in subs]
        for name, emoji in CANONICAL_APPS:
            assert (name, emoji) in pairs

        keyboard = sub_categories_kb(category.id, subs)
        labels = [btn.text for row in keyboard.inline_keyboard for btn in row]
        assert "🎵 تيك توك" in labels
        assert "👍 فيسبوك" in labels
        assert "🐦 تويتر" in labels
        assert not any("إكس (تويتر)" in text for text in labels)
        assert not any("📘" in text for text in labels)


@pytest.mark.asyncio
async def test_seed_renames_legacy_smm_rows():
    async with async_session_maker() as session:
        category = (
            await session.execute(select(Category).where(Category.type == CategoryType.SMM))
        ).scalars().first()
        twitter = (
            await session.execute(
                select(SubCategory).where(
                    SubCategory.category_id == category.id,
                    SubCategory.name_ar == "تويتر",
                )
            )
        ).scalar_one()
        twitter.name_ar = "إكس (تويتر)"
        twitter.emoji = "📱"
        tiktok = (
            await session.execute(
                select(SubCategory).where(
                    SubCategory.category_id == category.id,
                    SubCategory.name_ar == "تيك توك",
                )
            )
        ).scalar_one()
        tiktok.name_ar = "تيك توك 🎵"
        await session.commit()

    await init_db()

    async with async_session_maker() as session:
        names = dict(
            (
                await session.execute(
                    select(SubCategory.name_ar, SubCategory.emoji).join(Category).where(
                        Category.type == CategoryType.SMM
                    )
                )
            ).all()
        )
        assert names["تويتر"] == "🐦"
        assert names["تيك توك"] == "🎵"
        assert "إكس (تويتر)" not in names
        assert "تيك توك 🎵" not in names


@pytest.mark.asyncio
async def test_find_subcategory_by_tiktok_label_eager_loads_relations():
    async with async_session_maker() as session:
        category = (
            await session.execute(select(Category).where(Category.type == CategoryType.SMM))
        ).scalars().first()
        tiktok = (
            await session.execute(
                select(SubCategory).where(
                    SubCategory.category_id == category.id,
                    SubCategory.name_ar == "تيك توك",
                )
            )
        ).scalar_one()
        session.add(
            Product(
                sub_category_id=tiktok.id,
                name_ar="1000 متابع",
                price_usd=1,
                status=ProductStatus.ACTIVE,
                fulfillment_type=ProductFulfillmentType.API,
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        sub = await DynamicService.find_subcategory_by_label(session, "تيك توك 🎵")
        assert sub is not None
        assert sub.name_ar == "تيك توك"
        # These accesses used to raise MissingGreenlet on AsyncSession.
        assert sub.category.type == CategoryType.SMM
        assert len(sub.products) == 1


@pytest.mark.asyncio
async def test_catalog_label_handler_answers_tiktok_without_lazy_io():
    from handlers.games import catalog_label_selected

    class FakeMessage:
        def __init__(self, text: str):
            self.text = text
            self.answers: list[dict] = []

        async def answer(self, text, reply_markup=None, **kwargs):
            self.answers.append({"text": text, "markup": reply_markup})

    async with async_session_maker() as session:
        message = FakeMessage("تيك توك 🎵")
        await catalog_label_selected(message, session, db_user=None)
        assert message.answers
        assert "تيك توك" in message.answers[0]["text"]


@pytest.mark.asyncio
async def test_provider_import_maps_english_tiktok_to_canonical_subcategory():
    from handlers.admin.api_providers import _ensure_service_subcategory

    async with async_session_maker() as session:
        category = (
            await session.execute(select(Category).where(Category.type == CategoryType.SMM))
        ).scalars().first()
        before = (
            await session.execute(
                select(func.count(SubCategory.id)).where(SubCategory.category_id == category.id)
            )
        ).scalar_one()
        service = SimpleNamespace(
            category="TikTok Followers",
            service_type=None,
            name="1000 TikTok Followers",
        )
        sub = await _ensure_service_subcategory(session, category, service)
        after = (
            await session.execute(
                select(func.count(SubCategory.id)).where(SubCategory.category_id == category.id)
            )
        ).scalar_one()
        assert sub.name_ar == "تيك توك"
        assert sub.emoji == "🎵"
        assert after == before


@pytest.mark.asyncio
async def test_referral_card_uses_live_username_and_clickable_link(monkeypatch):
    from handlers.referral import _send_referral_info
    from services import i18n_service

    i18n_service.I18nService._load.cache_clear()
    reset_bot_username_cache()

    class Me:
        username = "LiveShop_bot"

    class Bot:
        async def get_me(self):
            return Me()

    class FakeMessage:
        def __init__(self):
            self.bot = Bot()
            self.answers: list[dict] = []

        async def answer(self, text, reply_markup=None, **kwargs):
            self.answers.append({"text": text, "markup": reply_markup, "kwargs": kwargs})

    async with async_session_maker() as session:
        user = User(telegram_id=6707747395, full_name="Referrer", language_code="ar")
        session.add(user)
        await session.commit()
        await session.refresh(user)

        message = FakeMessage()
        await _send_referral_info(message, session, user)
        assert message.answers
        body = message.answers[0]["text"]
        assert "https://t.me/LiveShop_bot?start=ref_6707747395" in body
        assert "<code>" not in body
        markup = message.answers[0]["markup"]
        share = markup.inline_keyboard[0][0]
        assert share.url.startswith("https://t.me/share/url?")
        assert "LiveShop_bot" in share.url

    reset_bot_username_cache()
    i18n_service.I18nService._load.cache_clear()
