#!/usr/bin/env python3
"""Run a non-destructive hosting/staging preflight.

The application still performs migrations during startup. This command is an
operator check, not a replacement for ``init_db``: it validates the environment,
checks the configured database without changing it, verifies that risky
features default to off, and optionally probes the public health endpoints.

Examples:

  # Before the first start (schema may not exist yet):
  python scripts/preflight.py

  # After the API has started and migrations have run:
  python scripts/preflight.py --base-url https://bot.example.com --require-schema

  # Treat operational warnings, such as missing Redis, as release blockers:
  python scripts/preflight.py --strict
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping
from urllib import error as url_error
from urllib import request as url_request
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{20,}$")
DATABASE_PREFIXES = ("sqlite+aiosqlite://", "postgresql+asyncpg://")
PLACEHOLDERS = {"change_me", "changeme", "your-token", "your_token", "none", "null"}
PROVIDER_ENV_KEYS = (
    "FIVESIM_API_KEY",
    "HEROSMS_API_KEY",
    "SMS_ACTIVATE_API_KEY",
    "SMSHUB_API_KEY",
)


@dataclass(frozen=True)
class Finding:
    level: str
    check: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _value(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key, "")).strip()


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return lowered in PLACEHOLDERS or lowered.startswith("change_me")


def check_environment(env: Mapping[str, str] | None = None) -> list[Finding]:
    """Validate non-secret configuration without printing secret values."""
    if env is None:
        env = os.environ
    findings: list[Finding] = []

    required = ("BOT_TOKEN", "BOT_USERNAME", "ADMIN_IDS", "ADMIN_NOTIFY_CHAT_ID")
    for key in required:
        value = _value(env, key)
        if not value:
            findings.append(Finding("error", key, "missing required environment variable"))
        elif _is_placeholder(value):
            findings.append(Finding("error", key, "placeholder value is not allowed"))
        else:
            findings.append(Finding("ok", key, "configured"))

    token = _value(env, "BOT_TOKEN")
    if token and not TOKEN_RE.fullmatch(token):
        findings.append(Finding("error", "BOT_TOKEN format", "does not match a Telegram bot token"))

    admin_ids = _value(env, "ADMIN_IDS")
    if admin_ids:
        pieces = [piece.strip() for piece in admin_ids.split(",")]
        if not pieces or any(not piece.isdigit() or int(piece) <= 0 for piece in pieces):
            findings.append(Finding("error", "ADMIN_IDS format", "use positive numeric Telegram user IDs"))
        else:
            findings.append(Finding("ok", "ADMIN_IDS format", f"{len(pieces)} admin ID(s)"))

    notify_chat = _value(env, "ADMIN_NOTIFY_CHAT_ID")
    if notify_chat:
        try:
            if int(notify_chat) == 0:
                raise ValueError
        except ValueError:
            findings.append(Finding("error", "ADMIN_NOTIFY_CHAT_ID format", "must be a non-zero numeric chat ID"))
        else:
            findings.append(Finding("ok", "ADMIN_NOTIFY_CHAT_ID format", "valid numeric chat ID"))

    database_url = _value(env, "DATABASE_URL")
    if not database_url:
        findings.append(
            Finding(
                "warn",
                "DATABASE_URL",
                "not set; application default is local SQLite (single-node staging only)",
            )
        )
    elif not database_url.startswith(DATABASE_PREFIXES):
        findings.append(
            Finding(
                "error",
                "DATABASE_URL",
                "unsupported driver; use sqlite+aiosqlite or postgresql+asyncpg",
            )
        )
    else:
        database_kind = "PostgreSQL" if database_url.startswith("postgresql+") else "SQLite"
        findings.append(Finding("ok", "DATABASE_URL", f"supported {database_kind} driver"))

    environment = _value(env, "ENVIRONMENT").lower() or "production"
    if environment not in {"development", "dev", "test", "staging", "production", "prod"}:
        findings.append(Finding("warn", "ENVIRONMENT", f"unusual value: {environment}"))

    for key in ("WEBAPP_URL", "ADMIN_WEBAPP_URL"):
        value = _value(env, key)
        if not value:
            findings.append(Finding("ok", key, "not configured (optional)"))
            continue
        parsed = urlparse(value)
        if not parsed.netloc or parsed.scheme not in {"http", "https"}:
            findings.append(Finding("error", key, "must be an absolute http(s) URL"))
        elif environment in {"production", "prod"} and parsed.scheme != "https":
            findings.append(Finding("error", key, "HTTPS is required in production"))
        else:
            findings.append(Finding("ok", key, "valid URL"))

    setup_key = _value(env, "SETUP_KEY")
    if setup_key and len(setup_key) < 32:
        findings.append(Finding("error", "SETUP_KEY", "must be at least 32 characters when configured"))
    elif setup_key:
        findings.append(Finding("warn", "SETUP_KEY", "configured; remove it after one-time setup"))
    else:
        findings.append(Finding("ok", "SETUP_KEY", "disabled"))

    redis_url = _value(env, "REDIS_URL")
    if redis_url:
        findings.append(Finding("ok", "REDIS_URL", "configured"))
    else:
        findings.append(
            Finding(
                "warn",
                "REDIS_URL",
                "not configured; FSM is in memory and must run as a single process",
            )
        )

    encryption_key = _value(env, "INVENTORY_ENCRYPTION_KEY")
    if not encryption_key:
        findings.append(
            Finding(
                "warn",
                "INVENTORY_ENCRYPTION_KEY",
                "not configured; digital inventory sales will remain unavailable",
            )
        )
    else:
        try:
            from cryptography.fernet import Fernet

            Fernet(encryption_key.encode())
        except Exception:  # noqa: BLE001 - report only a safe validation result
            findings.append(Finding("error", "INVENTORY_ENCRYPTION_KEY", "is not a valid Fernet key"))
        else:
            findings.append(Finding("ok", "INVENTORY_ENCRYPTION_KEY", "valid Fernet key"))

    configured_providers = [key for key in PROVIDER_ENV_KEYS if _value(env, key)]
    if configured_providers:
        findings.append(
            Finding("ok", "SMS providers", f"{len(configured_providers)} provider credential(s) configured")
        )
    else:
        findings.append(
            Finding("warn", "SMS providers", "none configured; number purchases cannot be tested yet")
        )

    return findings


def _sqlite_database_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite+aiosqlite:///"):
        return None
    raw_path = database_url.split("sqlite+aiosqlite:///", 1)[1].split("?", 1)[0]
    if raw_path in {":memory:", ""}:
        return None
    path = Path(raw_path)
    return path if path.is_absolute() else ROOT / path


def check_safe_feature_defaults() -> list[Finding]:
    """Ensure the registry cannot accidentally turn risky flows on by default."""
    from services.feature_registry import BY_KEY, SAFE_DEFAULT_DISABLED_FEATURES

    findings: list[Finding] = []
    for key in sorted(SAFE_DEFAULT_DISABLED_FEATURES):
        spec = BY_KEY.get(key)
        if spec is None:
            findings.append(Finding("error", f"feature:{key}", "listed as risky but missing from registry"))
        elif spec.default_enabled:
            findings.append(Finding("error", f"feature:{key}", "must default to disabled"))
        else:
            findings.append(Finding("ok", f"feature:{key}", "safe default is disabled"))
    return findings


async def _database_check(database_url: str, require_schema: bool) -> list[Finding]:
    from sqlalchemy import inspect, select, text
    from sqlalchemy.ext.asyncio import create_async_engine

    findings: list[Finding] = []
    sqlite_path = _sqlite_database_path(database_url)
    if sqlite_path is not None and not sqlite_path.exists():
        level = "error" if require_schema else "warn"
        findings.append(
            Finding(level, "database schema", f"SQLite file does not exist yet: {sqlite_path.name}")
        )
        return findings

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            findings.append(Finding("ok", "database connection", "SELECT 1 succeeded"))
            tables = await connection.run_sync(lambda sync_connection: set(inspect(sync_connection).get_table_names()))

            if "alembic_version" not in tables:
                level = "error" if require_schema else "warn"
                findings.append(
                    Finding(level, "database schema", "alembic_version is missing; start the app to run migrations")
                )
                return findings

            versions = {
                row[0]
                for row in (await connection.execute(text("SELECT version_num FROM alembic_version"))).all()
            }
            from alembic.config import Config
            from alembic.script import ScriptDirectory

            alembic_config = Config(str(ROOT / "alembic.ini"))
            alembic_config.set_main_option("script_location", str(ROOT / "migrations"))
            heads = set(ScriptDirectory.from_config(alembic_config).get_heads())
            if versions != heads:
                findings.append(
                    Finding(
                        "error",
                        "database schema",
                        f"migration version {sorted(versions) or 'none'} does not match head {sorted(heads)}",
                    )
                )
            else:
                findings.append(Finding("ok", "database schema", f"at migration head {sorted(heads)[0]}"))

            required_tables = {"users", "transactions"}
            missing = sorted(required_tables - tables)
            if missing:
                findings.append(Finding("error", "database core tables", f"missing: {', '.join(missing)}"))
            else:
                findings.append(Finding("ok", "database core tables", "users and transactions exist"))

            if "transactions" in tables:
                columns = await connection.run_sync(
                    lambda sync_connection: {
                        column["name"] for column in inspect(sync_connection).get_columns("transactions")
                    }
                )
                if "payment_reference" not in columns:
                    findings.append(
                        Finding("error", "transaction idempotency", "transactions.payment_reference is missing")
                    )
                else:
                    findings.append(Finding("ok", "transaction idempotency", "payment_reference column exists"))

            if "feature_flags" in tables:
                from database.models import FeatureFlag
                from services.feature_registry import SAFE_DEFAULT_DISABLED_FEATURES

                rows = (
                    await connection.execute(
                        select(FeatureFlag.key, FeatureFlag.enabled).where(
                            FeatureFlag.key.in_(SAFE_DEFAULT_DISABLED_FEATURES)
                        )
                    )
                ).all()
                enabled = sorted(key for key, is_enabled in rows if is_enabled)
                if enabled:
                    findings.append(
                        Finding(
                            "error",
                            "risky feature flags",
                            "enabled in database: " + ", ".join(enabled),
                        )
                    )
                else:
                    findings.append(Finding("ok", "risky feature flags", "all registered risky flags are disabled"))
            elif require_schema:
                findings.append(Finding("error", "feature flags", "feature_flags table is missing"))
    except Exception as exc:  # noqa: BLE001 - convert connection failures to operator output
        findings.append(Finding("error", "database connection", f"{type(exc).__name__}: {str(exc)[:180]}"))
    finally:
        await engine.dispose()
    return findings


def _http_check(base_url: str, path: str, timeout: float) -> Finding:
    url = base_url.rstrip("/") + path
    try:
        with url_request.urlopen(url, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            status = response.status
    except url_error.HTTPError as exc:
        return Finding("error", f"HTTP {path}", f"returned {exc.code}")
    except Exception as exc:  # noqa: BLE001
        return Finding("error", f"HTTP {path}", f"{type(exc).__name__}: {str(exc)[:160]}")

    if not 200 <= status < 300:
        return Finding("error", f"HTTP {path}", f"returned {status}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {}
    if path.startswith("/health/") and payload.get("status") != "ok":
        return Finding("error", f"HTTP {path}", "response did not report status=ok")
    return Finding("ok", f"HTTP {path}", f"returned {status}")


def _print_findings(findings: list[Finding], as_json: bool) -> None:
    if as_json:
        print(json.dumps([finding.as_dict() for finding in findings], ensure_ascii=False, indent=2))
        return

    symbols = {"ok": "✅", "warn": "⚠️", "error": "❌"}
    for finding in findings:
        print(f"{symbols.get(finding.level, '•')} [{finding.level.upper():5}] {finding.check}: {finding.detail}")
    counts = {level: sum(finding.level == level for finding in findings) for level in symbols}
    print(
        "\nPreflight summary: "
        f"{counts['ok']} ok, {counts['warn']} warning(s), {counts['error']} error(s)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate hosting configuration without mutating the database")
    parser.add_argument("--base-url", help="public API URL; probes /health/live and /health/ready")
    parser.add_argument("--skip-db", action="store_true", help="skip the database connection/schema check")
    parser.add_argument("--require-schema", action="store_true", help="fail if migrations have not run")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP/database timeout in seconds")
    parser.add_argument("--json", action="store_true", dest="as_json", help="print machine-readable JSON")
    args = parser.parse_args(argv)

    findings = check_environment()
    findings.extend(check_safe_feature_defaults())

    database_url = _value(os.environ, "DATABASE_URL") or "sqlite+aiosqlite:///./bot_database.db"
    if not args.skip_db and database_url.startswith(DATABASE_PREFIXES):
        findings.extend(asyncio.run(_database_check(database_url, args.require_schema)))
    elif not args.skip_db:
        # The environment validator already reports the unsupported URL.
        pass

    if args.base_url:
        findings.append(_http_check(args.base_url, "/health/live", args.timeout))
        findings.append(_http_check(args.base_url, "/health/ready", args.timeout))

    _print_findings(findings, args.as_json)
    has_errors = any(finding.level == "error" for finding in findings)
    has_warnings = any(finding.level == "warn" for finding in findings)
    return 1 if has_errors or (args.strict and has_warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
