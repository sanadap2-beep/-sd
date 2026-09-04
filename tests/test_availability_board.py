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
        await FeatureService.set_option(
            session,
            "numbers_availability_board",
            "watched_country_codes",
            ",".join(f"cc{i}" for i in range(15)),
        )

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return _entries(15)

    import services.number_catalog_service as ncs

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()

    # top_n الافتراضي 12 → نأخذ أول 12
    text, rows = await AvailabilityBoardService.build_rows()
    assert text is not None
    assert len(rows) == 12
    # الرابط العميق يفتح البوت بطلب شراء مباشر: buy_<service>__<country>
    label, url = rows[0]
    assert url.endswith("buy_whatsapp__cc0")
    assert "t.me/" in url
    assert "🌍 دولة 0 — " in label
    assert label.endswith("$")
    # الترتيب من الأرخص: أول دولة هي الأرخص
    assert rows[0][1].endswith("cc0")


async def test_build_rows_respects_top_n_and_active(monkeypatch):
    await FeatureService.reload()
    import services.number_catalog_service as ncs

    async with async_session_maker() as session:
        await _seed_service(session)
        await FeatureService.set_option(session, "numbers_availability_board", "top_n", 5)
        await FeatureService.set_option(
            session,
            "numbers_availability_board",
            "watched_country_codes",
            ",".join(f"cc{i}" for i in range(20)),
        )

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return _entries(20)

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()
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
    """:بين دورتين لإعادة النشر تُعدَّل اللوحة في مكانها بدل إزعاج القناة.

    (الافتراضي الجديد = إعادة نشر كل دورة، لذا نضبط هنا 5 دورات لنختبر
    مسار «التعديل في المكان» نفسه.)
    """
    await FeatureService.reload()
    import services.number_catalog_service as ncs

    async with async_session_maker() as session:
        await _seed_service(session)
        await FeatureService.set_enabled(session, "numbers_availability_board", True)
        await FeatureService.set_option(session, "numbers_availability_board", "channel_chat_id", "-1001")
        await FeatureService.set_option(session, "numbers_availability_board", "top_n", 5)
        await FeatureService.set_option(session, "numbers_availability_board", "repost_every_cycles", 5)
        await FeatureService.set_option(
            session,
            "numbers_availability_board",
            "watched_country_codes",
            "cc0,cc1,cc2,cc3,cc4,cc5",
        )

    calls = {"count": 0}

    async def fake_build_board(session, service, manager=None, use_cache=True):
        calls["count"] += 1
        if calls["count"] == 1:
            return _entries(5)
        return _entries(6)

    monkeypatch.setattr(ncs, "build_board", fake_build_board)

    # عزل الحالة العامة للمون (آخر منشور/صورة توفر) بين الاختبارات
    AvailabilityBoardService.reset_state()

    class FakeMessage:
        def __init__(self, message_id):
            self.message_id = message_id

    class FakeBot:
        def __init__(self):
            self.sent = []
            self.deleted = []
            self.edited = []
            self._next = 100

        async def send_message(self, chat_id, text, reply_markup=None):
            self._next += 1
            msg = FakeMessage(self._next)
            self.sent.append((chat_id, text, reply_markup, msg.message_id))
            return msg

        async def delete_message(self, chat_id, message_id):
            self.deleted.append((chat_id, message_id))

        async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
            self.edited.append(((chat_id, message_id), reply_markup, text))

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

    # دورة ثانية: تظهر دولة جديدة (cc5) فتُعدَّل نفس الرسالة بدل إزعاج المشتركين.
    result2 = await AvailabilityBoardService.post_board(bot)
    assert "حُدِّثت" in result2
    assert len(bot.sent) == 1
    assert bot.deleted == []
    assert bot.edited and bot.edited[0][0] == (-1001, msg_id)
    # الدولة الجديدة 🔥 في المقدمة، وكل الدول الست معروضة + زر دخول البوت.
    edited_markup = bot.edited[0][1]
    assert len(edited_markup.inline_keyboard) == 6
    assert edited_markup.inline_keyboard[0][0].text.startswith("🔥")


async def test_build_rows_prefers_newly_restocked_watched_country(monkeypatch):
    await FeatureService.reload()
    import services.number_catalog_service as ncs

    async with async_session_maker() as session:
        await _seed_service(session)
        await FeatureService.set_option(session, "numbers_availability_board", "watched_country_codes", "ae,sa,us")
        await FeatureService.set_option(session, "numbers_availability_board", "top_n", 5)

    first_entries = [
        BoardEntry("id", "إندونيسيا", "🇮🇩", Decimal("0.1"), Decimal("0.2")),
        BoardEntry("ke", "كينيا", "🇰🇪", Decimal("0.1"), Decimal("0.2")),
    ]
    second_entries = [
        *first_entries,
        BoardEntry("ae", "الإمارات", "🇦🇪", Decimal("3"), Decimal("4.5"), True),
    ]
    calls = {"count": 0}

    async def fake_build_board(session, service, manager=None, use_cache=True):
        calls["count"] += 1
        return first_entries if calls["count"] == 1 else second_entries

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()

    # أول دورة: تُنشر الدول المتاحة (اللوحة حيّة دائماً) بلا وسم 🔥 كاذب.
    _text, first_rows = await AvailabilityBoardService.build_rows()
    assert len(first_rows) == 2
    assert not any(label.startswith("🔥") for label, _ in first_rows)

    # الدورة الثانية: الإمارات ظهرت بعد أن كانت غائبة → 🔥 في المقدمة.
    _text2, rows = await AvailabilityBoardService.build_rows()
    assert len(rows) == 3
    assert rows[0][0].startswith("🔥 🇦🇪 الإمارات")
    assert "عادت الآن" in rows[0][0]
    assert rows[0][1].endswith("buy_whatsapp__ae")


async def test_build_rows_uses_live_bot_username_over_env(monkeypatch):
    await FeatureService.reload()
    import services.number_catalog_service as ncs
    import services.bot_identity as bot_identity

    async with async_session_maker() as session:
        await _seed_service(session)
        await FeatureService.set_option(session, "numbers_availability_board", "watched_country_codes", "ae")

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return [BoardEntry("ae", "الإمارات", "🇦🇪", Decimal("3"), Decimal("4.5"), True)]

    class Me:
        username = "LiveBot_bot"

    class Bot:
        async def get_me(self):
            return Me()

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    monkeypatch.setattr(bot_identity, "settings", type("S", (), {"BOT_USERNAME": "@WrongBot"})())
    bot_identity.reset_bot_username_cache()
    AvailabilityBoardService.reset_state()

    _text, rows = await AvailabilityBoardService.build_rows(bot=Bot())
    assert "https://t.me/LiveBot_bot?start=buy_whatsapp__ae" == rows[0][1]


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


# ══════════════ التطويرات الجديدة على «التوفر المتقطع» ══════════════


async def _configure(**options):
    await FeatureService.reload()
    async with async_session_maker() as session:
        await _seed_service(session)
        for key, value in options.items():
            await FeatureService.set_option(session, "numbers_availability_board", key, value)


async def test_board_is_live_every_cycle_even_without_restock(monkeypatch):
    """اللوحة لم تعد تتجمّد: كل دورة تُرجع محتوى قابلاً للنشر."""
    import services.number_catalog_service as ncs

    await _configure(top_n=6, watched_country_codes="ae", rotate_stable=True)

    entries = [
        BoardEntry(f"st{i}", f"ثابتة {i}", "🌍", Decimal("0.1"), Decimal(str(0.2 + i)))
        for i in range(4)
    ]

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return entries

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()

    for _ in range(3):
        built = await AvailabilityBoardService.build_rows()
        assert built is not None
        _text, rows = built
        assert len(rows) == 4


async def test_stable_countries_rotate_between_cycles(monkeypatch):
    """ترتيب الدول الثابتة يتغيّر كل دورة فتبدو القناة حيّة."""
    import services.number_catalog_service as ncs

    await _configure(top_n=10, watched_country_codes="ae", rotate_stable=True)

    entries = [
        BoardEntry(f"st{i}", f"ثابتة {i}", "🌍", Decimal("0.1"), Decimal(str(0.2 + i)))
        for i in range(5)
    ]

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return entries

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()

    _t1, rows1 = await AvailabilityBoardService.build_rows()
    _t2, rows2 = await AvailabilityBoardService.build_rows()
    assert [u for _, u in rows1] != [u for _, u in rows2]      # الترتيب تغيّر
    assert sorted(u for _, u in rows1) == sorted(u for _, u in rows2)  # نفس الدول


async def test_rotation_can_be_disabled(monkeypatch):
    import services.number_catalog_service as ncs

    await _configure(top_n=10, watched_country_codes="ae", rotate_stable=False)
    entries = [
        BoardEntry(f"st{i}", f"ثابتة {i}", "🌍", Decimal("0.1"), Decimal(str(0.2 + i)))
        for i in range(5)
    ]

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return entries

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()

    _t1, rows1 = await AvailabilityBoardService.build_rows()
    _t2, rows2 = await AvailabilityBoardService.build_rows()
    assert [u for _, u in rows1] == [u for _, u in rows2]


async def test_watched_countries_rotate_like_the_rest(monkeypatch):
    """الدول النادرة (المراقبة) لم تعد مثبّتة بالترتيب نفسه كل دورة.

    كان هذا هو جوهر شكوى «نفس أول ١٠ دول»: القائمة المراقبة تغطي الدول
    الكبرى، فكانت تملأ أول الخانات دائماً بالترتيب نفسه ولا يؤثر التدوير
    عليها أبداً.
    """
    import services.number_catalog_service as ncs

    await _configure(top_n=10, watched_country_codes="ae,sa,us,gb,qa,kw,bh,om,jo,eg", rotate_stable=True)

    codes = ["ae", "sa", "us", "gb", "qa", "kw", "bh", "om", "jo", "eg"]
    entries = [
        BoardEntry(code, f"دولة {code}", "🌍", Decimal("0.1"), Decimal(str(0.2 + i)), True)
        for i, code in enumerate(codes)
    ]

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return list(entries)

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()

    _t1, rows1 = await AvailabilityBoardService.build_rows()
    _t2, rows2 = await AvailabilityBoardService.build_rows()
    urls1 = [url for _, url in rows1]
    urls2 = [url for _, url in rows2]
    assert urls1 != urls2, "ترتيب الدول المراقبة يتجمّد على ما يبدو"
    assert sorted(urls1) == sorted(urls2)  # نفس الدول
    assert len(urls1) == 10


async def test_visible_window_changes_when_more_than_top_n(monkeypatch):
    """عندما يتجاوز المتاح عدد الدول المعروضة، تتبدل دول النافذة كل دورة."""
    import services.number_catalog_service as ncs

    await _configure(top_n=5, watched_country_codes="ae,sa", rotate_stable=True)

    entries = [
        BoardEntry(f"cc{i}", f"دولة {i}", "🌍", Decimal("0.1"), Decimal(str(0.2 + i)))
        for i in range(12)
    ]

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return list(entries)

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()

    _t1, rows1 = await AvailabilityBoardService.build_rows()
    _t2, rows2 = await AvailabilityBoardService.build_rows()
    set1 = {url.rsplit("__", 1)[1] for _, url in rows1}
    set2 = {url.rsplit("__", 1)[1] for _, url in rows2}
    assert set1 != set2, "دول النافذة المعروضة لا تتبدل بين الدورات"
    assert len(set1) == 5 and len(set2) == 5


async def test_restock_badge_survives_restart(monkeypatch):
    """الحالة محفوظة في قاعدة البيانات: إعادة التشغيل لا تفقد تاريخ التوفر."""
    import services.number_catalog_service as ncs

    await _configure(top_n=10, watched_country_codes="ae")

    stable = [BoardEntry("ke", "كينيا", "🇰🇪", Decimal("0.1"), Decimal("0.2"))]
    with_ae = [*stable, BoardEntry("ae", "الإمارات", "🇦🇪", Decimal("3"), Decimal("4.5"), True)]
    calls = {"n": 0}

    async def fake_build_board(session, service, manager=None, use_cache=True):
        calls["n"] += 1
        return stable if calls["n"] == 1 else with_ae

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()

    await AvailabilityBoardService.build_rows()  # يحفظ الصورة الأولى (بدون ae)

    # محاكاة إعادة تشغيل البوت: تُمسح كل الحالة في الذاكرة فقط.
    AvailabilityBoardService.reset_state()

    _text, rows = await AvailabilityBoardService.build_rows()
    hot = [label for label, _ in rows if label.startswith("🔥")]
    assert any("الإمارات" in label for label in hot), rows


async def test_inactive_country_never_gets_a_button(monkeypatch):
    """لا زر لدولة معطّلة — هذا ما كان يسبب «لا يوجد رقم» بعد الضغط."""
    import services.number_catalog_service as ncs
    from sqlalchemy import select

    await _configure(top_n=10, watched_country_codes="ae")

    async with async_session_maker() as session:
        result = await session.execute(select(Country).where(Country.code == "zz_dead"))
        dead = result.scalar_one_or_none()
        if dead is None:
            dead = Country(code="zz_dead", name_ar="دولة ميتة", flag="🏴", is_active=False)
            session.add(dead)
        dead.is_active = False
        await session.commit()

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return [
            BoardEntry("zz_dead", "دولة ميتة", "🏴", Decimal("0.1"), Decimal("0.2")),
            BoardEntry("ae", "الإمارات", "🇦🇪", Decimal("3"), Decimal("4.5"), True),
        ]

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()

    _text, rows = await AvailabilityBoardService.build_rows()
    assert all("zz_dead" not in url for _, url in rows)
    assert any(url.endswith("buy_whatsapp__ae") for _, url in rows)


async def test_all_links_use_live_username_including_enter_button(monkeypatch):
    """زر «دخول البوت» كان يستعمل قيمة البيئة الخاطئة → «اسم المستخدم غير موجود»."""
    import services.bot_identity as bot_identity
    import services.number_catalog_service as ncs

    await _configure(channel_chat_id="-1002", top_n=5, watched_country_codes="ae")
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "numbers_availability_board", True)

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return [BoardEntry("ae", "الإمارات", "🇦🇪", Decimal("3"), Decimal("4.5"), True)]

    class Me:
        username = "RealLive_bot"

    class Bot:
        def __init__(self):
            self.sent = []

        async def get_me(self):
            return Me()

        async def send_message(self, chat_id, text, reply_markup=None):
            self.sent.append((chat_id, text, reply_markup))
            return type("M", (), {"message_id": 7})()

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    monkeypatch.setattr(bot_identity, "settings", type("S", (), {"BOT_USERNAME": "@Wrong"})())
    bot_identity.reset_bot_username_cache()
    AvailabilityBoardService.reset_state()

    bot = Bot()
    assert "نُشرت" in await AvailabilityBoardService.post_board(bot)
    markup = bot.sent[0][2]
    urls = [btn.url for row in markup.inline_keyboard for btn in row]
    assert urls, markup
    assert all("RealLive_bot" in url for url in urls), urls
    assert urls[-1] == "https://t.me/RealLive_bot"


async def test_board_not_published_without_username(monkeypatch):
    import services.bot_identity as bot_identity
    import services.number_catalog_service as ncs

    await _configure(channel_chat_id="-1003", watched_country_codes="ae")
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "numbers_availability_board", True)

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return [BoardEntry("ae", "الإمارات", "🇦🇪", Decimal("3"), Decimal("4.5"), True)]

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    monkeypatch.setattr(bot_identity, "settings", type("S", (), {"BOT_USERNAME": ""})())
    bot_identity.reset_bot_username_cache()
    AvailabilityBoardService.reset_state()

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, *a, **k):
            self.sent.append(1)

    bot = Bot()
    result = await AvailabilityBoardService.post_board(bot)
    assert "يوزرنيم" in result
    assert bot.sent == []


# ══════════════ «القناة تُسمع بنفسها»: إعادة النشر الدورية ══════════════


def _fake_bot():
    class FakeMessage:
        def __init__(self, message_id):
            self.message_id = message_id

    class FakeBot:
        def __init__(self):
            self.sent = []
            self.deleted = []
            self.edited = []
            self._next = 100

        async def send_message(self, chat_id, text, reply_markup=None):
            self._next += 1
            self.sent.append((chat_id, text, reply_markup, self._next))
            return FakeMessage(self._next)

        async def delete_message(self, chat_id, message_id):
            self.deleted.append((chat_id, message_id))

        async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
            self.edited.append(((chat_id, message_id), reply_markup, text))

    return FakeBot()


def _static_board(entries):
    import services.number_catalog_service as ncs_module

    async def fake_build_board(session, service, manager=None, use_cache=True):
        return list(entries)

    return ncs_module, fake_build_board


async def test_auto_repost_publishes_fresh_message_every_n_cycles(monkeypatch):
    """اللوحة تُعاد كرسالة جديدة كل N دورة: تبقى بأعلى القناة + إشعار."""
    import services.number_catalog_service as ncs

    await _configure(
        channel_chat_id="-1009",
        watched_country_codes="cc0",
        top_n=5,
        repost_every_cycles=3,
        auto_repost=True,
    )
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "numbers_availability_board", True)

    entries = [BoardEntry("cc0", "دولة 0", "🌍", Decimal("0.1"), Decimal("0.2"))]
    monkeypatch.setattr(ncs, "build_board", _static_board(entries)[1])
    AvailabilityBoardService.reset_state()

    bot = _fake_bot()
    for _ in range(7):
        await AvailabilityBoardService.post_board(bot)

    # نشر عند الدورة 1، ثم إعادة نشر عند 4 و7 (كل 3 دورات).
    assert len(bot.sent) == 3
    assert len(bot.deleted) == 2  # حذف القديم قبل كل إعادة نشر
    assert len(bot.edited) == 4  # الدورات البينية تُعدَّل في مكانها


async def test_auto_repost_disabled_keeps_editing_in_place(monkeypatch):
    import services.number_catalog_service as ncs

    await _configure(
        channel_chat_id="-1010",
        watched_country_codes="cc0",
        top_n=5,
        repost_every_cycles=2,
        auto_repost=False,
    )
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "numbers_availability_board", True)

    entries = [BoardEntry("cc0", "دولة 0", "🌍", Decimal("0.1"), Decimal("0.2"))]
    monkeypatch.setattr(ncs, "build_board", _static_board(entries)[1])
    AvailabilityBoardService.reset_state()

    bot = _fake_bot()
    for _ in range(6):
        await AvailabilityBoardService.post_board(bot)

    assert len(bot.sent) == 1  # النشر الأول فقط
    assert bot.deleted == []
    assert len(bot.edited) == 5


async def test_restock_push_sends_new_message_when_enabled(monkeypatch):
    """إشعار فوري لحظة رجوع دولة نادرة (اختياري، معطّل افتراضياً)."""
    import services.number_catalog_service as ncs

    await _configure(
        channel_chat_id="-1011",
        watched_country_codes="ae,sa",
        top_n=5,
        auto_repost=False,
        repost_on_restock=True,
    )
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "numbers_availability_board", True)

    first = [BoardEntry("id", "إندونيسيا", "🇮🇩", Decimal("0.1"), Decimal("0.2"))]
    second = first + [
        BoardEntry("ae", "الإمارات", "🇦🇪", Decimal("3"), Decimal("4.5"), True)
    ]
    calls = {"count": 0}

    async def fake_build_board(session, service, manager=None, use_cache=True):
        calls["count"] += 1
        return list(first if calls["count"] == 1 else second)

    monkeypatch.setattr(ncs, "build_board", fake_build_board)
    AvailabilityBoardService.reset_state()

    bot = _fake_bot()
    await AvailabilityBoardService.post_board(bot)
    assert len(bot.sent) == 1

    # الإمارات رجعت للمخزون → رسالة جديدة (إشعار) بدل تعديل صامت.
    await AvailabilityBoardService.post_board(bot)
    assert len(bot.sent) == 2
    assert len(bot.deleted) == 1
    assert bot.edited == []


async def test_board_is_reposted_every_single_cycle_by_default(monkeypatch):
    """:الافتراضي: كل دورة = حذف القديمة + رسالة جديدة (إشعار كل دقيقة)."""
    import services.number_catalog_service as ncs

    await _configure(channel_chat_id="-1012", watched_country_codes="cc0", top_n=5)
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "numbers_availability_board", True)

    entries = [BoardEntry("cc0", "دولة 0", "🌍", Decimal("0.1"), Decimal("0.2"))]
    monkeypatch.setattr(ncs, "build_board", _static_board(entries)[1])
    AvailabilityBoardService.reset_state()

    bot = _fake_bot()
    for _ in range(4):
        result = await AvailabilityBoardService.post_board(bot)
        assert "نُشرت" in result

    assert len(bot.sent) == 4  # رسالة جديدة كل دورة
    assert len(bot.deleted) == 3  # حذف السابقة قبل كل رسالة
    assert bot.edited == []  # لا تعديل في مكان أبداً


async def test_repost_settings_defaults():
    await FeatureService.reload()
    assert await AvailabilityBoardService.auto_repost() is True
    assert await AvailabilityBoardService.repost_every_cycles() == 1
    assert await AvailabilityBoardService.repost_on_restock() is False
