"""قسم واتساب + جسر البوتين: العقد، الاشتراكات، التجديد، ولوحة الأدمن.

الهدف أن يعمل «الربط» بلا مفاجآت عند التشغيل:
- عميل الجسر يفسّر ردود البوت الثاني (بما فيها الردود الغنية والملفات).
- جسر ``wa_bridge`` يرفض الطلبات بلا سرّ صحيح ويطبّع القوائم.
- فشل الجسر لا يخصم رصيداً ولا يترك المستخدم عالقاً في «بانتظار الربط».
- التجديد التلقائي وتنبيه الانتهاء يعملان، والاسترداد لا يتكرر.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    Transaction,
    TransactionType,
    User,
    WaLinkState,
)
from services import wa_bridge_client
from services.balance_service import BalanceService
from services.feature_service import FeatureService
from services.whatsapp_section_service import WhatsAppSectionService


async def _make_user(session, balance: str = "20.00", telegram_id: int = 777) -> User:
    user = User(telegram_id=telegram_id, balance=Decimal(balance), full_name="مختبر")
    session.add(user)
    await session.flush()
    return user


async def _bridge_configured(url: str = "http://bridge.test:8090", secret: str = "s" * 32):
    async with async_session_maker() as session:
        from database.models import Setting

        for key, value in (("wa_bridge_url", url), ("wa_bridge_secret", secret)):
            row = await session.get(Setting, key)
            if row is None:
                session.add(Setting(key=key, value=value))
            else:
                row.value = value
        await session.commit()
    from services.settings_service import SettingsService

    SettingsService._cache.update({"wa_bridge_url": url, "wa_bridge_secret": secret})


# ══════════════════ عميل الجسر: العقد ══════════════════


@pytest.fixture(autouse=True)
def _bridge_env(monkeypatch):
    """لا طلبات شبكة في الاختبارات: نستبدل _request بردود مُجهّزة."""
    monkeypatch.setattr(wa_bridge_client, "consecutive_failures_count", 0)
    wa_bridge_client.reset_failure_counter()


async def test_client_start_link_requires_link_code(monkeypatch):
    captured = {}

    async def fake_request(method, path, tg_user_id, json_body=None, params=None, timeout=45):
        captured.update(method=method, path=path, body=json_body, tg=tg_user_id)
        return {"link_code": "234567", "instructions": "ضعه عند المزود"}

    monkeypatch.setattr(wa_bridge_client, "_request", fake_request)
    result = await wa_bridge_client.start_link(42, "+966512345678")
    assert result == {"link_code": "234567", "instructions": "ضعه عند المزود"}
    assert captured["method"] == "POST" and captured["path"] == "/link/start"
    assert captured["body"] == {"phone": "+966512345678"}
    assert captured["tg"] == 42


async def test_client_start_link_without_code_raises(monkeypatch):
    async def fake_request(*args, **kwargs):
        return {"instructions": "لا كود"}

    monkeypatch.setattr(wa_bridge_client, "_request", fake_request)
    with pytest.raises(wa_bridge_client.WaBridgeError, match="كود ربط"):
        await wa_bridge_client.start_link(1, "+966500000000")


async def test_client_rejects_unknown_link_state(monkeypatch):
    async def fake_request(*args, **kwargs):
        return {"state": "weird"}

    monkeypatch.setattr(wa_bridge_client, "_request", fake_request)
    with pytest.raises(wa_bridge_client.WaBridgeError, match="غير معروفة"):
        await wa_bridge_client.link_status(1)


async def test_client_link_status_accepts_none_state(monkeypatch):
    """البوت الثاني قد يقول «لا جلسة» — يجب أن تُفهم وتُنهي حالة PENDING."""

    async def fake_request(*args, **kwargs):
        return {"state": "none"}

    monkeypatch.setattr(wa_bridge_client, "_request", fake_request)
    assert (await wa_bridge_client.link_status(1))["state"] == "none"


async def test_client_menu_filters_invalid_items_and_pages(monkeypatch):
    seen = {}

    async def fake_request(method, path, tg_user_id, json_body=None, params=None, timeout=45):
        seen["params"] = params
        return {
            "status_text": "🟢 متصل",
            "menu": [
                {"id": "a", "label": "أمر"},
                {"label": "بلا id"},  # يبقى: label يكفي لعرضه
                "نص-غير-صالح",  # يُستبعد
                {"id": "c"},
            ],
            "page": 2,
            "pages": 5,
        }

    monkeypatch.setattr(wa_bridge_client, "_request", fake_request)
    data = await wa_bridge_client.menu(9, page=2)
    assert seen["params"] == {"page": 2}
    assert [i.get("id") for i in data["menu"]] == ["a", None, "c"]
    assert (data["page"], data["pages"]) == (2, 5)


async def test_client_unlink_tolerates_old_bridge(monkeypatch):
    async def fake_request(method, path, tg_user_id, json_body=None, params=None, timeout=45):
        raise wa_bridge_client.WaBridgeError("الجسر: لا مسار", status=404)

    monkeypatch.setattr(wa_bridge_client, "_request", fake_request)
    assert await wa_bridge_client.unlink(1) is False

    async def ok(method, path, tg_user_id, json_body=None, params=None, timeout=45):
        return {"ok": True}

    monkeypatch.setattr(wa_bridge_client, "_request", ok)
    assert await wa_bridge_client.unlink(1) is True


async def test_client_unlink_propagates_real_errors(monkeypatch):
    async def boom(method, path, tg_user_id, json_body=None, params=None, timeout=45):
        raise wa_bridge_client.WaBridgeError("الجسر لا يرد", status=500)

    monkeypatch.setattr(wa_bridge_client, "_request", boom)
    with pytest.raises(wa_bridge_client.WaBridgeError):
        await wa_bridge_client.unlink(1)


def test_parse_result_supports_rich_and_legacy_shapes():
    rich = wa_bridge_client.parse_result(
        {
            "text": "تم",
            "files": [
                {"name": "a/../qr.png", "url": "https://x/y.png"},
                {"name": "log.txt", "content_b64": base64.b64encode(b"hi").decode()},
                {"name": "بلا مصدر"},
            ],
            "buttons": [{"text": "فتح", "url": "https://ok"}, {"text": "x", "url": "http://no"}],
            "menu": [{"id": "m", "label": "رجوع"}],
            "page": 1,
            "pages": 3,
            "awaiting_input": {"prompt": "اكتب", "action": "send"},
        }
    )
    assert rich["text"] == "تم"
    assert rich["files"][0]["url"].startswith("https://")
    assert base64.b64decode(rich["files"][1]["content_b64"]) == b"hi"
    assert len(rich["files"]) == 2, "الملف بلا مصدر يُستبعد"
    assert rich["buttons"] == [{"text": "فتح", "url": "https://ok"}], "http غير مقبول"
    assert rich["menu"] == [{"id": "m", "label": "رجوع"}]
    assert rich["awaiting_input"]["action"] == "send"

    legacy = wa_bridge_client.parse_result({"text": "قديمة"})
    assert legacy["files"] == [] and legacy["menu"] is None
    assert legacy["pages"] == 1 and legacy["awaiting_input"] is None


async def test_request_surfaces_bridge_error_detail(monkeypatch):
    """رسالة الجسر (detail) تصل للمستخدم بدل «كود 400» غامض."""
    await _bridge_configured()

    class FakeResponse:
        status = 400
        _body = json.dumps({"detail": "الرقم مرفوض عند المزود"}).encode()

        async def text(self):
            return self._body.decode()

        async def json(self):
            return json.loads(self._body)

    class FakeCtx:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def request(self, method, url, **kwargs):
            return FakeCtx()

    monkeypatch.setattr(wa_bridge_client.aiohttp, "ClientSession", FakeSession)
    with pytest.raises(wa_bridge_client.WaBridgeError, match="الرقم مرفوض عند المزود"):
        await wa_bridge_client.link_status(1)
    assert wa_bridge_client.consecutive_failures() == 1


async def test_unconfigured_bridge_raises_before_network(monkeypatch):
    from services.settings_service import SettingsService

    SettingsService._cache.update({"wa_bridge_url": "", "wa_bridge_secret": ""})
    monkeypatch.setattr(wa_bridge_client.settings, "WA_BRIDGE_URL", "", raising=False)
    monkeypatch.setattr(wa_bridge_client.settings, "WA_BRIDGE_SECRET", "", raising=False)
    with pytest.raises(wa_bridge_client.WaBridgeError) as exc:
        await wa_bridge_client.menu(1)
    assert exc.value.not_configured is True
    with pytest.raises(wa_bridge_client.WaBridgeError):
        await wa_bridge_client.ping()


# ══════════════════ الاشتراكات والباقات ══════════════════


async def test_packages_are_validated_and_sorted():
    async with async_session_maker() as session:
        await FeatureService.set_option(
            session,
            "whatsapp_section",
            "packages_json",
            [
                {"days": 7, "price_usd": "6.3"},
                {"days": "1", "price_usd": 1},
                {"days": 0, "price_usd": 5},  # مرفوض
                {"price_usd": 3},  # مرفوض
                "خطأ",  # مرفوض
            ],
        )
        packages = await WhatsAppSectionService.packages()
    assert [p["days"] for p in packages] == [1, 7]
    assert packages[1]["price_usd"] == Decimal("6.3")


async def test_purchase_deducts_and_stacks_on_existing_expiry(monkeypatch):
    async with async_session_maker() as session:
        user = await _make_user(session, "20.00")
        sub = await WhatsAppSectionService.purchase(session, user, 1, Decimal("1.0"))
        assert sub.active_until > datetime.utcnow()
        await session.refresh(user)
        assert user.balance == Decimal("19.00")
        tx = (
            await session.execute(
                select(Transaction).where(Transaction.type == TransactionType.WA_SUBSCRIPTION)
            )
        ).scalars().first()
        assert tx is not None and Decimal(str(tx.amount)) < 0

        # باقة ثانية تتمدد من نهاية الساري (تراكُم) لا من الآن.
        first_end = sub.active_until
        sub = await WhatsAppSectionService.purchase(session, user, 3, Decimal("2.85"))
        assert sub.active_until > first_end + timedelta(days=2)


async def test_purchase_without_balance_does_not_change_anything(monkeypatch):
    async with async_session_maker() as session:
        user = await _make_user(session, "0.50")
        from services.balance_service import InsufficientBalanceError

        with pytest.raises(InsufficientBalanceError):
            await WhatsAppSectionService.purchase(session, user, 1, Decimal("1.0"))
        sub = await WhatsAppSectionService.get_sub(session, user.id)
        assert sub is None or sub.active_until is None


async def test_new_subscription_respects_auto_renew_default_option():
    """`auto_renew_default` كان إعداداً ميتاً — يجب أن يحكم بداية الاشتراك."""
    async with async_session_maker() as session:
        await FeatureService.set_option(
            session, "whatsapp_section", "auto_renew_default", False
        )
        await FeatureService.reload()
        user = await _make_user(session, "5.00", telegram_id=888)
        sub = await WhatsAppSectionService._get_or_create(session, user)
        assert sub.auto_renew is False
        # إعادة الضبط لاختبارات تالية
        await FeatureService.set_option(session, "whatsapp_section", "auto_renew_default", True)
        await FeatureService.reload()


async def test_start_link_rolls_back_state_when_bridge_fails(monkeypatch):
    async def boom(tg_user_id, phone):
        raise wa_bridge_client.WaBridgeError("الجسر لا يرد")

    monkeypatch.setattr(wa_bridge_client, "start_link", boom)
    async with async_session_maker() as session:
        user = await _make_user(session)
        with pytest.raises(wa_bridge_client.WaBridgeError):
            await WhatsAppSectionService.start_link(session, user, "+966500000001")
        sub = await WhatsAppSectionService.get_sub(session, user.id)
        assert sub.link_state == WaLinkState.NONE.value, "لم يُترك عالقاً في PENDING"
        assert sub.link_error and "لا يرد" in sub.link_error
        assert sub.phone is None


async def test_start_link_stores_pending_and_code(monkeypatch):
    async def ok(tg_user_id, phone):
        return {"link_code": "999", "instructions": "ضعه"}

    monkeypatch.setattr(wa_bridge_client, "start_link", ok)
    async with async_session_maker() as session:
        user = await _make_user(session, telegram_id=999)
        result = await WhatsAppSectionService.start_link(session, user, "+966500000002")
        sub = await WhatsAppSectionService.get_sub(session, user.id)
        assert result["link_code"] == "999"
        assert sub.link_state == WaLinkState.PENDING.value
        assert sub.phone == "+966500000002"


async def test_check_link_marks_linked_and_clears_error(monkeypatch):
    async def linked(tg_user_id):
        return {"state": "linked", "connected_since": "2026-09-14 10:00"}

    monkeypatch.setattr(wa_bridge_client, "link_status", linked)
    async with async_session_maker() as session:
        user = await _make_user(session, telegram_id=1001)
        sub = await WhatsAppSectionService._get_or_create(session, user)
        sub.link_state = WaLinkState.PENDING.value
        sub.link_error = "سابق"
        await session.commit()
        sub = await WhatsAppSectionService.check_link(session, user)
        assert sub.link_state == WaLinkState.LINKED.value
        assert sub.link_error is None
        assert sub.connected_since == "2026-09-14 10:00"


# ══════════════════ دورة التجديد والإنهاء ══════════════════


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return type("M", (), {"message_id": 1})()


async def test_renewal_cycle_renews_expiring_subscription():
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "whatsapp_section", True)
        user = await _make_user(session, "5.00", telegram_id=1100)
        sub = await WhatsAppSectionService.purchase(session, user, 1, Decimal("1.0"))
        sub.active_until = datetime.utcnow() + timedelta(hours=3)  # داخل مهلة التذكير
        sub.auto_renew = True
        await session.commit()
        before = sub.active_until
        bot = _FakeBot()

        stats = await WhatsAppSectionService.renewal_cycle(session, bot=bot)
        await session.refresh(sub)
        await session.refresh(user)

        assert stats["renewed"] == 1
        assert sub.active_until > before + timedelta(hours=23)
        assert user.balance == Decimal("3.00"), "1$ للشراء + 1$ للتجديد"
        assert bot.sent, "يجب أن يصل المستخدم إشعار التجديد"
        assert sub.last_renewed_at is not None


async def test_renewal_cycle_notifies_expiry_once_and_skips_no_balance():
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "whatsapp_section", True)
        user = await _make_user(session, "0.00", telegram_id=1200)
        sub = await WhatsAppSectionService._get_or_create(session, user)
        sub.active_until = datetime.utcnow() - timedelta(hours=1)
        sub.auto_renew = True
        await session.commit()
        bot = _FakeBot()
        stats = await WhatsAppSectionService.renewal_cycle(session, bot=bot)
        assert stats["expired_notified"] == 1
        stats2 = await WhatsAppSectionService.renewal_cycle(session, bot=bot)
        assert stats2["expired_notified"] == 0, "التنبيه لا يتكرر"

    async with async_session_maker() as session:
        poor = await _make_user(session, "0.20", telegram_id=1300)
        sub = await WhatsAppSectionService._get_or_create(session, poor)
        sub.active_until = datetime.utcnow() + timedelta(hours=1)
        sub.auto_renew = True
        await session.commit()
        stats = await WhatsAppSectionService.renewal_cycle(session, bot=_FakeBot())
        assert stats["skipped_balance"] >= 1


async def test_renewal_cycle_disabled_feature_is_noop(monkeypatch):
    async def fake_enabled(cls, key, default=None):
        return False

    monkeypatch.setattr(FeatureService, "enabled", classmethod(fake_enabled))
    async with async_session_maker() as session:
        stats = await WhatsAppSectionService.renewal_cycle(session, bot=_FakeBot())
    assert stats == {"renewed": 0, "expired_notified": 0, "skipped_balance": 0}


# ══════════════════ عمليات الأدمن ══════════════════


async def test_admin_extend_is_free_and_stacks():
    async with async_session_maker() as session:
        user = await _make_user(session, "5.00", telegram_id=1400)
        sub = await WhatsAppSectionService.purchase(session, user, 1, Decimal("1.0"))
        await session.refresh(user)
        after_purchase = user.balance
        before = sub.active_until
        sub = await WhatsAppSectionService.extend(session, sub, 7)
        await session.refresh(user)
        assert (sub.active_until - before).days == 7
        assert user.balance == after_purchase, "التمديد المجاني لا يخصم"
        assert sub.expire_notified_at is None


async def test_admin_refund_once_and_skips_renewals():
    async with async_session_maker() as session:
        user = await _make_user(session, "10.00", telegram_id=1500)
        sub = await WhatsAppSectionService.purchase(session, user, 1, Decimal("1.0"))
        # عملية تجديد يدوية في السجل — يجب ألا تُسترد (الاسترداد لآخر *شراء*).
        await BalanceService.deduct_balance(
            session,
            user.id,
            Decimal("1.0"),
            TransactionType.WA_SUBSCRIPTION,
            description="تجديد اشتراك واتساب (يوم)",
            related_table="wa_subscriptions",
            related_id=sub.id,
        )
        await session.refresh(user)
        assert user.balance == Decimal("8.00")
        tx = await WhatsAppSectionService.last_purchase_transaction(session, user.id)
        assert tx is not None and "اشتراك واتساب 1 يوم" in (tx.description or "")

        amount = await WhatsAppSectionService.refund_purchase(session, user, sub)
        assert amount == Decimal("1.0")
        await session.refresh(user)
        assert user.balance == Decimal("9.00")
        # مرة ثانية: لا استرداد مزدوج لنفس العملية.
        assert await WhatsAppSectionService.refund_purchase(session, user, sub) is None
        await session.refresh(user)
        assert user.balance == Decimal("9.00")


async def test_admin_unlink_pushes_to_bridge_and_clears_state(monkeypatch):
    calls = []

    async def ok_unlink(tg_user_id):
        calls.append(tg_user_id)
        return True

    monkeypatch.setattr(wa_bridge_client, "unlink", ok_unlink)
    async with async_session_maker() as session:
        user = await _make_user(session, "5.00", telegram_id=1600)
        sub = await WhatsAppSectionService._get_or_create(session, user)
        sub.link_state = WaLinkState.LINKED.value
        sub.phone = "+966500000009"
        await session.commit()
        pushed = await WhatsAppSectionService.unlink(session, sub, user)
        assert pushed is True and calls == [1600]
        await session.refresh(sub)
        assert sub.link_state == WaLinkState.NONE.value
        assert sub.link_error is None


async def test_bridge_health_alerts_only_after_threshold(monkeypatch):
    from services.whatsapp_section_service import WhatsAppSectionService as W

    W._bridge_down_alerted = False
    alerts: list[str] = []

    async def fake_notify(bot, text, dedupe_key=None):
        alerts.append(text)

    monkeypatch.setattr(W, "_notify_admins", staticmethod(fake_notify))
    async def fake_configured():
        return True

    monkeypatch.setattr(wa_bridge_client, "configured", fake_configured)

    probe_results = [
        {"ok": False, "error": "لا يرد", "latency_ms": 5},
        {"ok": True, "latency_ms": 3, "version": "2.0", "capabilities": ["menu"]},
    ]
    counters = [5, 0]

    async def fake_probe():
        return probe_results.pop(0)

    monkeypatch.setattr(wa_bridge_client, "probe", fake_probe)
    monkeypatch.setattr(wa_bridge_client, "consecutive_failures", lambda: counters.pop(0))

    first = await W.bridge_health(bot=None)
    assert first["ok"] is False and first["alerted"] is True, "عتبة الإخفاقات تجاوزها"
    second = await W.bridge_health(bot=None)
    assert second["ok"] is True and second["recovered"] is True
    assert len(alerts) == 2
    W._bridge_down_alerted = False


# ══════════════════ خدمة الجسر نفسها (wa_bridge) ══════════════════


def _bridge_client(adapter=None, secret: str = "k" * 32):
    from fastapi.testclient import TestClient

    from wa_bridge.adapter import StaticJsonAdapter
    from wa_bridge.bridge import create_app

    app = create_app(adapter or StaticJsonAdapter(), secret=secret)
    return TestClient(app), secret


def test_bridge_requires_secret_and_user_id():
    client, secret = _bridge_client()
    assert client.get("/ping").status_code == 401
    assert client.get("/ping", headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = {"Authorization": f"Bearer {secret}"}
    assert client.get("/ping", headers=ok).status_code == 200
    assert client.get("/menu", headers=ok).status_code == 400, "X-TG-User-Id مطلوب"
    assert client.get("/menu", headers={**ok, "X-TG-User-Id": "abc"}).status_code == 400


def test_bridge_menu_shape_and_sanitising():
    from wa_bridge.adapter import BridgeAdapter

    class Messy(BridgeAdapter):
        async def menu(self, tg_id, page=0):
            items = [
                {"id": "ok", "label": "ز" * 200},
                {"label": "بلا id"},
                {"id": "http", "label": "رابط غير آمن", "url": "http://evil"},
                {"id": "safe", "label": "رابط", "url": "https://fine"},
                {"id": "typed", "label": "زر", "data": {"k": 1}, "kind": "input", "prompt": "اكتب"},
            ] + [{"id": f"p{i}", "label": f"بند {i}"} for i in range(20)]
            page_items, page_no, pages = self.paginate(items, page)
            return {"status_text": "حالة", "menu": page_items, "page": page_no, "pages": pages}

    client, secret = _bridge_client(Messy())
    data = client.get(
        "/menu", headers={"Authorization": f"Bearer {secret}", "X-TG-User-Id": "5"}
    ).json()
    assert data["status_text"] == "حالة" and data["pages"] == 3, "25 بنداً / 12 لكل صفحة"
    ids = [i["id"] for i in data["menu"]]
    assert "ok" in ids and "typed" in ids
    assert all("id" in i for i in data["menu"]), "العنصر بلا id يُستبعد"
    typed = next(i for i in data["menu"] if i["id"] == "typed")
    assert typed["kind"] == "input" and typed["prompt"] == "اكتب"
    assert "safe" in ids
    http = next(i for i in data["menu"] if i["id"] == "http")
    assert http["kind"] == "action" and "url" not in http, "http مرفوض"
    assert all(len(i["label"]) <= 48 for i in data["menu"])
    # رقم صفحة خارج المدى يُقصّ لآخر صفحة (لا قائمة فارغة ولا خطأ)
    clamped = client.get(
        "/menu?page=9", headers={"Authorization": f"Bearer {secret}", "X-TG-User-Id": "5"}
    ).json()
    assert clamped["page"] == 2 and clamped["menu"], "تُقصّ لآخر صفحة"


def test_bridge_action_files_and_input_flow():
    from wa_bridge.adapter import BridgeAdapter

    class Rich(BridgeAdapter):
        async def action(self, tg_id, action_id, payload=None):
            if action_id == "ask":
                return {"text": "اكتب الرقم", "awaiting_input": {"prompt": "الرقم؟", "action": "ask"}}
            return {
                "text": "ملفك",
                "files": [{"name": "a b.txt", "content_b64": base64.b64encode(b"x").decode()}],
                "buttons": [{"text": "لوحة", "url": "https://x"}],
                "menu": [{"id": "back", "label": "رجوع"}],
            }

        async def input(self, tg_id, text, payload=None):
            return {"text": f"وصل: {text}"}

    headers = {"Authorization": f"Bearer {'k' * 32}", "X-TG-User-Id": "9"}
    client, _ = _bridge_client(Rich(), secret="k" * 32)
    out = client.post("/action", headers=headers, json={"action": "ask"}).json()
    assert out["awaiting_input"] == {"prompt": "الرقم؟", "action": "ask"}
    out = client.post("/action", headers=headers, json={"action": "go"}).json()
    assert out["files"][0]["name"] == "a b.txt", "المسافة تبقى آمنة"
    assert out["buttons"] == [{"text": "لوحة", "url": "https://x"}]
    traversal = client.post(
        "/action",
        headers=headers,
        json={"action": "file_name_traversal"},
    )
    assert traversal.status_code == 200
    assert out["menu"] == [{"id": "back", "label": "رجوع", "kind": "action"}]
    assert client.post("/input", headers=headers, json={"text": "050"}).json()["text"] == "وصل: 050"
    assert client.post("/input", headers=headers, json={"text": "  "}).status_code == 400
    assert client.post("/action", headers=headers, json={}).status_code == 400


def test_bridge_rejects_bad_phone_and_reports_adapter_error():
    from wa_bridge.adapter import BridgeAdapter, BridgeError

    class Strict(BridgeAdapter):
        async def start_link(self, tg_id, phone):
            raise BridgeError("الرقم مرفوض عند المزود")

    client, secret = _bridge_client(Strict(), secret="k" * 32)
    headers = {"Authorization": f"Bearer {secret}", "X-TG-User-Id": "1"}
    assert client.post("/link/start", headers=headers, json={"phone": "abc"}).status_code == 400
    response = client.post("/link/start", headers=headers, json={"phone": "+966500000000"})
    assert response.status_code == 400
    assert "مرفوض" in response.json()["detail"]


def test_bridge_link_status_unknown_state_becomes_none():
    from wa_bridge.adapter import BridgeAdapter

    class Weird(BridgeAdapter):
        async def link_status(self, tg_id):
            return {"state": "banana"}

        async def menu(self, tg_id, page=0):
            raise RuntimeError("انفجار داخلي")

    client, secret = _bridge_client(Weird(), secret="k" * 32)
    headers = {"Authorization": f"Bearer {secret}", "X-TG-User-Id": "1"}
    assert client.get("/link/status", headers=headers).json()["state"] == "none"
    assert client.get("/menu", headers=headers).status_code == 500, "لا تُسرّب التفاصيل الداخلية"


def test_bridge_refuses_to_run_without_secret():
    import os

    from wa_bridge.bridge import create_app

    saved = os.environ.pop("BRIDGE_SECRET", None)
    try:
        with pytest.raises(RuntimeError, match="BRIDGE_SECRET"):
            create_app()
    finally:
        if saved is not None:
            os.environ["BRIDGE_SECRET"] = saved


def test_static_json_adapter_serves_example_menu():
    from pathlib import Path

    from wa_bridge.adapter import StaticJsonAdapter

    path = Path(__file__).resolve().parents[1] / "wa_bridge" / "menu.example.json"
    adapter = StaticJsonAdapter(path=str(path))
    client, secret = _bridge_client(adapter, secret="k" * 32)
    headers = {"Authorization": f"Bearer {secret}", "X-TG-User-Id": "42"}
    data = client.get("/menu", headers=headers).json()
    assert any(i["kind"] == "input" for i in data["menu"]), "مثال القائمة يغطي الأزرار الكتابية"
    assert any(i["kind"] == "url" for i in data["menu"])
    result = client.post(
        "/action", headers=headers, json={"action": "docs", "payload": {}}
    ).json()
    assert result["files"], "زر الملفات التجريبي يرجع ملفاً فعلاً"
    assert base64.b64decode(result["files"][0]["content_b64"]).decode().startswith("ملف تجريبي")


# ══════════════════ واجهة القسم: كاش القوائم والأزرار ══════════════════


def test_menu_store_ttl_owner_check_and_prune():
    """الكاش: TTL + تحقق الملكية + قصّ الحجم (مسؤول عن بقاء الأزرار حية)."""
    import asyncio
    import time

    from handlers.whatsapp import (
        _MENU_STATES,
        _MenuState,
        _get_menu,
        _prune_menu_states,
        _save_menu,
    )

    _MENU_STATES.clear()
    try:
        state = _MenuState(tg_id=111, items=[{"id": "a", "label": "أ"}])
        token = asyncio.run(_save_menu(state, ttl_minutes=1))
        assert _get_menu(token, 111) is state
        assert _get_menu(token, 222) is None, "قائمة مستخدم آخر لا تُقرأ"
        assert _get_menu("deadbeef", 111) is None

        state.expires_at = 0.001
        assert _get_menu(token, 111) is None, "منتهية ⇒ يُعاد الجلب بدل زر ميت"

        for index in range(5):
            _MENU_STATES[f"tok{index:03d}"] = _MenuState(
                tg_id=1, items=[], expires_at=time.monotonic() + 60
            )
        _prune_menu_states(max_items=2)
        assert len(_MENU_STATES) <= 2
    finally:
        _MENU_STATES.clear()


def test_menu_keyboard_renders_kinds_and_paging():
    from handlers.whatsapp import _menu_kb

    items = [
        {"id": "a", "label": "عادي"},
        {"id": "b", "label": "اكتب", "kind": "input", "prompt": "؟"},
        {"id": "c", "label": "رابط", "kind": "url", "url": "https://x"},
        {"id": "d", "label": "معطل", "disabled": True},
    ]
    kb = _menu_kb("dead1111", items, "ar", page=1, pages=3)
    rows = kb.inline_keyboard
    flat = [btn for row in rows for btn in row]
    assert flat[0].callback_data == "wa:act:dead1111:0"
    assert flat[1].text.startswith("✍️"), "زر الإدخال يُميَّز بصرياً"
    assert flat[2].url == "https://x" and flat[2].callback_data is None
    assert flat[3].text.startswith("🚫") and flat[3].callback_data == "wa:noop"
    nav = [btn.callback_data for btn in rows[-3]]
    assert any(data and data.startswith("wa:page:dead1111:0") for data in nav), "زر السابق"
    assert any(data and data.endswith("wa:page:dead1111:2") for data in nav), "زر التالي"
    assert rows[-2][0].callback_data == "wa:refresh"
    assert rows[-1][0].callback_data == "wa:home"


def test_menu_kb_caps_at_max_items():
    from handlers.whatsapp import _MAX_MENU_ITEMS, _menu_kb

    items = [{"id": f"i{i}", "label": f"بند {i}"} for i in range(_MAX_MENU_ITEMS + 25)]
    kb = _menu_kb("abcd1234", items, "ar")
    action_rows = [r for r in kb.inline_keyboard if r[0].callback_data and r[0].callback_data.startswith("wa:act")]
    assert len(action_rows) == _MAX_MENU_ITEMS


def test_result_kb_falls_back_to_menu_entry_when_no_new_menu():
    from handlers.whatsapp import _result_kb

    kb = _result_kb("tok12345", None, {"text": "تم", "buttons": [{"text": "لوحة", "url": "https://a"}]}, "ar")
    first_row = kb.inline_keyboard[0]
    assert first_row[0].url == "https://a"
    assert any(
        btn.callback_data == "wa:menu" for row in kb.inline_keyboard for btn in row
    )


async def test_send_result_files_handles_url_base64_and_failures(monkeypatch):
    from handlers.whatsapp import _send_result_files

    sent = []

    class FakeMessage:
        async def answer_document(self, document, **kwargs):
            if isinstance(document, str):
                sent.append(("url", document))
            else:
                sent.append(("bytes", document.filename))
            return object()

    class FakeCallback:
        message = FakeMessage()

    result = {
        "files": [
            {"name": "photo.png", "url": "https://cdn/x.png"},
            {"name": "log.txt", "content_b64": base64.b64encode(b"data").decode()},
            {"name": "broken.txt", "content_b64": "%%%ليس-base64%%%"},
            {"name": "empty.txt"},
        ]
    }
    await _send_result_files(FakeCallback(), result)
    assert sent == [("url", "https://cdn/x.png"), ("bytes", "log.txt")], "الملف الفاسد لا يُسقط الرد"
