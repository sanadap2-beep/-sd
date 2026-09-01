"""
اختبارات الإصلاحات الجديدة على الميدل ويرز والبث الجماعي:

1) user_middleware لا يكتب في قاعدة البيانات عند كل حدث — فقط عند تغيّر
   فعلي أو مرور 60 ثانية على آخر نشاط مسجل.
2) subscription_middleware يخزّن نتيجة فحص الاشتراك 60 ثانية فلا يضرب
   حد Telegram FloodControl مع كل ضغطة زر.
3) تقسيم رسائل البث الطويلة (نص 4096 / كابشن 1024) وتجاوز المستخدمين
   الذين حظروا البوت دون اعتبارهم فشلاً.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers.admin.broadcast import (
    _send_with_retry,
    _send_text_chunks,
    _send_to_user,
    _split_text,
)
from middlewares.subscription_middleware import SubscriptionMiddleware
from middlewares.user_middleware import UserMiddleware


# ────────────────────────── تقسيم النصوص ──────────────────────────


def test_split_text_keeps_chunks_under_limit():
    text = "كلمة " * 3000  # 15000 حرف تقريباً
    chunks = _split_text(text, 4000)
    assert len(chunks) > 1
    assert all(len(c) <= 4000 for c in chunks)
    # لا يُفقد أي محتوى: الكلمات نفسها تبقى بنفس الترتيب بعد التقسيم
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_split_text_short_returns_single_chunk():
    assert _split_text("قصير", 4096) == ["قصير"]
    assert _split_text("", 4096) == []


@pytest.mark.asyncio
async def test_send_with_retry_retries_on_retry_after():
    calls = []

    async def flaky(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            from aiogram.exceptions import TelegramRetryAfter

            raise TelegramRetryAfter(
                method=SimpleNamespace(__class__=SimpleNamespace(__name__="send_message")),
                message="retry after 1s",
                retry_after=1,
            )
        return True

    ok = await _send_with_retry(flaky, chat_id=1)
    assert ok is True
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_send_with_retry_forbidden_is_not_failure():
    from aiogram.exceptions import TelegramForbiddenError

    async def forbidden(**kwargs):
        raise TelegramForbiddenError(
            method=SimpleNamespace(__class__=SimpleNamespace(__name__="send_message")),
            message="blocked",
        )

    assert await _send_with_retry(forbidden, chat_id=1) is True


@pytest.mark.asyncio
async def test_send_text_chunks_splits_long_text():
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=lambda **kw: True),
    )
    long_text = "رسالة " * 1500  # > 4096 حرف
    ok = await _send_text_chunks(bot, 999, long_text)
    assert ok is True
    sent_messages = [c.kwargs["text"] for c in bot.send_message.await_args_list]
    assert len(sent_messages) > 1
    assert all(len(t) <= 4000 for t in sent_messages)


@pytest.mark.asyncio
async def test_send_to_user_none_message():
    """رسالة نصية بلا محتوى يُرسل كنص فارغ دون انهيار."""
    bot = SimpleNamespace(send_message=AsyncMock(return_value=True))
    message = SimpleNamespace(
        photo=None,
        video=None,
        document=None,
        text=None,
        caption=None,
    )
    assert await _send_to_user(bot, 5, message) is True


# ───────────────────── اشتراك: كاش 60 ثانية ─────────────────────


@pytest.mark.asyncio
async def test_subscription_middleware_caches_check_result():
    middleware = SubscriptionMiddleware(bot=object())
    middleware._cache.clear()

    calls = {"n": 0}

    async def fake_check(bot, session, telegram_id):
        calls["n"] += 1
        return True, []

    import services.subscription_service as sub_mod

    monkeypatch_original = sub_mod.SubscriptionService.is_user_subscribed_all
    sub_mod.SubscriptionService.is_user_subscribed_all = staticmethod(fake_check)
    try:
        async def handler(event, data):
            return "ok"

        db_user = SimpleNamespace(telegram_id=777, is_admin=False)

        # حدثان متتاليان لنفس المستخدم
        for _ in range(2):
            result = await middleware(
                handler,
                SimpleNamespace(data="some_button", answer=AsyncMock()),
                {"db_user": db_user, "session": object()},
            )
            assert result == "ok"

        assert calls["n"] == 1, "الفحص الثاني يجب أن يأتي من الكاش"

        # زر التحقق نفسه مُعفى من الاعتراض، لكن معالج الحقيقة يجري فحصاً
        # جديداً مباشرة — الميدلوير هنا يمرر الحدث دون حظر إضافي.
        await middleware(
            handler,
            SimpleNamespace(data="check_subscription", answer=AsyncMock()),
            {"db_user": db_user, "session": object()},
        )
        assert calls["n"] == 1, "زر التحقق يُمرَّر من الميدلوير دون فحص إضافي"
    finally:
        sub_mod.SubscriptionService.is_user_subscribed_all = monkeypatch_original


@pytest.mark.asyncio
async def test_subscription_middleware_cache_expires():
    middleware = SubscriptionMiddleware(bot=object())
    middleware._cache.clear()

    calls = {"n": 0}

    async def fake_check(bot, session, telegram_id):
        calls["n"] += 1
        return True, []

    import services.subscription_service as sub_mod

    original = sub_mod.SubscriptionService.is_user_subscribed_all
    sub_mod.SubscriptionService.is_user_subscribed_all = staticmethod(fake_check)
    try:
        async def handler(event, data):
            return "ok"

        db_user = SimpleNamespace(telegram_id=888, is_admin=False)

        await middleware(
            handler,
            SimpleNamespace(data="a", answer=AsyncMock()),
            {"db_user": db_user, "session": object()},
        )
        # نمدّد عمر الكاش في الذاكرة: نحذف الإدخال يدوياً لمحاكاة انتهاء المدة
        middleware._cache.pop(888, None)
        await middleware(
            handler,
            SimpleNamespace(data="b", answer=AsyncMock()),
            {"db_user": db_user, "session": object()},
        )
        assert calls["n"] == 2
    finally:
        sub_mod.SubscriptionService.is_user_subscribed_all = original


# ─────────────── user middleware: تقليل عمليات الكتابة ───────────────


class _FakeResult:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeSession:
    def __init__(self, user):
        self.user = user
        self.commit_count = 0

    async def execute(self, _statement):
        return _FakeResult(self.user)

    async def commit(self):
        self.commit_count += 1

    async def refresh(self, _user):
        pass

    async def get(self, *_a, **_k):
        return self.user


@pytest.mark.asyncio
async def test_user_middleware_commits_once_for_two_events():
    middleware = UserMiddleware()

    tg_user = SimpleNamespace(
        id=1001,
        username="test",
        full_name="مستخدم تجريبي",
        language_code="ar",
    )
    db_user = SimpleNamespace(
        telegram_id=1001,
        username="test",
        full_name="مستخدم تجريبي",
        language_code="ar",
        last_activity_at=None,
        is_banned=False,
        is_admin=False,
    )
    session = _FakeSession(db_user)

    async def handler(event, data):
        return "ok"

    import services.settings_service as settings_mod

    async def fake_get_bool(*_a, **_k):
        return False

    original_get_bool = settings_mod.SettingsService.get_bool
    settings_mod.SettingsService.get_bool = staticmethod(fake_get_bool)
    try:
        await middleware(
            handler,
            SimpleNamespace(text="/start", answer=AsyncMock()),
            {"session": session, "event_from_user": tg_user},
        )
        await middleware(
            handler,
            SimpleNamespace(text="/menu", answer=AsyncMock()),
            {"session": session, "event_from_user": tg_user},
        )
        # حدثان في نفس الدقيقة بلا تغيير في البيانات → commit واحد كحد أقصى
        assert session.commit_count <= 1
    finally:
        settings_mod.SettingsService.get_bool = original_get_bool
