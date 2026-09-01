"""Run Alembic migrations safely during startup."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from config import settings


_ROOT = Path(__file__).resolve().parent.parent


def _sync_url() -> str:
    return settings.DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", "")


def _run_sync() -> None:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", _sync_url().replace("%", "%%"))
    engine = create_engine(_sync_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
        if tables and "alembic_version" not in tables:
            # Existing installations predate Alembic. Mark the current schema
            # as the baseline; seed.py keeps its compatibility column checks.
            command.stamp(config, "head")
        else:
            command.upgrade(config, "head")
    finally:
        engine.dispose()


async def run_migrations() -> None:
    await asyncio.to_thread(_run_sync)
