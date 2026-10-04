"""Tenant runtime: webhook ingress + per-tenant dispatcher for sub-bots.

- كل بوت فرعي مسار webhook خاص: POST /wh/<token_hash>.
- Dispatcher واحد مشترك + Bot مخبأ لكل مستأجر.
- عزل FSM تلقائي: مفاتيح aiogram تتضمن bot_id (مختلف لكل توكن).
- حد معدل per-tenant يحمي المشترك من تاجر مخترق/مسبام.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from datetime import datetime

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import Message, Update
from sqlalchemy import select

from config import settings
from database.engine import async_session_maker
from database.models import Tenant, User

logger = logging.getLogger(__name__)

current_tenant: ContextVar[Tenant | None] = ContextVar("current_tenant", default=None)
current_tenant_id: ContextVar[int | None] = ContextVar("current_tenant_id", default=None)
current_tenant_token: ContextVar[str | None] = ContextVar("current_tenant_token", default=None)

_tenant_bots: dict[int, Bot] = {}
_tenant_dp: Dispatcher | None = None

# حد per-tenant: 300 تحديث/دقيقة لكل بوت فرعي
_TENANT_RATE_LIMIT = 300
_tenant_hits: dict[int, deque[float]] = defaultdict(deque)


class TenantMiddleware(BaseMiddleware):
    """يحقن المستأجر والمستخدم الخاص به في سياق المعالجة."""

    async def __call__(self, handler, event, data):
        tenant_id = current_tenant_id.get()
        if tenant_id is None:
            return None
        session = data.get("session")
        if session is None:
            return None
        # تحميل المستأجر طازجاً (الحالة/الاشتراك قد تغيرت)
        fresh = await session.get(Tenant, tenant_id)
        if fresh is None or not fresh.is_active:
            return None
        if fresh.subscription_status not in ("active", "grace"):
            if isinstance(event, (Message,)):
                try:
                    await event.answer(
                        "⚠️ هذا المتجر موقوف مؤقتاً — تواصل مع إدارته."
                    )
                except Exception:
                    pass
            else:
                try:
                    cb = getattr(event, "answer", None)
                    if cb is not None:
                        await event.answer("⚠️ المتجر موقوف مؤقتاً.", show_alert=True)
                except Exception:
                    pass
            return None
        data["tenant"] = fresh

        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)
        result = await session.execute(
            select(User).where(
                User.tenant_id == fresh.id, User.telegram_id == tg_user.id
            )
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                tenant_id=fresh.id,
                telegram_id=tg_user.id,
                username=tg_user.username,
                full_name=tg_user.full_name,
                language_code=(tg_user.language_code or fresh.default_language or "ar")[:8],
                display_currency=fresh.display_currency or "USD",
                is_activated=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        else:
            user.last_activity_at = datetime.utcnow()
            if user.username != tg_user.username:
                user.username = tg_user.username
            if user.full_name != tg_user.full_name:
                user.full_name = tg_user.full_name
            await session.commit()
        if user.is_banned:
            try:
                if isinstance(event, Message):
                    await event.answer("🚫 تم حظرك من هذا المتجر.")
                else:
                    await event.answer("🚫 تم حظرك من هذا المتجر.", show_alert=True)
            except Exception:
                pass
            return None
        data["db_user"] = user
        return await handler(event, data)


def get_tenant_bot_by_id(tenant_id: int, raw_token: str) -> Bot:
    bot = _tenant_bots.get(tenant_id)
    if bot is None:
        from services.html_guard import HtmlGuardedBot

        bot = HtmlGuardedBot(
            token=raw_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        _tenant_bots[tenant_id] = bot
    return bot


def get_tenant_bot(tenant: Tenant, *, token: str | None = None) -> Bot:
    if token is None:
        token = current_tenant_token.get()
    if token is None:
        from services.tenant_service import TenantService

        token = TenantService.reveal_token(tenant)
    return get_tenant_bot_by_id(tenant.id, token)


def drop_tenant_bot(tenant_id: int) -> None:
    bot = _tenant_bots.pop(tenant_id, None)
    if bot is not None:
        try:
            import asyncio

            loop = asyncio.get_running_loop()
            loop.create_task(bot.session.close())
        except Exception:
            pass


def get_tenant_dispatcher() -> Dispatcher:
    global _tenant_dp
    if _tenant_dp is not None:
        return _tenant_dp
    storage = (
        RedisStorage.from_url(settings.REDIS_URL)
        if settings.REDIS_URL
        else MemoryStorage()
    )
    dp = Dispatcher(storage=storage)

    from middlewares.db_session import DbSessionMiddleware
    from middlewares.throttling import GlobalThrottlingMiddleware

    tenant_mw = TenantMiddleware()
    db_mw = DbSessionMiddleware()
    throttle_mw = GlobalThrottlingMiddleware()
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(tenant_mw)
        observer.outer_middleware(db_mw)
        observer.outer_middleware(throttle_mw)

    from handlers.tenant import store as tenant_store
    from handlers.tenant import support as tenant_support
    from handlers.tenant import deposit as tenant_deposit

    dp.include_router(tenant_store.router)
    dp.include_router(tenant_support.router)
    dp.include_router(tenant_deposit.router)

    _tenant_dp = dp
    return dp


def _tenant_allowed(tenant_id: int) -> bool:
    now = time.monotonic()
    hits = _tenant_hits[tenant_id]
    while hits and now - hits[0] >= 60:
        hits.popleft()
    if len(hits) >= _TENANT_RATE_LIMIT:
        return False
    hits.append(now)
    if len(_tenant_hits) > 5000:
        _tenant_hits.clear()
    return True


async def feed_update(token_hash: str, update_dict: dict) -> dict:
    """يغذي تحديث تيليجرام خام للـ dispatcher الخاص بالمستأجرين.

    يرجع {"ok": True} أو {"ok": False, "reason": ...}.
    """
    from services.tenant_service import TenantService

    async with async_session_maker() as session:
        tenant = await TenantService.get_by_hash(session, token_hash)
        if tenant is None:
            return {"ok": False, "reason": "unknown tenant"}
        if not TenantService.is_usable(tenant):
            return {"ok": False, "reason": "suspended"}
        # استخراج كل ما يلزم قبل إغلاق الجلسة (تجنب detached attributes)
        tenant_id = tenant.id
        brand = tenant.brand_name
        try:
            raw_token = TenantService.reveal_token(tenant)
        except Exception:
            logger.exception("تعذر فك توكن المستأجر %s", tenant_id)
            return {"ok": False, "reason": "token error"}

    if not _tenant_allowed(tenant_id):
        logger.warning("tenant %s تجاوز حد المعدل", tenant_id)
        return {"ok": False, "reason": "rate limited"}

    bot = get_tenant_bot_by_id(tenant_id, raw_token)
    update = Update(**update_dict)
    t1 = current_tenant_id.set(tenant_id)
    t2 = current_tenant_token.set(raw_token)
    try:
        dp = get_tenant_dispatcher()
        await dp.feed_update(bot, update)
    except Exception:
        logger.exception("فشل معالجة تحديث المستأجر %s (%s)", tenant_id, brand)
        return {"ok": False, "reason": "handler error"}
    finally:
        current_tenant_id.reset(t1)
        current_tenant_token.reset(t2)
    return {"ok": True}
