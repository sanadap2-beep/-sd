"""Premium custom emoji + /start menu button."""

from aiogram.types import MessageEntity

from services.premium_emoji import (
    extract_custom_emoji_id,
    is_valid_custom_id,
    render,
    utf16_len,
)
from keyboards.emoji_button import face, icon_button
from services.rich_text import html_to_entities, plain_fallback, prem_slot


def _slice(text: str, off: int, length: int) -> str:
    u16 = text.encode("utf-16-le")
    return u16[off * 2:(off + length) * 2].decode("utf-16-le")


def test_utf16_counts_surrogate_pairs():
    assert utf16_len("🔥") == 2
    assert utf16_len("a") == 1
    assert utf16_len("فري فاير") == 8


def test_custom_id_validation():
    assert is_valid_custom_id("5368322652652933093")
    assert not is_valid_custom_id("abc")
    assert not is_valid_custom_id("")
    assert not is_valid_custom_id(None)


def test_render_offsets_are_utf16():
    text, ents = render(
        ("e", "5368322652652933093", "🔥"),
        ("t", " "),
        ("b", "فري فاير"),
    )
    assert text == "🔥 فري فاير"
    by_type = {e.type: e for e in ents}
    assert _slice(text, by_type["custom_emoji"].offset, 2) == "🔥"
    assert _slice(text, by_type["bold"].offset, by_type["bold"].length) == "فري فاير"


def test_render_invalid_id_falls_back_to_char():
    text, ents = render(("e", "nope", "🎮"), ("t", "x"))
    assert text == "🎮x"
    assert all(e.type != "custom_emoji" for e in ents)


def test_extract_from_entities():
    entities = [
        MessageEntity(type="bold", offset=0, length=4),
        MessageEntity(type="custom_emoji", offset=5, length=2, custom_emoji_id="1234567890123456789"),
    ]

    class FakeMessage:
        pass

    msg = FakeMessage()
    msg.entities = entities
    assert extract_custom_emoji_id(msg) == "1234567890123456789"
    msg.entities = [MessageEntity(type="bold", offset=0, length=4)]
    assert extract_custom_emoji_id(msg) is None


def test_button_helpers():
    text, icon = face("🔥 فري فاير", None, "12345")
    assert text == "فري فاير"
    assert icon == {"icon_custom_emoji_id": "12345"}
    text, icon = face("فري فاير", "🔥", None)
    assert text == "🔥 فري فاير" and icon == {}
    btn = icon_button("فري فاير", emoji="🔥", custom_emoji_id="12345", callback_data="x")
    assert btn.text == "فري فاير"
    assert btn.icon_custom_emoji_id == "12345"


def test_rich_converter_with_premium():
    html = f"{prem_slot()} <b>شدات <i>سريعة</i></b> <code>10$</code>"
    text, ents = html_to_entities(html, [("5368322652652933093", "⚡")])
    kinds = {e.type for e in ents}
    assert {"custom_emoji", "bold", "italic", "code"} <= kinds
    for e in ents:
        # كل كيان يجب أن يقتطع نصاً صحيحاً دون تجاوز
        _slice(text, e.offset, e.length)


def test_rich_fallback_never_crashes():
    broken = "<b>ناقص <i>تداخل</b> زائد</i> \ue000"
    plain = plain_fallback(broken, [("1", "✨")])
    assert "\ue000" not in plain
    assert "ناقص" in plain


async def test_category_stores_custom_emoji_id(fresh_database):
    from database.engine import async_session_maker
    from database.models import Category, CategoryType

    async with async_session_maker() as session:
        cat = Category(
            name_ar="ألعاب",
            emoji="🎮",
            custom_emoji_id="5368322652652933093",
            type=CategoryType.GAMES,
        )
        session.add(cat)
        await session.commit()
        await session.refresh(cat)
        assert cat.custom_emoji_id == "5368322652652933093"


async def test_menu_sync_uses_start_only(monkeypatch):
    from services import bot_menu_service

    calls = []

    class FakeScope:
        pass

    class FakeBot:
        async def set_my_commands(self, cmds, scope=None, language_code=None):
            calls.append(([c.command for c in cmds], language_code))

        async def set_chat_menu_button(self, menu_button=None):
            calls.append(("menu",))

    assert await bot_menu_service.sync_bot_menu(FakeBot()) is True
    cmd_calls = [c for c in calls if isinstance(c, tuple) and len(c) == 2]
    assert cmd_calls
    for cmds, _lang in cmd_calls:
        assert cmds == ["start"], cmds
    assert ("menu",) in calls
