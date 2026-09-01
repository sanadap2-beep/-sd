"""Client for Plisio's automatic USDT invoices.

Plisio returns a small hosted-invoice response for regular (non-White-label)
accounts. In that mode the successful response normally contains only
``txn_id`` and ``invoice_url``. White-label accounts may additionally return a
wallet address, QR code, amount, and expiry timestamp. The rest of the
application must therefore treat the hosted invoice URL as the payment
surface, not require ``wallet_hash`` to be present.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import aiohttp

from config import settings
from services.payment_gateway_base import PaymentGatewayBase

logger = logging.getLogger(__name__)


class PlisioError(Exception):
    """Base error for Plisio integration."""


class PlisioConnectionError(PlisioError):
    """A request could not reach Plisio or timed out."""


class PlisioAPIError(PlisioError):
    """Plisio rejected a request or returned an invalid provider response."""

    def __init__(self, message: str, code: Any | None = None):
        self.code = code
        super().__init__(message)


def _redact(value: object, secret_key: str) -> str:
    """Remove the configured secret from text before logging or displaying it."""
    text = str(value or "")
    if secret_key:
        text = text.replace(secret_key, "[redacted]")
    return text


def _provider_error_message(payload: dict[str, Any], secret_key: str) -> tuple[str, Any | None]:
    """Extract Plisio's safe, human-readable API error fields."""
    details = payload.get("data")
    if isinstance(details, dict):
        message = details.get("message") or details.get("name") or "Unknown error"
        code = details.get("code")
    elif details:
        message = details
        code = None
    else:
        message = payload.get("message") or payload.get("error") or "Unknown error"
        code = payload.get("code")
    return _redact(message, secret_key)[:500], code


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_http_url(value: object) -> bool:
    url = _clean_text(value)
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _normalise_expiry(value: object) -> object | None:
    """Keep a provider expiry value usable by the handler (seconds or ISO)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError, OverflowError):
        return text


class PlisioClient(PaymentGatewayBase):
    """Async Plisio API client with the legacy normalized payment contract."""

    BASE_URL = "https://api.plisio.net/api/v1"

    PAID_STATUSES = {"completed", "mismatch"}
    FAILED_STATUSES = {
        "error",
        "expired",
        "cancelled",
        "cancelled duplicate",
        "cancelled_duplicate",
    }
    PENDING_STATUSES = {"new", "pending", "confirming", "pending internal", "pending_internal"}

    def __init__(self):
        self.secret_key = settings.PLISIO_SECRET_KEY
        self.base_url = settings.PLISIO_API_URL.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return a reusable HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def close(self):
        """Close the reusable HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Send a request and return Plisio's ``data`` payload.

        API messages are preserved for operator/user diagnostics, while the
        secret key is redacted from every error path. Provider errors are not
        accidentally wrapped as generic errors, so callers can distinguish an
        invalid key or rejected amount from a network outage.
        """
        url = f"{self.base_url}{endpoint}"
        request_params = dict(params or {})
        request_params["api_key"] = self.secret_key

        try:
            session = await self._get_session()
            if method.upper() == "GET":
                request = session.get(url, params=request_params)
            elif method.upper() == "POST":
                request = session.post(url, data=request_params)
            else:
                raise PlisioError(f"Unsupported HTTP method: {method}")

            async with request as response:
                try:
                    body = await response.text()
                except AttributeError:
                    # Keeps the client compatible with lightweight test doubles
                    # and older aiohttp wrappers that expose only json().
                    try:
                        payload = await response.json(content_type=None)
                    except TypeError:
                        payload = await response.json()
                else:
                    try:
                        payload = json.loads(body)
                    except (TypeError, json.JSONDecodeError) as exc:
                        logger.error("Plisio returned non-JSON response (HTTP %s)", response.status)
                        raise PlisioAPIError("استجابة Plisio غير صالحة") from exc

                if not isinstance(payload, dict):
                    raise PlisioAPIError("استجابة Plisio غير صالحة")

                provider_status = str(payload.get("status") or "").strip().lower()
                if response.status >= 400 or provider_status == "error":
                    message, code = _provider_error_message(payload, self.secret_key)
                    logger.error(
                        "Plisio API rejected %s %s (HTTP %s, code=%s): %s",
                        method.upper(),
                        endpoint,
                        response.status,
                        code,
                        message,
                    )
                    raise PlisioAPIError(message, code=code)

                if provider_status and provider_status != "success":
                    message, code = _provider_error_message(payload, self.secret_key)
                    logger.error(
                        "Unexpected Plisio status for %s %s: %s",
                        method.upper(),
                        endpoint,
                        provider_status,
                    )
                    raise PlisioAPIError(message, code=code)

                result = payload.get("data", {})
                if not isinstance(result, (dict, list)):
                    raise PlisioAPIError("استجابة Plisio لا تحتوي بيانات صالحة")
                return result

        except PlisioError:
            raise
        except aiohttp.ClientError as exc:
            safe_error = _redact(exc, self.secret_key)
            logger.error("Plisio connection error: %s", safe_error[:400])
            raise PlisioConnectionError("فشل الاتصال بـ Plisio") from exc
        except asyncio.TimeoutError as exc:
            logger.error("Plisio request timed out: %s %s", method.upper(), endpoint)
            raise PlisioConnectionError("انتهت مهلة الاتصال بـ Plisio") from exc
        except Exception as exc:  # noqa: BLE001 - convert unexpected client errors safely
            safe_error = _redact(exc, self.secret_key)
            logger.exception("Unexpected Plisio client error: %s", safe_error[:400])
            raise PlisioError("حدث خطأ غير متوقع أثناء الاتصال بـ Plisio") from exc

    async def create_payment(
        self,
        amount: Decimal | float | str,
        order_id: str,
        currency: str = "USDT_TRX",
        order_name: str | None = None,
        callback_url: str | None = None,
        email: str | None = None,
        lifetime: int = 1800,
    ) -> dict[str, Any]:
        """Create a hosted invoice and return the normalized payment payload.

        ``wallet_hash`` and all other White-label fields are optional. The
        normalized response is valid when ``uuid`` and ``url`` are present.
        """
        try:
            amount_decimal = Decimal(str(amount))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise PlisioError("مبلغ الفاتورة غير صالح") from exc
        if not amount_decimal.is_finite() or amount_decimal <= 0:
            raise PlisioError("مبلغ الفاتورة يجب أن يكون موجباً ومنتهياً")

        try:
            lifetime_seconds = int(lifetime)
        except (TypeError, ValueError):
            lifetime_seconds = 1800
        expire_minutes = max(1, math.ceil(lifetime_seconds / 60))

        params: dict[str, Any] = {
            "source_amount": str(amount_decimal),
            "source_currency": "USD",
            "order_number": order_id,
            "currency": currency,
            "expire_min": str(expire_minutes),
        }

        params["order_name"] = order_name or f"Deposit {order_id}"
        if callback_url:
            params["callback_url"] = callback_url
        if email:
            params["email"] = email

        result = await self._request("GET", "/invoices/new", params)
        if not isinstance(result, dict):
            raise PlisioAPIError("استجابة إنشاء فاتورة Plisio غير صالحة")

        external_id = _clean_text(result.get("txn_id") or result.get("id"))
        payment_url = _clean_text(
            result.get("invoice_url") or result.get("url") or result.get("payment_url")
        )
        if not external_id or not payment_url or not _is_http_url(payment_url):
            logger.error(
                "Plisio invoice response missing a valid txn_id or invoice_url: id=%s url=%s",
                bool(external_id),
                bool(payment_url),
            )
            raise PlisioAPIError("استجابة إنشاء فاتورة Plisio لا تحتوي معرفاً ورابط دفع صالحين")

        payer_amount = _clean_text(result.get("amount") or result.get("invoice_total_sum"))
        return {
            "uuid": external_id,
            # Optional on regular hosted invoices; never use its absence as a
            # reason to reject a successful invoice.
            "address": _clean_text(result.get("wallet_hash") or result.get("address")),
            "amount": payer_amount,
            "payer_amount": payer_amount,
            "payer_amount_known": payer_amount is not None,
            "currency": _clean_text(result.get("currency") or currency),
            "network": self._get_network_name(currency),
            "url": payment_url,
            "qr_code": result.get("qr_code"),
            "expired_at": _normalise_expiry(
                result.get("expire_utc")
                or result.get("expire_at_utc")
                or result.get("expires_at")
            ),
            "raw": result,
        }

    async def get_payment_info(self, uuid: str) -> dict[str, Any]:
        """Fetch and normalize an invoice operation's current state."""
        external_id = _clean_text(uuid)
        if not external_id:
            raise PlisioError("معرف فاتورة Plisio غير صالح")

        result = await self._request("GET", f"/operations/{external_id}")
        if not isinstance(result, dict):
            raise PlisioAPIError("استجابة فحص فاتورة Plisio غير صالحة")

        raw_status = str(result.get("status") or "new").strip().lower()
        status_map = {
            "completed": "paid",
            "mismatch": "paid",
            "new": "process",
            "pending": "process",
            "confirming": "check",
            "pending internal": "process",
            "pending_internal": "process",
            "expired": "cancel",
            "cancelled": "cancel",
            "cancelled duplicate": "cancel",
            "cancelled_duplicate": "cancel",
            "error": "fail",
        }
        mapped_status = status_map.get(raw_status, raw_status or "process")

        return {
            "status": mapped_status,
            "uuid": _clean_text(result.get("id") or result.get("txn_id")) or external_id,
            "amount": result.get("amount") or result.get("actual_sum"),
            "address": _clean_text(result.get("wallet_hash") or result.get("address")),
            "confirmations": result.get("confirmations", 0),
            "raw": result,
        }

    def is_paid_status(self, status: str) -> bool:
        """Return whether a normalized or raw Plisio status is paid."""
        return str(status or "").strip().lower() in {
            "paid",
            "completed",
            "mismatch",
        }

    def is_failed_status(self, status: str) -> bool:
        """Return whether a normalized or raw Plisio status is final failure."""
        return str(status or "").strip().lower() in {
            "fail",
            "error",
            "cancel",
            "cancelled",
            "cancelled duplicate",
            "cancelled_duplicate",
            "expired",
        }

    def is_pending_status(self, status: str) -> bool:
        """Return whether a normalized or raw Plisio status is still pending."""
        return str(status or "").strip().lower() in {
            "process",
            "check",
            "new",
            "pending",
            "confirming",
            "pending internal",
            "pending_internal",
        }

    def _get_network_name(self, currency: str) -> str:
        """Convert a Plisio currency code into a display network name."""
        network_map = {
            "USDT_TRX": "TRC20",
            "USDT_BSC": "BEP20",
            "USDT_ETH": "ERC20",
            "BNB": "BEP20",
            "BTC": "BTC",
            "ETH": "ERC20",
        }
        return network_map.get(currency, currency)

    async def get_supported_currencies(self) -> list[Any]:
        """Fetch supported currencies; an unavailable list is non-fatal."""
        try:
            result = await self._request("GET", "/currencies")
            return result if isinstance(result, list) else []
        except PlisioError as exc:
            logger.error("فشل جلب العملات من Plisio: %s", str(exc)[:400])
            return []

    async def get_balance(self, currency: str = "USDT_TRX") -> Decimal:
        """Fetch the Plisio balance, propagating provider errors to health checks."""
        result = await self._request("GET", "/balances", {"currency": currency})
        if not isinstance(result, dict):
            raise PlisioAPIError("استجابة رصيد Plisio غير صالحة")
        balance = result.get("balance", "0")
        try:
            parsed = Decimal(str(balance))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise PlisioAPIError("رصيد Plisio غير صالح") from exc
        if not parsed.is_finite():
            raise PlisioAPIError("رصيد Plisio غير صالح")
        return parsed


plisio_client = PlisioClient()

# Backwards-compatible names used by older modules.
cryptomus_client = plisio_client
CryptomusError = PlisioError
