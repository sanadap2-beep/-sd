# -*- coding: utf-8 -*-
"""اختبارات خدمة جلب كود الدخول عبر الجلسة (Telethon) — بعميل وهمي بلا شبكة."""

from __future__ import annotations

import asyncio
import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import services.tg_ready_service as tgrs
from config import settings
from services import session_login_service as slls


# ══════════════ عميل وهمي ══════════════


class _FakeMsg(SimpleNamespace):
    pass


class _FakeEvent(SimpleNamespace):
    pass


class _FakeClient:
    def __init__(self, *, authorized=True, history=None, raise_on_history=None):
        self.authorized = authorized
        self.history = list(history or [])
        self.raise_on_history = raise_on_history
        self.handler = None
        self.disconnected = False
        self.connected = False
        self.tmp_path: str | None = None

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return self.authorized

    async def get_messages(self, entity, limit=10):
        if self.raise_on_history is not None:
            raise self.raise_on_history
        return self.history[:limit]

    def add_event_handler(self, callback, event=None):
        self.handler = callback

    def remove_event_handler(self, callback, event=None):
        if self.handler is callback:
            self.handler = None

    async def fire(self, text: str, when: datetime | None = None):
        """محاكاة وصول رسالة 777000 للمستمع المسجّل."""
        assert self.handler is not None, "no listener registered"
        msg = _FakeMsg(text=text, date=when or datetime.now(timezone.utc))
        await self.handler(_FakeEvent(message=msg))


def _patch_api(monkeypatch, api_id=111222333, api_hash="0123456789abcdef0123456789abcdef"):
    monkeypatch.setattr(settings, "API_ID", api_id)
    monkeypatch.setattr(settings, "API_HASH", api_hash)


def _patch_client(monkeypatch, fake: _FakeClient, captured: dict):
    def _create(path: str):
        captured["path"] = path
        fake.tmp_path = path
        return fake

    monkeypatch.setattr(slls, "_create_client", _create)


def _fresh_msg(text: str, age_s: float = 5.0) -> _FakeMsg:
    return _FakeMsg(
        text=text,
        date=datetime.now(timezone.utc) - timedelta(seconds=age_s),
    )


# ══════════════ fetch_code_via_session ══════════════


async def test_history_hit_returns_code(monkeypatch):
    _patch_api(monkeypatch)
    fake = _FakeClient(authorized=True, history=[
        _fresh_msg("Login code: 47291. Do not give this code to anyone.", 10),
    ])
    captured: dict = {}
    _patch_client(monkeypatch, fake, captured)

    res = await slls.fetch_code_via_session(b"SESSION", wait_s=0)
    assert res["ok"] is True
    assert res["code"] == "47291"
    assert res["error"] is None
    # تنظيف: قطع الاتصال + حذف الملف المؤقت
    assert fake.disconnected is True
    assert captured["path"] and not os.path.exists(captured["path"])


async def test_stale_history_no_wait_returns_no_code_yet(monkeypatch):
    _patch_api(monkeypatch)
    fake = _FakeClient(authorized=True, history=[
        _fresh_msg("Login code: 11111", age_s=slls.SESSION_CODE_FRESH_S + 100),
    ])
    _patch_client(monkeypatch, fake, {})

    res = await slls.fetch_code_via_session(b"SESSION", wait_s=0)
    assert res["ok"] is False
    assert res["error"] == "no_code_yet"


async def test_wait_then_fire_returns_code(monkeypatch):
    _patch_api(monkeypatch)
    fake = _FakeClient(authorized=True, history=[])
    _patch_client(monkeypatch, fake, {})
    on_wait_calls: list[int] = []

    async def on_wait():
        on_wait_calls.append(1)
        # الزبون «ذهب» لشاشة الدخول — الكود يصل بعد لحظة
        async def _later():
            await asyncio.sleep(0.05)
            await fake.fire("Login code: 55213", datetime.now(timezone.utc))
        asyncio.ensure_future(_later())

    res = await slls.fetch_code_via_session(b"SESSION", wait_s=2, on_wait=on_wait)
    assert res["ok"] is True
    assert res["code"] == "55213"
    assert on_wait_calls == [1]


async def test_wait_timeout_no_code_yet_and_on_wait_once(monkeypatch):
    _patch_api(monkeypatch)
    fake = _FakeClient(authorized=True, history=[])
    captured: dict = {}
    _patch_client(monkeypatch, fake, captured)
    on_wait_calls: list[int] = []

    async def on_wait():
        on_wait_calls.append(1)

    res = await slls.fetch_code_via_session(b"SESSION", wait_s=0.15, on_wait=on_wait)
    assert res["ok"] is False
    assert res["error"] == "no_code_yet"
    assert on_wait_calls == [1]
    assert fake.disconnected is True
    assert captured["path"] and not os.path.exists(captured["path"])


async def test_not_authorized(monkeypatch):
    _patch_api(monkeypatch)
    fake = _FakeClient(authorized=False)
    _patch_client(monkeypatch, fake, {})

    res = await slls.fetch_code_via_session(b"SESSION", wait_s=0)
    assert res["ok"] is False
    assert res["error"] == "not_authorized"
    assert fake.disconnected is True


async def test_not_configured_no_client_created(monkeypatch):
    monkeypatch.setattr(settings, "API_ID", 0)
    monkeypatch.setattr(settings, "API_HASH", "")
    created: list[str] = []

    def _create(path: str):
        created.append(path)
        raise AssertionError("client must not be created when unconfigured")

    monkeypatch.setattr(slls, "_create_client", _create)

    res = await slls.fetch_code_via_session(b"SESSION", wait_s=0)
    assert res["ok"] is False
    assert res["error"] == "not_configured"
    assert created == []


async def test_flood_wait_rate_limited(monkeypatch):
    from telethon import errors as terr

    _patch_api(monkeypatch)
    fake = _FakeClient(authorized=True)
    _patch_client(monkeypatch, fake, {})

    # FloodWait يرميه تيليجرام أثناء أي عملية RPC — هنا من فحص الصلاحية
    async def _authorize():
        raise terr.FloodWaitError(request=None, capture=12)

    fake.is_user_authorized = _authorize  # type: ignore[method-assign]

    res = await slls.fetch_code_via_session(b"SESSION", wait_s=0)
    assert res["ok"] is False
    assert res["error"] == "rate_limited"
    assert int(res["retry_after"]) == 12


async def test_history_exception_treated_as_empty_then_timeout(monkeypatch):
    """فشل جلب السجل (لا access_hash) → سجل فارغ ثم انتظار → no_code_yet."""
    _patch_api(monkeypatch)
    fake = _FakeClient(authorized=True, raise_on_history=ValueError("PEER_ID_INVALID"))
    _patch_client(monkeypatch, fake, {})

    res = await slls.fetch_code_via_session(b"SESSION", wait_s=0.1)
    assert res["ok"] is False
    assert res["error"] == "no_code_yet"


# ══════════════ get_session_assets ══════════════


def _zip_with(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, blob in entries.items():
            zf.writestr(name, blob)
    return buf.getvalue()


async def test_assets_local_session_found(monkeypatch, tmp_path):
    root = tmp_path / "tg_ready"
    (root / "1" / "963911223344").mkdir(parents=True)
    (root / "1" / "963911223344" / "963911223344.session").write_bytes(b"LOCALSESS")
    (root / "1" / "963911223344" / "2FA.txt").write_text("pw123", encoding="utf-8")
    monkeypatch.setattr(tgrs, "READY_FILES_ROOT", root)

    item = SimpleNamespace(
        phone_number="+963911223344",
        batch_id=1,
        files_json=json.dumps([
            "1/963911223344/963911223344.session",
            "1/963911223344/2FA.txt",
        ]),
    )
    # أي محاولة تحميل شبكة = فشل
    async def _no_net(url, **kw):
        raise AssertionError("must not download when local exists")

    monkeypatch.setattr(tgrs, "download_file_bytes", _no_net)

    assets = await slls.get_session_assets(item, "+963911223344")
    assert assets.session_bytes == b"LOCALSESS"
    assert assets.twofa == "pw123"
    assert assets.cached == []
    assert assets.error is None


async def test_assets_remote_zip_downloads_and_caches(monkeypatch, tmp_path):
    root = tmp_path / "tg_ready"
    monkeypatch.setattr(tgrs, "READY_FILES_ROOT", root)
    raw = _zip_with({
        "acct_911223344.session": b"ZIPSESS",
        "2FA.txt": b"zip_pw",
    })

    async def _dl(url, **kw):
        assert "files" in url or url.endswith(".zip")
        return raw

    monkeypatch.setattr(tgrs, "download_file_bytes", _dl)

    item = SimpleNamespace(
        phone_number="+963911223344",
        batch_id=7,
        files_json=None,
    )
    payload = "+963911223344 | https://供应商.example/files/acc.zip"

    assets = await slls.get_session_assets(item, payload)
    assert assets.session_bytes == b"ZIPSESS"
    assert assets.twofa == "zip_pw"
    assert assets.error is None
    # كاش محلي: المسارات محفوظة فعلياً تحت الجذر الجديد
    assert assets.cached, "expected cached rel paths"
    for rel in assets.cached:
        assert (root / rel).is_file()


async def test_assets_no_session_anywhere(monkeypatch):
    item = SimpleNamespace(phone_number="+1234567890", batch_id=1, files_json=None)

    async def _no_net(url, **kw):
        return None

    monkeypatch.setattr(tgrs, "download_file_bytes", _no_net)
    # لا رابط ملف أصلاً → no_session
    assets = await slls.get_session_assets(item, "+1234567890")
    assert assets.session_bytes is None
    assert assets.error == "no_session"
    assert assets.cached == []


async def test_assets_download_fail(monkeypatch):
    item = SimpleNamespace(phone_number="+1234567890", batch_id=1, files_json=None)

    async def _fail(url, **kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(tgrs, "download_file_bytes", _fail)
    payload = "+1234567890 | https://host.example/files/x.zip"
    assets = await slls.get_session_assets(item, payload)
    assert assets.session_bytes is None
    assert assets.error == "download_failed"


async def test_assets_zip_without_session_is_download_failed(monkeypatch, tmp_path):
    root = tmp_path / "tg_ready"
    monkeypatch.setattr(tgrs, "READY_FILES_ROOT", root)
    raw = _zip_with({"readme.txt": b"nothing here"})

    async def _dl(url, **kw):
        return raw

    monkeypatch.setattr(tgrs, "download_file_bytes", _dl)
    item = SimpleNamespace(phone_number="+1234567890", batch_id=1, files_json=None)
    payload = "+1234567890 | https://host.example/files/x.zip"
    assets = await slls.get_session_assets(item, payload)
    assert assets.session_bytes is None
    assert assets.error == "download_failed"


# ══════════════ بوابة session_handler الإدارية ══════════════


async def test_session_handler_admin_gate(monkeypatch):
    """مستخدم غير مدير يرسل .txt → تجاهل صامت (لا رد ولا تحميل)."""
    from handlers.session_handler import handle_txt_document

    answers: list[str] = []

    class _Doc:
        file_name = "links.txt"
        file_id = "file-123"
        mime_type = "text/plain"

    class _Msg:
        document = _Doc()
        from_user = SimpleNamespace(id=999)

        async def answer(self, text, **kw):
            answers.append(text)

    class _Bot:
        async def get_file(self, *a, **kw):
            raise AssertionError("must not fetch file for non-admin")

    result = await handle_txt_document(_Msg(), _Bot())
    assert result is None
    assert answers == []


async def test_session_handler_admin_gate_missing_from_user(monkeypatch):
    from handlers.session_handler import handle_txt_document

    class _Doc:
        file_name = "links.txt"
        file_id = "f"
        mime_type = "text/plain"

    class _Msg:
        document = _Doc()
        from_user = None

        async def answer(self, text, **kw):
            raise AssertionError("must be silent")

    result = await handle_txt_document(_Msg(), object())
    assert result is None
