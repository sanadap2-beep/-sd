"""
الواجهة الأساسية لكل مزودي الأرقام.
العملة الداخلية: دولار أمريكي (USD).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


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