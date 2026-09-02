"""
الفئة الأساسية لكل بروتوكولات المزودين.

كل بروتوكول جديد (SMM V2, Games, Custom, إلخ)
يجب أن يرث من BaseProtocol ويطبق الدوال المطلوبة.

هذا يضمن واجهة موحدة للتعامل مع أي مزود.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal


# ══════════════ Data Classes ══════════════


@dataclass
class ProtocolBalance:
    """رصيد المزود."""

    amount: Decimal
    currency: str
    raw: dict = field(default_factory=dict)


@dataclass
class ProtocolService:
    """
    خدمة مسحوبة من المزود.
    كل مزود يعيد خدماته بنفس هذا الشكل الموحد.
    """

    external_id: str
    name: str
    category: str | None = None
    service_type: str | None = None
    rate: Decimal = Decimal("0")
    min_quantity: int = 1
    max_quantity: int = 1000000
    description: str | None = None
    requires_link: bool = True
    requires_quantity: bool = True
    requires_player_id: bool = False
    supports_refill: bool = False
    supports_cancel: bool = False
    raw: dict = field(default_factory=dict)


@dataclass
class ProtocolOrder:
    """طلب مرسل للمزود."""

    external_order_id: str
    status: str = "pending"
    charge: Decimal | None = None
    remains: int | None = None
    start_count: int | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ProtocolOrderStatus:
    """حالة طلب من المزود."""

    external_order_id: str
    status: str
    charge: Decimal | None = None
    remains: int | None = None
    start_count: int | None = None
    raw: dict = field(default_factory=dict)


# ══════════════ Exceptions ══════════════


class ProtocolError(Exception):
    """خطأ عام في البروتوكول."""

    pass


class ProtocolAuthError(ProtocolError):
    """خطأ في المصادقة مع المزود."""

    pass


class ProtocolConnectionError(ProtocolError):
    """خطأ في الاتصال بالمزود."""

    pass


class ProtocolInsufficientFundsError(ProtocolError):
    """الرصيد غير كافٍ عند المزود."""

    pass


class ProtocolInvalidServiceError(ProtocolError):
    """الخدمة المطلوبة غير موجودة."""

    pass


def is_insufficient_funds_error(error: object) -> bool:
    """Detect provider-side 'not enough funds' messages across SMM panels."""
    text = str(error or "").lower().replace("_", " ").replace("-", " ")
    needles = (
        "insufficient",
        "not enough fund",
        "not enough balance",
        "no enough fund",
        "balance is too low",
        "low balance",
        "out of fund",
        "not enough money",
        "not enough credit",
    )
    return any(needle in text for needle in needles)


def is_invalid_service_error(error: object) -> bool:
    text = str(error or "").lower().replace("_", " ").replace("-", " ")
    return "service" in text and (
        "not found" in text or "invalid" in text or "disabled" in text
    )


# ══════════════ Order Status Mapping ══════════════

ORDER_STATUS_MAPPING = {
    # حالات مكتملة
    "completed": "completed",
    "complete": "completed",
    "success": "completed",
    "done": "completed",
    "finished": "completed",
    "ok": "completed",
    # حالات فاشلة
    "failed": "failed",
    "fail": "failed",
    "error": "failed",
    "canceled": "failed",
    "cancelled": "failed",
    "rejected": "failed",
    # حالات قيد المعالجة
    "processing": "processing",
    "in_progress": "processing",
    "inprogress": "processing",
    "in progress": "processing",
    "pending": "pending",
    "waiting": "pending",
    "queued": "pending",
    # حالات جزئية
    "partial": "partial",
    "partial_complete": "partial",
    # حالات استرجاع
    "refunded": "refunded",
    "refund": "refunded",
}


def normalize_order_status(status: str) -> str:
    """يحول حالة الطلب من أي مزود إلى الحالات المعيارية."""
    if not status:
        return "pending"
    normalized = status.lower().strip().replace("_", " ")
    return ORDER_STATUS_MAPPING.get(
        normalized,
        ORDER_STATUS_MAPPING.get(status.lower(), "pending"),
    )


# ══════════════ Base Protocol ══════════════


class BaseProtocol(ABC):
    """
    الفئة الأساسية لكل بروتوكولات المزودين.

    كل بروتوكول جديد يجب أن يرث من هذه الفئة
    ويطبق كل الدوال المجرّدة (abstract methods).
    """

    name: str = "base"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        custom_config: dict | None = None,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.custom_config = custom_config or {}

    @abstractmethod
    async def test_connection(self) -> bool:
        """
        يختبر الاتصال بالمزود.
        يرجع True إذا نجح، False إذا فشل.
        """
        pass

    @abstractmethod
    async def get_balance(self) -> ProtocolBalance:
        """
        يجلب رصيد الحساب لدى المزود.
        """
        pass

    @abstractmethod
    async def get_services(self) -> list[ProtocolService]:
        """
        يجلب كل الخدمات المتاحة لدى المزود.
        """
        pass

    @abstractmethod
    async def place_order(
        self,
        service_id: str,
        target: str,
        quantity: int,
        extra_params: dict | None = None,
    ) -> ProtocolOrder:
        """
        يرسل طلباً جديداً للمزود.

        service_id: آيدي الخدمة عند المزود
        target: الهدف (رابط أو Player ID)
        quantity: الكمية
        extra_params: معاملات إضافية اختيارية
        """
        pass

    @abstractmethod
    async def check_order_status(self, external_order_id: str) -> ProtocolOrderStatus:
        """
        يفحص حالة طلب معين.
        """
        pass

    async def check_multiple_orders(self, order_ids: list[str]) -> dict[str, ProtocolOrderStatus]:
        """
        يفحص حالة عدة طلبات دفعة واحدة.
        الافتراضي: يستدعي check_order_status لكل واحد.
        يمكن للبروتوكولات إعادة تعريفه لأداء أفضل.
        """
        results = {}
        for order_id in order_ids:
            try:
                status = await self.check_order_status(order_id)
                results[order_id] = status
            except Exception:
                pass
        return results

    async def cancel_order(self, external_order_id: str) -> bool:
        """
        يلغي طلباً معيناً.
        الافتراضي: غير مدعوم (يرجع False).
        """
        return False

    async def refill_order(self, external_order_id: str) -> bool:
        """
        يعيد ملء طلب معين (إذا نقص).
        الافتراضي: غير مدعوم (يرجع False).
        """
        return False
