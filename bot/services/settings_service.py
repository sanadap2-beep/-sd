"""
إدارة الإعدادات (key-value) مع كاش بالذاكرة لتقليل الاستعلامات.
كل تعديل عبر set() يحدّث الكاش فوراً حتى تنعكس التغييرات بدون إعادة تشغيل البوت.
"""

from decimal import Decimal

from sqlalchemy import select

from database.models import Setting
from database.engine import async_session_maker


class SettingsService:
    _cache: dict[str, str] = {}
    _loaded: bool = False

    @classmethod
    async def _ensure_loaded(cls) -> None:
        if not cls._loaded:
            await cls.reload()

    @classmethod
    async def reload(cls) -> None:
        async with async_session_maker() as session:
            result = await session.execute(select(Setting))
            rows = result.scalars().all()
            cls._cache = {r.key: r.value for r in rows}
            cls._loaded = True

    @classmethod
    async def get(cls, key: str, default: str | None = None) -> str | None:
        await cls._ensure_loaded()
        return cls._cache.get(key, default)

    @classmethod
    async def get_decimal(cls, key: str, default: Decimal = Decimal("0")) -> Decimal:
        val = await cls.get(key)
        try:
            return Decimal(val) if val is not None else default
        except Exception:
            return default

    @classmethod
    async def get_bool(cls, key: str, default: bool = False) -> bool:
        val = await cls.get(key)
        if val is None:
            return default
        return val.lower() in ("true", "1", "yes")

    @classmethod
    async def get_int(cls, key: str, default: int = 0) -> int:
        val = await cls.get(key)
        try:
            return int(val) if val is not None else default
        except (ValueError, TypeError):
            return default

    @classmethod
    async def get_all(cls) -> dict[str, str]:
        await cls._ensure_loaded()
        return dict(cls._cache)

    @classmethod
    async def set(cls, session, key: str, value: str) -> None:
        setting = await session.get(Setting, key)
        if setting:
            setting.value = value
        else:
            setting = Setting(key=key, value=value)
            session.add(setting)
        await session.commit()
        cls._cache[key] = value

    @classmethod
    async def delete(cls, session, key: str) -> None:
        setting = await session.get(Setting, key)
        if setting:
            await session.delete(setting)
            await session.commit()
        cls._cache.pop(key, None)
