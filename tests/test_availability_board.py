"""Tests for the intermittent-availability live channel board."""

from __future__ import annotations

from decimal import Decimal

from database.engine import async_session_maker
from database.models import Country, NumberService
from services.availability_board_service import AvailabilityBoardService
from services.feature_service import FeatureService
from services.number_catalog_service import BoardEntry


def _entries(n: int) -> list[BoardEntry]:
    return [
        BoardEntry(
            code=f"cc{i}",
            name_ar=f"دولة {i}",
            flag="🌍",
            cost_usd=Decimal(str(0.1 * (i + 1))),
            sell_usd=Decimal(str(0.2 * (i + 1))),
        )
        for i in range(n)
    ]


async def _seed_service(session):
    """يضمن وجود خدمة واتساب مفعلة (قد تكون مزروعة مسبقاً)."""
    from sqlalchemy import select

    result = await session.execute(select(NumberService).where(NumberService.code == "whatsapp"))
    svc = result.scalar_one_or_none()
    if svc is None:
        svc = NumberService(code="whatsapp", name_ar="واتساب", emoji="💬", is_active=True)
        session.add(svc)
    svc.is_active = True
    await session.commit()
    return svc


async def test_build_rows_returns_deeplinks(monkeypatch):
    from database.engine import async_session_maker as asm

    await FeatureService.reload()
    async with asm() as session:
        await _seed_service(session)

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return _entries(15)

    import services.number_catalog_service as ncs

    monkeypatch.setattr(ncs, "build_board", fake_build_board)

    # top_n الافتراضي 10 → نأخذ أول 10
    text, rows = await AvailabilityBoardService.build_rows()
    assert text is not None
    assert len(rows) == 10
    # الرابط العميق يفتح البوت بطلب شراء مباشر: buy_<service>__<country>
    label, url = rows[0]
    assert url.endswith("buy_whatsapp__cc0")
    assert "t.me/" in url
    assert label.startswith("🌍 دولة 0 — ")
    assert label.endswith("$")
    # الترتيب من الأرخص: أول دولة هي الأرخص
    assert rows[0][1].endswith("cc0")


async def test_build_rows_respects_top_n_and_active(monkeypatch):
    await FeatureService.reload()
    import services.number_catalog_service as ncs

    async with async_session_maker() as session:
        await _seed_service(session)
        await FeatureService.set_option(session, "numbers_availability_board", "top_n", 5)

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return _entries(20)

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    _text, rows = await AvailabilityBoardService.build_rows()
    assert len(rows) == 5


async def test_build_rows_none_when_no_stock(monkeypatch):
    await FeatureService.reload()
    import services.number_catalog_service as ncs

    async with async_session_maker() as session:
        await _seed_service(session)

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return []

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    assert await AvailabilityBoardService.build_rows() is None


async def test_post_board_publishes_and_replaces(monkeypatch):
    await FeatureService.reload()
    import services.number_catalog_service as ncs

    async with async_session_maker() as session:
        await _seed_service(session)
        await FeatureService.set_enabled(session, "numbers_availability_board", True)
        await FeatureService.set_option(session, "numbers_availability_board", "channel_chat_id", "-1001")
        await FeatureService.set_option(session, "numbers_availability_board", "top_n", 5)

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return _entries(5)

    monkeypatch.setattr(ncs, "build_board", fake_build_board)

    # عزل الحالة العامة للمون (آخر منشور) بين الاختبارات
    AvailabilityBoardService._last_post = None

    class FakeMessage:
        def __init__(self, message_id):
            self.message_id = message_id

    class FakeBot:
        def __init__(self):
            self.sent = []
            self.deleted = []
            self._next = 100

        async def send_message(self, chat_id, text, reply_markup=None):
            self._next += 1
            msg = FakeMessage(self._next)
            self.sent.append((chat_id, text, reply_markup, msg.message_id))
            return msg

        async def delete_message(self, chat_id, message_id):
            self.deleted.append((chat_id, message_id))

    bot = FakeBot()
    result = await AvailabilityBoardService.post_board(bot)
    assert "نُشرت" in result
    assert len(bot.sent) == 1
    chat_id, text, markup, msg_id = bot.sent[0]
    assert chat_id == -1001
    # 5 دول + زر دخول البوت = 6 صفوف
    assert len(markup.inline_keyboard) == 6
    # كل زر دولة رابط خارجي (URL) لا callback
    first_btn = markup.inline_keyboard[0][0]
    assert first_btn.url and first_btn.callback_data is None

    # دورة ثانية: يجب أن تحذف الرسالة السابقة وتنشر جديدة
    result2 = await AvailabilityBoardService.post_board(bot)
    assert "نُشرت" in result2
    assert len(bot.sent) == 2
    assert bot.deleted == [(-1001, msg_id)]


async def test_post_board_disabled_or_unset():
    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, *a, **k):
            self.sent.append(1)

    await FeatureService.reload()
    bot = FakeBot()
    # الميزة معطلة
    assert "معطلة" in await AvailabilityBoardService.post_board(bot)

    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "numbers_availability_board", True)
    # القناة غير مضبوطة
    assert "لم تُضبط" in await AvailabilityBoardService.post_board(bot)
    assert bot.sent == []


def test_deeplink_parses_back_to_service_and_country():
    """نفس منطق /start: buy_<service>__<country>."""
    payload = "buy_whatsapp__tr"
    assert payload.startswith("buy_")
    raw = payload.replace("buy_", "", 1)
    assert "__" in raw
    service_code, country_code = raw.split("__", 1)
    assert service_code == "whatsapp"
    assert country_code == "tr"
