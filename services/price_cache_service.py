"""
كاش أسعار المزودين.

المشكلة التي يصلحها هذا الملف:
الكاش كان `dict` صنفياً في ذاكرة العملية، رغم أن Redis موجود أصلاً في
docker-compose.yml. النتيجة: كل worker لديه أسعار مختلفة، والكاش يضيع
كلياً عند إعادة التشغيل، ولا يمكن تشغيل أكثر من نسخة من البوت.

الحل: واجهة واحدة بنسختين.
- إن كان REDIS_URL مضبوطاً وتعمل الميزة `redis_cache`، يُستخدم Redis
  فيشارك كل الـ workers نفس الأسعار.
- وإلا يُستخدم كاش الذاكرة كما كان، فلا ينكسر أي تثبيت قائم.

الأهم: أي عطل في Redis لا يُسقط البيع. يُسجَّل الخطأ ويُعاد None
(أي «لا كاش») فيتابع المزود جلب السعر من مصدره الحقيقي.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from time import monotonic

from config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "bot:price:"


@dataclass
class _Entry:
    value: dict
    expires_at: float


class _MemoryBackend:
    """الكاش القديم في الذاكرة — يُستخدم حين لا يتوفر Redis."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> dict | None:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= monotonic():
                self._entries.pop(key, None)
                return None
            return dict(entry.value)

    async def set(self, key: str, value: dict, ttl: float) -> None:
        async with self._lock:
            self._entries[key] = _Entry(dict(value), monotonic() + max(1, ttl))

    async def delete_prefix(self, prefix: str | None) -> int:
        async with self._lock:
            keys = [
                key
                for key in self._entries
                if prefix is None or key.startswith(prefix)
            ]
            for key in keys:
                self._entries.pop(key, None)
            return len(keys)

    async def size(self) -> int:
        async with self._lock:
            return len(self._entries)


class _RedisBackend:
    """كاش Redis مشترك بين كل الـ workers."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client = None
        self._failed = False

    async def _connect(self):
        if self._client is not None or self._failed:
            return self._client
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                self._url, encoding="utf-8", decode_responses=True
            )
            await client.ping()
            self._client = client
            logger.info("كاش الأسعار متصل بـ Redis.")
            return self._client
        except Exception as exc:  # noqa: BLE001
            self._failed = True
            logger.warning(
                "تعذّر الاتصال بـ Redis، سيتم استخدام كاش الذاكرة: %s", exc
            )
            return None

    async def get(self, key: str) -> dict | None:
        client = await self._connect()
        if client is None:
            return None
        raw = await client.get(_KEY_PREFIX + key)
        if raw is None:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    async def set(self, key: str, value: dict, ttl: float) -> None:
        client = await self._connect()
        if client is None:
            return
        await client.set(
            _KEY_PREFIX + key,
            json.dumps(value, default=str),
            ex=int(max(1, ttl)),
        )

    async def delete_prefix(self, prefix: str | None) -> int:
        client = await self._connect()
        if client is None:
            return 0
        pattern = _KEY_PREFIX + (prefix or "*")
        removed = 0
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                removed += await client.delete(*keys)
            if cursor == 0:
                break
        return removed

    async def size(self) -> int:
        client = await self._connect()
        if client is None:
            return 0
        count = 0
        cursor = 0
        while True:
            cursor, keys = await client.scan(
                cursor=cursor, match=_KEY_PREFIX + "*", count=200
            )
            count += len(keys)
            if cursor == 0:
                break
        return count


class PriceCacheService:
    """
    واجهة الكاش. نفس الأسماء القديمة (get/set/invalidate/stats) حتى لا
    يتغير أي مستدعٍ موجود.

    القيم المخزنة قواميس {اسم المزود: السعر}. الأسعار Decimal، وتُحوَّل
    نصوصاً عند التخزين ثم تُعاد Decimal عند القراءة لأن الحسابات المالية
    في هذا المشروع Decimal حصراً ولا float أبداً.
    """

    _memory = _MemoryBackend()
    _redis: _RedisBackend | None = None
    hits = 0
    misses = 0

    @classmethod
    async def _backend(cls):
        """يختار Redis إن كان مضبوطاً والميزة مفعّلة، وإلا الذاكرة."""
        if cls._redis is not None:
            return cls._redis
        if not settings.REDIS_URL:
            return cls._memory
        # نتجنب استيراد FeatureService على مستوى الوحدة لأن ذلك يُنشئ
        # دورة استيراد (feature_service -> database.engine -> config).
        try:
            from services.feature_service import FeatureService

            if not await FeatureService.enabled("redis_cache"):
                return cls._memory
        except Exception:  # noqa: BLE001
            return cls._memory
        cls._redis = _RedisBackend(settings.REDIS_URL)
        return cls._redis

    @staticmethod
    def _normalize(value: dict) -> dict:
        """
        يحوّل القاموس لصيغة JSON آمنة.

        المفاتيح في provider_manager هي ProviderName، وهو `str` enum، أي أن
        `str(ProviderName.FIVESIM)` يعطي "ProviderName.FIVESIM" بينما القيمة
        الحقيقية هي "fivesim". لذا نستخدم `.value` عند وجودها، وإلا لضاع
        التطابق بعد القراءة وعاد الكاش بلا فائدة.
        """
        out = {}
        for key, price in (value or {}).items():
            name = getattr(key, "value", key)
            out[str(name)] = str(price)
        return out

    @staticmethod
    def _restore(raw: dict) -> dict:
        """
        يعيد الأسعار Decimal والمفاتيح ProviderName كما يتوقعها المستدعي.

        provider_manager يستخدم المفاتيح للوصول إلى `self._providers[...]`،
        فإرجاعها كنصوص خام يعمل فقط لأن ProviderName من نوع str — لكننا
        نعيدها enum صراحةً حتى لا يعتمد السلوك على هذه المصادفة.
        """
        from decimal import Decimal, InvalidOperation

        from database.models import ProviderName

        out: dict = {}
        for key, value in (raw or {}).items():
            try:
                price = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                continue
            try:
                out[ProviderName(key)] = price
            except ValueError:
                out[key] = price
        return out

    @classmethod
    async def ttl_seconds(cls) -> int:
        """مدة صلاحية السعر بالكاش — يضبطها الأدمن من مركز الإضافات."""
        try:
            from services.feature_service import FeatureService

            ttl = await FeatureService.config_int("redis_cache", "price_ttl_seconds", 20)
        except Exception:  # noqa: BLE001
            ttl = 20
        return max(1, ttl)

    @classmethod
    async def get(cls, key: str) -> dict | None:
        backend = await cls._backend()
        try:
            raw = await backend.get(key)
        except Exception as exc:  # noqa: BLE001
            logger.debug("فشل قراءة الكاش %s: %s", key, exc)
            cls.misses += 1
            return None
        if raw is None:
            cls.misses += 1
            return None
        cls.hits += 1
        return cls._restore(raw)

    @classmethod
    async def set(cls, key: str, value: dict, ttl: float = 20) -> None:
        backend = await cls._backend()
        try:
            await backend.set(key, cls._normalize(value), ttl)
        except Exception as exc:  # noqa: BLE001
            logger.debug("فشل كتابة الكاش %s: %s", key, exc)

    @classmethod
    async def invalidate(cls, prefix: str | None = None) -> int:
        backend = await cls._backend()
        try:
            removed = await backend.delete_prefix(prefix)
        except Exception as exc:  # noqa: BLE001
            logger.debug("فشل مسح الكاش %s: %s", prefix, exc)
            removed = 0
        # نمسح الذاكرة أيضاً حتى لا يبقى نسخة قديمة عند التبديل
        if backend is not cls._memory:
            removed += await cls._memory.delete_prefix(prefix)
        return removed

    @classmethod
    async def stats(cls) -> dict[str, int]:
        backend = await cls._backend()
        try:
            size = await backend.size()
        except Exception:  # noqa: BLE001
            size = 0
        return {
            "size": size,
            "hits": cls.hits,
            "misses": cls.misses,
            "backend": 1 if backend is cls._redis else 0,  # 1=redis 0=memory
        }
