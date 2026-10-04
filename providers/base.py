"""
الواجهة الأساسية لكل مزودي الأرقام.
العملة الداخلية: دولار أمريكي (USD).
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)


async def with_retry(func, *, attempts: int = 3, base_delay: float = 2.0, transient_only: bool = True):
    """إعادة محاولة موحدة لكل المزودين: 429/503/timeout فقط مع backoff."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            transient = (
                "429" in msg or "503" in msg or "timeout" in msg
                or "connection" in msg or "temporar" in msg
            )
            if transient_only and not transient:
                raise
            if attempt >= attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning("إعادة محاولة المزود (%s/%s) بعد %ss: %s", attempt, attempts, delay, exc)
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("with_retry: no attempts")


@dataclass
class PurchasedNumber:
    provider_order_id: str
    phone_number: str
    cost_usd: Decimal
    raw: dict


@dataclass
class OrderStatusResult:
    status: str
    sms_code: str | None
    full_text: str | None
    raw: dict


class BaseProvider(ABC):
    name: str

    @abstractmethod
    async def get_balance(self) -> Decimal:
        """يجلب رصيد الحساب لدى المزود بالدولار."""
        ...

    @abstractmethod
    async def get_price(self, country: str, service: str) -> Decimal | None:
        """
        يجلب سعر التكلفة بالدولار.
        يرجع None إذا لم تكن الخدمة متاحة.
        """
        ...

    @abstractmethod
    async def buy_number(
        self,
        country: str,
        service: str,
        operator: str | None = None,
        max_price: Decimal | None = None,
    ) -> PurchasedNumber:
        """يشتري رقماً ويرجع بيانات الشراء.

        max_price اختياري: سقف التكلفة بالدولار لحماية هامش الربح.
        """
        ...

    @abstractmethod
    async def check_status(self, order_id: str) -> OrderStatusResult:
        """يتحقق من حالة الطلب ويرجع الكود إذا وصل."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """يلغي الطلب ويرجع True إذا نجح."""
        ...

    @abstractmethod
    async def finish_order(self, order_id: str) -> bool:
        """يُنهي الطلب بعد استلام الكود."""
        ...

    @abstractmethod
    async def get_countries_services(self) -> list[dict]:
        """يجلب قائمة الدول والخدمات المتاحة."""
        ...