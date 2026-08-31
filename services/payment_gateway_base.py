"""Common contract for automatic payment gateways."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal


class PaymentGatewayError(Exception):
    pass


class PaymentGatewayBase(ABC):
    name = "gateway"

    @abstractmethod
    async def create_payment(self, amount: Decimal, order_id: str, **kwargs) -> dict:
        """Create an invoice and return a normalized provider payload."""

    @abstractmethod
    async def get_payment_info(self, external_id: str) -> dict:
        """Return the current normalized payment state."""

    @abstractmethod
    def is_paid_status(self, status: str) -> bool:
        """Return True when a provider state is final and paid."""

    def is_failed_status(self, status: str) -> bool:
        return status.lower() in {"failed", "error", "expired", "cancelled", "cancel"}
