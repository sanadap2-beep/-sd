from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from database.engine import async_session_maker
from database.seed import DEFAULT_SETTINGS
from scripts.preflight import check_environment, check_safe_feature_defaults


def _valid_environment() -> dict[str, str]:
    return {
        "BOT_TOKEN": "123456:TEST_TOKEN_FOR_TESTS_1234567890",
        "BOT_USERNAME": "test_bot",
        "ADMIN_IDS": "1,2",
        "ADMIN_NOTIFY_CHAT_ID": "-1001",
        "DATABASE_URL": "sqlite+aiosqlite:///./preflight-test.db",
        "ENVIRONMENT": "staging",
        "INVENTORY_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "REDIS_URL": "redis://localhost:6379/0",
        "FIVESIM_API_KEY": "provider-key",
    }


def test_preflight_accepts_valid_core_environment_without_exposing_secrets():
    env = _valid_environment()
    findings = check_environment(env)

    assert not any(finding.level == "error" for finding in findings)
    rendered = " ".join(finding.detail for finding in findings)
    assert env["BOT_TOKEN"] not in rendered
    assert env["INVENTORY_ENCRYPTION_KEY"] not in rendered


def test_preflight_rejects_missing_credentials_and_bad_urls():
    env = _valid_environment()
    env.update(
        {
            "BOT_TOKEN": "",
            "ADMIN_IDS": "not-an-id",
            "ADMIN_NOTIFY_CHAT_ID": "0",
            "WEBAPP_URL": "http://webapp.example.test",
            "ENVIRONMENT": "production",
        }
    )

    findings = check_environment(env)
    errors = {finding.check for finding in findings if finding.level == "error"}
    assert "BOT_TOKEN" in errors
    assert "ADMIN_IDS format" in errors
    assert "ADMIN_NOTIFY_CHAT_ID format" in errors
    assert "WEBAPP_URL" in errors


def test_risky_feature_registry_defaults_are_fail_closed():
    findings = check_safe_feature_defaults()
    assert findings
    assert not any(finding.level == "error" for finding in findings)


def test_payment_and_withdrawal_seeds_are_disabled_by_default():
    for key in (
        "payment_shamcash_manual_enabled",
        "payment_stars_enabled",
        "payment_usdt_manual_enabled",
        "payment_shamcash_auto_enabled",
        "payment_usdt_auto_enabled",
        "payment_other_enabled",
        "withdraw_shamcash_syp_enabled",
    ):
        assert DEFAULT_SETTINGS[key] == "false"


@pytest.mark.asyncio
async def test_payment_method_helper_fails_closed_until_enabled_and_configured():
    from handlers.deposit_methods import _payment_method_enabled
    from services.settings_service import SettingsService

    await SettingsService.reload()
    assert await _payment_method_enabled("stars") is False
    assert await _payment_method_enabled("shamcash_manual") is False

    async with async_session_maker() as session:
        await SettingsService.set(session, "payment_stars_enabled", "true")
        await SettingsService.set(session, "payment_shamcash_manual_enabled", "true")
    assert await _payment_method_enabled("stars") is True
    assert await _payment_method_enabled("shamcash_manual") is False

    async with async_session_maker() as session:
        await SettingsService.set(session, "shamcash_manual_address", "wallet-for-test")
    assert await _payment_method_enabled("shamcash_manual") is True
