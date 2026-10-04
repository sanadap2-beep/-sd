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
    # إغلاق مسبح الاتصالات قبل حذف الملف: وصلات aiosqlite المفتوحة
    # تشير للـ inode المحذوف فيتسرب اختبار لآخر (UNIQUE زائفة، I/O errors).
    try:
        from database.engine import engine

        await engine.dispose()
    except Exception:
        pass
    database_path = Path("/tmp/number-bot-pytest.db")
    if database_path.exists():
        database_path.unlink()
    await init_db()
    # إعادة تعيين الكاشات الذاكرة حتى لا تسرّب قيم اختبار لاختبار تالٍ
    # (كل قاعدة بيانات جديدة = إعدادات/ميزات جديدة).
    try:
        from services.settings_service import SettingsService
        from services.feature_service import FeatureService

        SettingsService._cache = {}
        SettingsService._loaded = False
        FeatureService._cache = {}
        FeatureService._config_cache = {}
        FeatureService._loaded = False
    except Exception:
        pass
    try:
        # أقفال العمليات والبوتات المخبأة مرتبطة بحلقات أحداث انتهت.
        from services.balance_service import BalanceService
        from services.operation_lock_service import OperationLockService

        BalanceService._locks.clear()
        try:
            OperationLockService._locks.clear()
        except AttributeError:
            pass
    except Exception:
        pass
    try:
        from services import tenant_runtime

        for _tid, _bot in list(getattr(tenant_runtime, "_tenant_bots", {}).items()):
            try:
                await _bot.session.close()
            except Exception:
                pass
        tenant_runtime._tenant_bots.clear()
        tenant_runtime._tenant_hits.clear()
    except Exception:
        pass
    try:
        from api.app import InMemoryRateLimitMiddleware

        InMemoryRateLimitMiddleware._hits.clear()
    except Exception:
        pass
    yield
    try:
        from database.engine import engine as _engine

        await _engine.dispose()
    except Exception:
        pass
