from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import Fernet

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_FOR_TESTS_1234567890")
os.environ.setdefault("BOT_USERNAME", "test_bot")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("ADMIN_NOTIFY_CHAT_ID", "-1001")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/number-bot-pytest.db")
os.environ.setdefault("INVENTORY_ENCRYPTION_KEY", Fernet.generate_key().decode())

import pytest_asyncio

from database.seed import init_db


@pytest_asyncio.fixture(autouse=True)
async def fresh_database():
    database_path = Path("/tmp/number-bot-pytest.db")
    if database_path.exists():
        database_path.unlink()
    await init_db()
    yield
