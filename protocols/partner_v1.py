"""Protocol for the friend-panel REST API (tlbkenne / Partner V1).

Envelope: ``{"ok": true, "data": ...}`` or ``{"ok": false, "error": "...", "code": "..."}``.
Auth: ``Authorization: Bearer pak_...``.

Phone numbers are created with ``POST /orders`` then collected via
``POST /orders/{id}/code``. SMM/AI services may also send link/quantity.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from urllib.parse import urlencode

import aiohttp

from protocols.base import (
    BaseProtocol,
    ProtocolAuthError,
    ProtocolBalance,
    ProtocolConnectionError,
    ProtocolError,
    ProtocolInsufficientFundsError,
    ProtocolInvalidServiceError,
    ProtocolOrder,
    ProtocolOrderStatus,
    ProtocolService,
    is_insufficient_funds_error,
    is_invalid_service_error,
    normalize_order_status,
)

logger = logging.getLogger(__name__)

ENGINE_KEYS = ("partner_v1", "tlbkenne")
DEFAULT_API_URL = "http://169.58.216.253:8888/api/v1"

PARTNER_TYPES = (
    ("tg", "✈️", "أرقام تيليجرام"),
    ("wa1", "📱", "واتساب سيرفر 1"),
    ("wa2", "📲", "واتساب سيرفر 2"),
    ("ai", "🤖", "ذكاء اصطناعي"),
    ("smf", "📈", "خدمات الرشق"),
    ("ext", "🔗", "خدمات خارجية"),
)

PHONE_TYPES = {"tg", "wa1", "wa2"}
PHONE_CATEGORIES = {"phone_number", "phone", "number", "numbers"}
SMM_TYPES = {"smf"}
SMM_CATEGORIES = {"followers", "smm", "subscription"}


def is_partner_v1_config(custom_config: dict | None) -> bool:
    if not custom_config:
        return False
    engine = str(custom_config.get("engine") or custom_config.get("preset") or "").lower()
    return engine in ENGINE_KEYS


def is_partner_v1_provider(provider) -> bool:
    raw = getattr(provider, "custom_config", None)
    if not raw:
        return False
    if isinstance(raw, dict):
        return is_partner_v1_config(raw)
    try:
        return is_partner_v1_config(json.loads(raw))
    except (TypeError, ValueError):
        return False


def partner_type_meta(type_key: str) -> tuple[str, str]:
    for key, emoji, label in PARTNER_TYPES:
        if key == type_key:
            return emoji, label
    return "📦", type_key or "أخرى"


class PartnerV1Protocol(BaseProtocol):
    name = "partner_v1"

    def __init__(self, api_url: str, api_key: str, custom_config: dict | None = None):
        super().__init__(api_url or DEFAULT_API_URL, api_key, custom_config)
        self.timeout = int((custom_config or {}).get("timeout", 30))

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.api_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def unwrap(result):
        if not isinstance(result, dict):
            return result
        if result.get("ok") is False:
            code = str(result.get("code") or "")
            error = str(result.get("error") or code or "provider error")
            blob = f"{code} {error}"
            if code in {"UNAUTHORIZED", "INVALID_KEY", "KEY_DISABLED"}:
                raise ProtocolAuthError(error)
            if code == "INSUFFICIENT_BALANCE" or is_insufficient_funds_error(blob):
                raise ProtocolInsufficientFundsError(error)
            if code in {"INVALID_SERVICE", "SERVICE_UNAVAILABLE"} or is_invalid_service_error(blob):
                raise ProtocolInvalidServiceError(error)
            raise ProtocolError(f"{code}: {error}" if code else error)
        if "data" in result:
            return result.get("data")
        return result

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        params: dict | None = None,
    ):
        url = self._url(path)
        if params:
            url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
        try:
            async with aiohttp.ClientSession(
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as session:
                kwargs = {}
                if payload is not None and method.upper() != "GET":
                    kwargs["json"] = payload
                async with session.request(method.upper(), url, **kwargs) as response:
                    body = await response.text()
                    if response.status in (401, 403):
                        raise ProtocolAuthError(f"مفتاح API مرفوض: HTTP {response.status}")
                    try:
                        parsed = json.loads(body) if body else {}
                    except json.JSONDecodeError as exc:
                        raise ProtocolError(f"استجابة غير JSON: {body[:200]}") from exc
                    if response.status not in (200, 201, 202):
                        try:
                            self.unwrap(parsed)
                        except ProtocolError:
                            raise
                        raise ProtocolConnectionError(
                            f"HTTP {response.status}: {body[:200]}"
                        )
                    return self.unwrap(parsed)
        except ProtocolError:
            raise
        except TimeoutError as exc:
            raise ProtocolConnectionError("انتهت مهلة الاتصال بمزود الصديق") from exc
        except aiohttp.ClientError as exc:
            raise ProtocolConnectionError(f"تعذر الاتصال بمزود الصديق: {exc}") from exc

    async def test_connection(self) -> bool:
        await self._request("GET", "/ping")
        return True

    async def get_balance(self) -> ProtocolBalance:
        data = await self._request("GET", "/balance")
        if not isinstance(data, dict):
            raise ProtocolError("رصيد المزود ليس كائناً")
        raw = data.get("balance", data.get("amount", 0))
        try:
            amount = Decimal(str(raw))
        except Exception as exc:
            raise ProtocolError("قيمة الرصيد غير صالحة") from exc
        return ProtocolBalance(
            amount=amount,
            currency=str(data.get("currency") or "USD"),
            raw=data if isinstance(data, dict) else {"data": data},
        )

    async def get_services(
        self,
        service_type: str | None = None,
        category: str | None = None,
    ) -> list[ProtocolService]:
        params = {}
        if service_type:
            params["type"] = service_type
        if category:
            params["category"] = category
        data = await self._request("GET", "/services", params=params or None)
        items = _as_list(data)
        return [svc for item in items if (svc := self._parse_service(item))]

    async def get_service(self, service_id: str | int) -> ProtocolService | None:
        try:
            data = await self._request("GET", f"/services/{service_id}")
        except ProtocolError:
            logger.warning("تعذر جلب الخدمة %s من مزود الصديق", service_id)
            return None
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return None
        return self._parse_service(data)

    def _parse_service(self, item: dict) -> ProtocolService | None:
        if not isinstance(item, dict):
            return None
        external_id = item.get("service_id", item.get("id"))
        if external_id is None:
            return None
        rate = item.get("price", item.get("rate", item.get("cost", 0)))
        try:
            rate = Decimal(str(rate))
        except Exception:
            rate = Decimal("0")
        type_key = str(item.get("type") or item.get("service_type") or "").lower()
        category = str(item.get("category") or "")
        requires_link, requires_quantity = _infer_requirements(item, type_key, category)
        name = str(item.get("name") or item.get("name_ar") or item.get("title") or f"خدمة {external_id}")
        return ProtocolService(
            external_id=str(external_id),
            name=name,
            category=category or None,
            service_type=type_key or None,
            rate=rate,
            min_quantity=_as_int(item.get("min", item.get("min_quantity", 1)), 1),
            max_quantity=_as_int(item.get("max", item.get("max_quantity", 1)), 1),
            description=item.get("description") or item.get("country") or None,
            requires_link=requires_link,
            requires_quantity=requires_quantity,
            requires_player_id=False,
            supports_refill=bool(item.get("refill") or item.get("supports_refill")),
            supports_cancel=True,
            raw=item,
        )

    async def place_order(
        self,
        service_id: str,
        target: str,
        quantity: int,
        extra_params: dict | None = None,
    ) -> ProtocolOrder:
        payload: dict = {}
        if str(service_id).isdigit():
            payload["service_id"] = int(service_id)
        else:
            payload["service_id"] = service_id
        if target:
            payload["link"] = target
        if quantity and int(quantity) > 1:
            payload["quantity"] = int(quantity)
        if extra_params:
            payload.update(extra_params)
        data = await self._request("POST", "/orders", payload=payload)
        if not isinstance(data, dict):
            raise ProtocolError("المزود لم يرجع بيانات الطلب")
        order_id = data.get("order_id", data.get("id"))
        if order_id is None:
            raise ProtocolError(f"المزود لم يرجع order_id: {data}")
        return ProtocolOrder(
            external_order_id=str(order_id),
            status=normalize_order_status(str(data.get("status") or "pending")),
            raw=data,
        )

    async def check_order_status(self, external_order_id: str) -> ProtocolOrderStatus:
        data = await self._request("GET", f"/orders/{external_order_id}")
        if not isinstance(data, dict):
            data = {}
        status = str(data.get("status") or "pending")
        if _looks_like_phone(data) and status.lower() not in {
            "completed",
            "complete",
            "success",
            "failed",
            "cancelled",
            "canceled",
            "refunded",
        }:
            try:
                coded = await self._request("POST", f"/orders/{external_order_id}/code")
                if isinstance(coded, dict):
                    data = {**data, **coded}
                    status = str(coded.get("status") or status)
            except ProtocolError as exc:
                logger.info("كود الطلب %s غير جاهز بعد: %s", external_order_id, exc)
        return ProtocolOrderStatus(
            external_order_id=str(external_order_id),
            status=normalize_order_status(status),
            charge=_as_decimal(data.get("charge") or data.get("price") or data.get("cost")),
            remains=_as_int_or_none(data.get("remains")),
            start_count=_as_int_or_none(data.get("start_count")),
            raw=data,
        )

    async def cancel_order(self, external_order_id: str) -> bool:
        try:
            await self._request("POST", f"/orders/{external_order_id}/cancel")
            return True
        except ProtocolError as exc:
            logger.warning("فشل إلغاء طلب الصديق %s: %s", external_order_id, exc)
            return False


def _as_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("services", "items", "results", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _infer_requirements(item: dict, type_key: str, category: str) -> tuple[bool, bool]:
    cat = category.lower()
    if type_key in PHONE_TYPES or cat in PHONE_CATEGORIES:
        return False, False
    if type_key in SMM_TYPES or cat in SMM_CATEGORIES:
        return True, True
    if type_key in {"ai", "ext"} or cat in {"subscription", "ai"}:
        return False, False
    if "requires_link" in item or "requires_quantity" in item:
        return bool(item.get("requires_link")), bool(item.get("requires_quantity"))
    return False, False


def _looks_like_phone(data: dict) -> bool:
    type_key = str(data.get("type") or data.get("service_type") or "").lower()
    category = str(data.get("category") or "").lower()
    if type_key in PHONE_TYPES or category in PHONE_CATEGORIES:
        return True
    try:
        sid = int(data.get("service_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    return 1000 <= sid <= 3999
