"""StateResetMiddleware must not wipe admin provider-wizard data."""

from __future__ import annotations

import pytest

from middlewares.state_reset_middleware import should_reset_fsm


ADMIN_CURRENCY_STATE = "AdminApiProviderStates:waiting_currency"
DEPOSIT_STATE = "DepositStates:waiting_amount"


def test_admin_currency_step_keeps_wizard_state():
    assert (
        should_reset_fsm(ADMIN_CURRENCY_STATE, callback_data="admin:aprov_curr:USD")
        is False
    )
    assert (
        should_reset_fsm(ADMIN_CURRENCY_STATE, callback_data="admin:aprov_test")
        is False
    )
    assert (
        should_reset_fsm(ADMIN_CURRENCY_STATE, callback_data="admin:aprov_ptype:smm")
        is False
    )


def test_admin_home_cancels_wizard():
    assert should_reset_fsm(ADMIN_CURRENCY_STATE, callback_data="admin:main") is True


def test_user_menu_still_resets_admin_and_user_flows():
    assert should_reset_fsm(ADMIN_CURRENCY_STATE, callback_data="menu:deposit") is True
    assert should_reset_fsm(DEPOSIT_STATE, callback_data="back_to_main") is True
    assert should_reset_fsm(DEPOSIT_STATE, is_slash_command=True) is True


def test_opening_admin_from_user_flow_still_resets():
    assert should_reset_fsm(DEPOSIT_STATE, callback_data="admin:api_providers") is True
    assert should_reset_fsm(DEPOSIT_STATE, callback_data="admin:aprov_curr:USD") is True


def test_no_state_never_resets():
    assert should_reset_fsm(None, callback_data="admin:aprov_curr:USD") is False
    assert should_reset_fsm(None, is_slash_command=True) is False


@pytest.mark.asyncio
async def test_middleware_keeps_provider_wizard_data_on_currency_click():
    from aiogram.types import CallbackQuery, User as TgUser
    from middlewares.state_reset_middleware import StateResetMiddleware

    class FakeState:
        def __init__(self):
            self.cleared = False
            self._state = ADMIN_CURRENCY_STATE
            self.data = {"protocol_type": "smm_v2", "name": "JumboSMM"}

        async def get_state(self):
            return None if self.cleared else self._state

        async def clear(self):
            self.cleared = True
            self.data = {}

    callback = CallbackQuery(
        id="1",
        from_user=TgUser(id=1, is_bot=False, first_name="Admin"),
        chat_instance="x",
        data="admin:aprov_curr:USD",
    )
    state = FakeState()
    middleware = StateResetMiddleware()

    async def handler(event, data):
        return data["state"].data

    result = await middleware(handler, callback, {"state": state})
    assert state.cleared is False
    assert result["protocol_type"] == "smm_v2"
