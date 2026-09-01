"""
خدمة أعلام الميزات.

مصدر الحقيقة هو جدول feature_flags، والسجل المرجعي للقيم الافتراضية
في services/feature_registry.py. الكاش بالذاكرة ويُحدَّث فور أي تعديل
حتى ينعكس التغيير على البوت بدون إعادة تشغيل.

الاستخدام في أي مكان في البوت:

    if await FeatureService.enabled("bulk_numbers"):
        ...

    limit = await FeatureService.config("bulk_numbers", "max_quantity", 100)

وكل استدعاء لميزة مسجَّل في FeatureEvent حتى يعرف الأدمن أيها مستعمل.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from database.engine import async_session_maker
from database.models import FeatureEvent, FeatureFlag
from services.feature_registry import BY_KEY, FeatureSpec

logger = logging.getLogger(__name__)


class FeatureService:
    _cache: dict[str, bool] = {}
    _config_cache: dict[str, dict] = {}
    _loaded: bool = False

    # ─────────── التحميل ───────────

    @classmethod
    async def reload(cls) -> None:
        """يقرأ التجاوزات المحفوظة من قاعدة البيانات."""
        try:
            async with async_session_maker() as session:
                result = await session.execute(select(FeatureFlag))
                rows = result.scalars().all()
        except Exception as exc:  # قاعدة البيانات غير مهيأة بعد
            logger.warning("تعذّر تحميل أعلام الميزات: %s", exc)
            cls._loaded = True
            return

        cls._cache = {row.key: bool(row.enabled) for row in rows}
        cls._config_cache = {}
        for row in rows:
            if row.config_json:
                try:
                    parsed = json.loads(row.config_json)
                    if isinstance(parsed, dict):
                        cls._config_cache[row.key] = parsed
                except (TypeError, ValueError):
                    logger.warning("إعداد غير صالح للميزة %s", row.key)
        cls._loaded = True

    @classmethod
    async def _ensure_loaded(cls) -> None:
        if not cls._loaded:
            await cls.reload()

    @classmethod
    async def sync_registry(cls) -> int:
        """
        يضيف أي ميزة جديدة في السجل إلى قاعدة البيانات بقيمتها الافتراضية.
        يُنادى عند بدء التشغيل وبعد أي إضافة ميزة جديدة.
        """
        created = 0
        try:
            async with async_session_maker() as session:
                existing = set(
                    (await session.execute(select(FeatureFlag.key))).scalars().all()
                )
                for spec in BY_KEY.values():
                    if spec.key not in existing:
                        session.add(
                            FeatureFlag(
                                key=spec.key,
                                enabled=spec.default_enabled,
                                config_json=json.dumps(spec.defaults, ensure_ascii=False),
                            )
                        )
                        created += 1
                if created:
                    await session.commit()
        except Exception as exc:
            logger.warning("تعذّرت مزامنة سجل الميزات: %s", exc)
        if created:
            await cls.reload()
            logger.info("تم تسجيل %s ميزة جديدة في لوحة الأدمن.", created)
        return created

    # ─────────── القراءة ───────────

    @classmethod
    def _spec(cls, key: str) -> FeatureSpec | None:
        return BY_KEY.get(key)

    @classmethod
    async def enabled(cls, key: str, default: bool | None = None) -> bool:
        """هل الميزة مفعّلة؟ الميزة غير المسجلة تعتبر معطّلة."""
        await cls._ensure_loaded()
        spec = cls._spec(key)
        if spec is None:
            logger.warning("استعلام عن ميزة غير مسجلة: %s", key)
            return bool(default) if default is not None else False
        if key in cls._cache:
            return cls._cache[key]
        return spec.default_enabled

    @classmethod
    async def config(cls, key: str, option: str, default: Any = None) -> Any:
        """قيمة إعداد لميزة، مع الرجوع للافتراضي في السجل."""
        await cls._ensure_loaded()
        if key in cls._config_cache and option in cls._config_cache[key]:
            return cls._config_cache[key][option]
        spec = cls._spec(key)
        if spec is not None and option in spec.defaults:
            return spec.defaults[option]
        return default

    @classmethod
    async def config_int(cls, key: str, option: str, default: int = 0) -> int:
        try:
            return int(await cls.config(key, option, default))
        except (TypeError, ValueError):
            return default

    @classmethod
    async def config_bool(cls, key: str, option: str, default: bool = False) -> bool:
        value = await cls.config(key, option, default)
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes")

    @classmethod
    async def config_decimal(cls, key: str, option: str, default: float = 0.0) -> float:
        from decimal import Decimal, InvalidOperation

        try:
            return float(Decimal(str(await cls.config(key, option, default))))
        except (InvalidOperation, TypeError, ValueError):
            return float(default)

    @classmethod
    async def config_json(cls, key: str, option: str, default: list | dict) -> Any:
        """إعداد محفوظ كنص JSON (قوائم المستويات والخطط)."""
        raw = await cls.config(key, option, None)
        if raw is None:
            return default
        if isinstance(raw, (list, dict)):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    @classmethod
    async def full_config(cls, key: str) -> dict:
        await cls._ensure_loaded()
        spec = cls._spec(key)
        base = dict(spec.defaults) if spec else {}
        base.update(cls._config_cache.get(key, {}))
        return base

    # ─────────── الكتابة (من لوحة الأدمن) ───────────

    @classmethod
    async def set_enabled(cls, session, key: str, enabled: bool) -> bool:
        if cls._spec(key) is None:
            return False
        flag = await session.get(FeatureFlag, key)
        if flag is None:
            flag = FeatureFlag(
                key=key,
                enabled=enabled,
                config_json=json.dumps(
                    cls._spec(key).defaults, ensure_ascii=False  # type: ignore[union-attr]
                ),
            )
            session.add(flag)
        else:
            flag.enabled = enabled
        await session.commit()
        cls._cache[key] = enabled
        return True

    @classmethod
    async def set_option(cls, session, key: str, option: str, value: Any) -> bool:
        spec = cls._spec(key)
        if spec is None:
            return False
        flag = await session.get(FeatureFlag, key)
        if flag is None:
            flag = FeatureFlag(key=key, enabled=spec.default_enabled, config_json=None)
            session.add(flag)
            await session.flush()

        current = {}
        if flag.config_json:
            try:
                parsed = json.loads(flag.config_json)
                if isinstance(parsed, dict):
                    current = parsed
            except (TypeError, ValueError):
                current = {}

        # نحافظ على نوع القيمة الافتراضي حتى لا ينكسر المنطق المعتمد عليها.
        typed = cls._coerce(value, spec.defaults.get(option))
        current[option] = typed
        flag.config_json = json.dumps(current, ensure_ascii=False)
        await session.commit()

        cls._config_cache.setdefault(key, {})[option] = typed
        return True

    @staticmethod
    def _coerce(value: Any, reference: Any) -> Any:
        if reference is None:
            return value
        if isinstance(reference, bool):
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "1", "yes")
        if isinstance(reference, int) and not isinstance(reference, bool):
            try:
                return int(value)
            except (TypeError, ValueError):
                return reference
        if isinstance(reference, float):
            try:
                return float(value)
            except (TypeError, ValueError):
                return reference
        if isinstance(reference, (list, dict)):
            if isinstance(value, (list, dict)):
                return value
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, type(reference)) else reference
            except (TypeError, ValueError):
                return reference
        return str(value)

    @classmethod
    async def reset(cls, session, key: str) -> bool:
        """يعيد الميزة لقيمها الافتراضية في السجل."""
        spec = cls._spec(key)
        if spec is None:
            return False
        flag = await session.get(FeatureFlag, key)
        if flag is None:
            return False
        flag.enabled = spec.default_enabled
        flag.config_json = json.dumps(spec.defaults, ensure_ascii=False)
        await session.commit()
        cls._cache[key] = spec.default_enabled
        cls._config_cache[key] = dict(spec.defaults)
        return True

    # ─────────── القياس ───────────

    @classmethod
    async def track(
        cls,
        key: str,
        event_type: str,
        user_id: int | None = None,
        value: str | None = None,
    ) -> None:
        """يسجل استخدام ميزة. لا يفشل العملية الأساسية أبداً."""
        if not await cls.enabled("feature_usage_analytics"):
            return
        try:
            async with async_session_maker() as session:
                session.add(
                    FeatureEvent(
                        feature_key=key[:64],
                        event_type=event_type[:32],
                        user_id=user_id,
                        value=str(value)[:255] if value is not None else None,
                    )
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - القياس اختياري
            logger.debug("تعذّر تسجيل حدث الميزة %s: %s", key, exc)

    @classmethod
    async def usage_stats(cls, days: int = 30) -> dict[str, int]:
        """عدد الاستخدامات لكل ميزة خلال فترة، لعرضها في لوحة الأدمن."""
        since = datetime.utcnow() - timedelta(days=max(1, days))
        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    select(FeatureEvent.feature_key, func.count(FeatureEvent.id))
                    .where(FeatureEvent.created_at >= since)
                    .group_by(FeatureEvent.feature_key)
                )
                return {key: int(count) for key, count in result.all()}
        except Exception as exc:
            logger.debug("تعذّر حساب إحصاءات الميزات: %s", exc)
            return {}

    @classmethod
    async def prune_events(cls, retention_days: int = 90) -> int:
        cutoff = datetime.utcnow() - timedelta(days=max(1, retention_days))
        try:
            async with async_session_maker() as session:
                result = await session.execute(
                    delete(FeatureEvent).where(FeatureEvent.created_at < cutoff)
                )
                await session.commit()
                return int(result.rowcount or 0)
        except Exception as exc:
            logger.debug("تعذّر تنظيف أحداث الميزات: %s", exc)
            return 0

    # ─────────── العرض ───────────

    @classmethod
    async def snapshot(cls) -> list[dict]:
        """كل الميزات بحالتها وإعداداتها، للوحة الأدمن وللواجهة البرمجية."""
        await cls._ensure_loaded()
        usage = await cls.usage_stats()
        out = []
        for spec in BY_KEY.values():
            out.append(
                {
                    "key": spec.key,
                    "name_ar": spec.name_ar,
                    "name_en": spec.name_en,
                    "category": spec.category_ar,
                    "description": spec.desc_ar,
                    "enabled": await cls.enabled(spec.key),
                    "config": await cls.full_config(spec.key),
                    "usage_30d": usage.get(spec.key, 0),
                }
            )
        return out
