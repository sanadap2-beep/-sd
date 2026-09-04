""":⚙️ سيرفرات/مزودي الأرقام المتعددين.

الهدف:
- كل خدمة أرقام تحوي أكثر من سيرفر (مزود)، يختاره المستخدم قبل الدول.
- الأدمن يدير السيرفرات بلا تعديل كود.
- عند القفل على سيرفر، تُعرض الدول من مزوده فقط.
"""

from __future__ import annotations

from decimal import Decimal

from database.engine import async_session_maker
from database.models import NumberServer, NumberService, ProviderName
from services.number_server_service import NumberServerService
from keyboards.numbers import countries_price_kb


async def _seed_service(session, code="wa", fivesim="wa", herosms=None, sms_activate=None, smshub=None) -> NumberService:
    svc = NumberService(
        code=code,
        name_ar="واتساب",
        emoji="💬",
        fivesim_code=fivesim,
        herosms_code=herosms,
        sms_activate_code=sms_activate,
        smshub_code=smshub,
        is_active=True,
    )
    session.add(svc)
    await session.commit()
    await session.refresh(svc)
    return svc


async def test_ensure_defaults_creates_one_server_per_configured_provider():
    async with async_session_maker() as session:
        svc = await _seed_service(
            session,
            code="wa",
            fivesim="wa",
            herosms="wa_hero",
            sms_activate="wa_act",
            smshub=None,
        )

        servers = await NumberServerService.ensure_defaults(session, svc)

    assert len(servers) == 3
    providers = {server.provider for server in servers}
    assert providers == {
        ProviderName.FIVESIM.value,
        ProviderName.HEROSMS.value,
        ProviderName.SMS_ACTIVATE.value,
    }


async def test_ensure_defaults_does_not_duplicate_existing_servers():
    async with async_session_maker() as session:
        svc = await _seed_service(session, code="wa", fivesim="wa", herosms="hero")
        servers_1 = await NumberServerService.ensure_defaults(session, svc)
        servers_2 = await NumberServerService.ensure_defaults(session, svc)

    assert len(servers_1) == 2
    assert len(servers_2) == 2
    assert servers_2[0].id == servers_1[0].id


async def test_list_servers_active_only():
    async with async_session_maker() as session:
        svc = await _seed_service(session, code="wa", fivesim="wa", herosms="hero")
        servers = await NumberServerService.ensure_defaults(session, svc)
        await NumberServerService.update(session, servers[0].id, is_active=False)

        all_servers = await NumberServerService.list_servers(session, svc.id, active_only=False)
        active = await NumberServerService.list_servers(session, svc.id, active_only=True)

    assert len(all_servers) == 2
    assert len(active) == 1
    assert all(server.is_active for server in active)


async def test_validate_provider_rejects_unknown():
    assert NumberServerService.validate_provider("fivesim") == "fivesim"
    assert NumberServerService.validate_provider("unknown") is None


async def test_countries_price_kb_carries_server_id():
    entries = [
        type("E", (), {"code": "us", "flag": "🇺🇸", "name_ar": "الولايات المتحدة", "sell_usd": Decimal("1.23"), "cost_usd": Decimal("0.80")})(),
    ]
    kb = countries_price_kb("wa", entries, page=0, server_id=7, server_label="🟢 5sim")
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]

    # الدولة والسيرفر بينفعوا في نفس الباني — لا يختلط السيرفر بين الدول.
    assert "num_country:wa:us:7" in callbacks
    assert any(cb == "num_server:wa" for cb in callbacks)


# ══════════════ إصلاح: coroutine مرر كـ reply_markup ══════════════


def _fake_number_servers(count: int) -> list[NumberServer]:
    """سيرفرات أرقام بدون قاعدة بيانات لاختبار الباني."""
    return [
        NumberServer(
            number_service_id=1,
            name_ar=f"سيرفر {i + 1}",
            emoji="🖥",
            provider=ProviderName.FIVESIM,
            is_active=True,
        )
        for i in range(count)
    ]


def test_servers_kb_returns_markup_directly_not_coroutine():
    """انحدار: كان _servers_kb async ويستدعى بلا await فمرّر coroutine
    إلى reply_markup → ValidationError عند اختيار الخدمة (num_svc:...).
    يجب أن يعيد InlineKeyboardMarkup جاهزاً مباشرة."""
    import inspect

    from aiogram.types import InlineKeyboardMarkup
    from handlers.numbers import _servers_kb

    servers = _fake_number_servers(3)
    kb = _servers_kb("wa", servers)

    assert isinstance(kb, InlineKeyboardMarkup)  # لو رجعت async لكانت coroutine
    assert not inspect.isawaitable(kb)
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert callbacks[0].startswith("num_server_pick:wa:")
    assert "num_hub" in callbacks  # زر الرجوع


async def test_change_server_button_reaches_a_handler():
    """انحدار: كان فلتر المعالج F.data == "num_server:" (يطابق مستحيل)
    فزر «تغيير السيرفر» مات وسقط إلى fallback. الآن num_server:{code} يُعالج."""
    from aiogram.types import CallbackQuery, User as AiogramUser
    from handlers.numbers import router as numbers_router

    async def _first_match(data: str) -> bool:
        cb = CallbackQuery(
            id="q",
            from_user=AiogramUser(id=1, is_bot=False, first_name="t"),
            chat_instance="ci",
            data=data,
        )
        for handler in numbers_router.callback_query.handlers:
            matched, _ = await handler.check(cb)
            if matched:
                return True
        return False

    assert await _first_match("num_server:wa") is True
    # ولا يختلط مع زر اختيار السيرفر نفسه
    assert await _first_match("num_server_pick:wa:7") is True
