from __future__ import annotations

import inspect as python_inspect
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import database.migrations as migrations
from handlers.numbers import ready_number_packages


# Keep the migration test independent from the database used by the global
# async fixture. Alembic itself runs synchronously in the startup thread.
def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_unversioned_schema_policy_is_fail_closed() -> None:
    assert migrations._unversioned_schema_action(set()) == "upgrade"
    assert migrations._unversioned_schema_action(
        set(migrations._INITIAL_SCHEMA_TABLES)
    ) == "baseline_initial"
    assert migrations._unversioned_schema_action({"users", "transactions"}) == "reject"
    assert migrations._unversioned_schema_action(
        set(migrations._INITIAL_SCHEMA_TABLES) | {"feature_flags"}
    ) == "reject"


def test_known_initial_schema_is_upgraded_instead_of_stamped_as_head(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "legacy.db"
    sync_url = f"sqlite:///{database_path}"
    async_url = f"sqlite+aiosqlite:///{database_path}"

    # The Alembic environment supports both the explicit config URL and the
    # environment fallback used by direct CLI calls.
    monkeypatch.setattr(migrations.settings, "DATABASE_URL", async_url)
    monkeypatch.setenv("DATABASE_URL", async_url)

    command.upgrade(_alembic_config(sync_url), migrations._INITIAL_SCHEMA_REVISION)
    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))

    migrations._run_sync()

    inspector = inspect(engine)
    assert inspector.get_columns("users")
    assert "display_currency" in {column["name"] for column in inspector.get_columns("users")}
    assert "cart_items" in inspector.get_table_names()
    assert "feature_flags" in inspector.get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "c9d41f7a2b15"
    engine.dispose()


def test_unknown_unversioned_schema_is_not_modified(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "unknown.db"
    sync_url = f"sqlite:///{database_path}"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_engine(sync_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))

    monkeypatch.setattr(migrations.settings, "DATABASE_URL", async_url)
    monkeypatch.setenv("DATABASE_URL", async_url)

    with pytest.raises(RuntimeError, match="refusing to stamp head"):
        migrations._run_sync()

    assert "alembic_version" not in inspect(engine).get_table_names()
    engine.dispose()


def test_startup_and_bulk_callback_dependencies_are_explicit() -> None:
    import bot

    assert hasattr(bot, "async_session_maker")
    assert "db_user" in python_inspect.signature(ready_number_packages).parameters


@pytest.mark.asyncio
async def test_main_reaches_polling_after_initialization(monkeypatch) -> None:
    import bot

    class StopPolling(Exception):
        pass

    class FakeScheduler:
        def __init__(self) -> None:
            self.shutdown_called = False

        def shutdown(self, wait=False) -> None:
            self.shutdown_called = True

    scheduler = FakeScheduler()
    monkeypatch.setattr(bot, "start_scheduler", AsyncMock(return_value=scheduler))
    monkeypatch.setattr(bot.bot, "delete_webhook", AsyncMock())
    monkeypatch.setattr(bot.dp, "start_polling", AsyncMock(side_effect=StopPolling))
    monkeypatch.setattr(bot.plisio_client, "close", AsyncMock())

    with pytest.raises(StopPolling):
        await bot.main()

    assert scheduler.shutdown_called is True
