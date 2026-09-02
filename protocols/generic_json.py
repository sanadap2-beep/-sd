"""بروتوكول REST JSON عام للألعاب والتطبيقات والمزودين المخصصين.

يدعم المزودين الذين لا يستخدمون SMM V2 عبر إعدادات mappings محفوظة في
``ApiProvider.custom_config``. لا يوجد API موحد لكل مواقع الألعاب، لذلك يسمح
هذا البروتوكول بتحديد المسارات وأسماء الحقول مع قيم افتراضية عملية.
"""

from __future__ import annotations

import json
from decimal import Decimal
import aiohttp

from protocols.base import (
    BaseProtocol,
    ProtocolBalance,
    ProtocolConnectionError,
    ProtocolError,
    ProtocolOrder,
    ProtocolOrderStatus,
    ProtocolService,
    normalize_order_status,
)


_DEFAULTS = {
    "auth": "bearer",
    "request_format": "json",
    "methods": {},
    "endpoints": {
        "balance": "/balance",
        "services": "/services",
        "order": "/order",
        "status": "/order/{order_id}",
        "cancel": "/order/{order_id}/cancel",
        "refill": "/order/{order_id}/refill",
    },
    "fields": {
        "balance": "balance",
        "currency": "currency",
        "services": "data",
        "order_id": "order_id",
        "status": "status",
        "charge": "charge",
        "remains": "remains",
        "start_count": "start_count",
    },
    "order_payload": {
        "service_id": "{service_id}",
        "target": "{target}",
        "quantity": "{quantity}",
    },
    "service_fields": {
        "id": "id",
        "name": "name",
        "category": "category",
        "type": "type",
        "rate": "rate",
        "min": "min",
        "max": "max",
        "description": "description",
        "requires_link": "requires_link",
        "requires_quantity": "requires_quantity",
        "requires_player_id": "requires_player_id",
        "refill": "refill",
        "cancel": "cancel",
    },
}


def _deep_get(data, path: str | None, default=None):
    if not path:
        return default
    current = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _deep_set(data: dict, path: str, value) -> None:
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _merge(base: dict, override: dict | None) -> dict:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


class GenericJsonProtocol(BaseProtocol):
    """REST JSON protocol with configurable endpoints and field mappings."""

    name = "generic_json"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        custom_config: dict | None = None,
    ):
        super().__init__(api_url, api_key, custom_config)
        self.config = _merge(_DEFAULTS, custom_config or {})
        self.timeout = int(self.config.get("timeout", 30))

    def _endpoint(self, operation: str, **values) -> str:
        template = self.config["endpoints"].get(operation)
        if not template:
            return ""
        return template.format(**values)

    def _method(self, operation: str, default: str) -> str:
        return str(self.config.get("methods", {}).get(operation, default)).upper()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        auth = self.config.get("auth", "bearer")
        if auth == "bearer":
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif auth == "x-api-key":
            headers["X-API-Key"] = self.api_key
        elif auth == "header":
            headers[str(self.config.get("auth_header", "X-API-Key"))] = self.api_key
        return headers

    def _request_url(self, endpoint: str) -> str:
        if not endpoint:
            raise ProtocolError("مسار API غير مضبوط لهذا الإجراء")
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        return f"{self.api_url}/{endpoint.lstrip('/')}"

    async def _request(
        self,
        operation: str,
        method: str = "GET",
        payload: dict | None = None,
        **values,
    ):
        endpoint = self._endpoint(operation, **values)
        url = self._request_url(endpoint)
        params = dict(payload or {}) if method.upper() == "GET" else None
        data = dict(payload or {}) if method.upper() != "GET" else None
        if self.config.get("auth") == "query":
            (params if params is not None else data)[
                str(self.config.get("auth_query", "api_key"))
            ] = self.api_key

        request_kwargs = {"params": params} if params is not None else {}
        if data is not None:
            if self.config.get("request_format", "json") == "form":
                request_kwargs["data"] = data
            else:
                request_kwargs["json"] = data

        try:
            async with aiohttp.ClientSession(
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as session:
                async with session.request(
                    method.upper(),
                    url,
                    **request_kwargs,
                ) as response:
                    body = await response.text()
                    if response.status in (401, 403):
                        raise ProtocolError(f"فشل مصادقة المزود: HTTP {response.status}")
                    if response.status not in (200, 201, 202):
                        raise ProtocolConnectionError(f"HTTP {response.status}: {body[:200]}")
                    try:
                        result = json.loads(body) if body else {}
                    except json.JSONDecodeError:
                        raise ProtocolError(f"استجابة JSON غير صالحة: {body[:200]}") from None
                    if isinstance(result, dict) and result.get("error"):
                        raise ProtocolError(str(result["error"]))
                    return result
        except aiohttp.ClientError as exc:
            raise ProtocolConnectionError(f"فشل الاتصال بالمزود: {exc}") from exc
        except TimeoutError as exc:
            raise ProtocolConnectionError("انتهت مهلة الاتصال بالمزود") from exc

    async def test_connection(self) -> bool:
        if self.config.get("balance_optional") and not self.config.get("endpoints", {}).get("balance"):
            # بعض مزودي المتاجر العامة لا يملكون رصيد حساب؛ يكفي نجاح سحب المنتجات.
            await self.get_services()
            return True
        await self.get_balance()
        return True

    async def get_balance(self) -> ProtocolBalance:
        if self.config.get("balance_optional") and not self.config.get("endpoints", {}).get("balance"):
            return ProtocolBalance(amount=Decimal("0"), currency="USD", raw={"optional": True})
        data = await self._request("balance", self._method("balance", "GET"))
        balance = _deep_get(
            data,
            self.config["fields"].get("balance"),
            _deep_get(data, "data.balance", 0),
        )
        currency = _deep_get(
            data,
            self.config["fields"].get("currency"),
            "USD",
        )
        try:
            amount = Decimal(str(balance))
        except Exception as exc:
            raise ProtocolError("قيمة الرصيد من المزود غير صالحة") from exc
        return ProtocolBalance(amount=amount, currency=str(currency), raw=data)

    async def get_services(self) -> list[ProtocolService]:
        data = await self._request("services", self._method("services", "GET"))
        services_data = _deep_get(data, self.config["fields"].get("services"))
        if services_data is None and isinstance(data, list):
            services_data = data
        if isinstance(services_data, dict):
            services_data = services_data.get("services", [])
        if not isinstance(services_data, list):
            raise ProtocolError("استجابة الخدمات ليست قائمة")

        field_map = self.config["service_fields"]
        result = []
        for item in services_data:
            if not isinstance(item, dict):
                continue
            external_id = _deep_get(item, field_map["id"])
            if external_id is None:
                continue
            rate = _deep_get(item, field_map["rate"], 0)
            try:
                rate = Decimal(str(rate))
            except Exception:
                rate = Decimal("0")
            result.append(
                ProtocolService(
                    external_id=str(external_id),
                    name=str(_deep_get(item, field_map["name"], "خدمة")),
                    category=_deep_get(item, field_map["category"]),
                    service_type=_deep_get(item, field_map["type"]),
                    rate=rate,
                    min_quantity=_as_int(_deep_get(item, field_map["min"], 1), 1),
                    max_quantity=_as_int(
                        _deep_get(item, field_map["max"], 1_000_000),
                        1_000_000,
                    ),
                    description=_deep_get(item, field_map["description"]),
                    requires_link=bool(_deep_get(item, field_map["requires_link"], True)),
                    requires_quantity=bool(_deep_get(item, field_map["requires_quantity"], True)),
                    requires_player_id=bool(
                        _deep_get(item, field_map["requires_player_id"], False)
                    ),
                    supports_refill=bool(_deep_get(item, field_map["refill"], False)),
                    supports_cancel=bool(_deep_get(item, field_map["cancel"], False)),
                    raw=item,
                )
            )
        return result

    async def place_order(
        self,
        service_id: str,
        target: str,
        quantity: int,
        extra_params: dict | None = None,
    ) -> ProtocolOrder:
        payload = {}
        for key, template in self.config["order_payload"].items():
            value = str(template).replace("{service_id}", str(service_id))
            value = value.replace("{target}", str(target))
            value = value.replace("{quantity}", str(quantity))
            payload[key] = value
        payload.update(extra_params or {})
        data = await self._request("order", self._method("order", "POST"), payload)
        order_id = _deep_get(data, self.config["fields"].get("order_id"))
        if order_id is None:
            order_id = _deep_get(data, "data.order_id", _deep_get(data, "id"))
        if order_id is None:
            raise ProtocolError("المزود لم يرجع رقم الطلب")
        return ProtocolOrder(
            external_order_id=str(order_id),
            status=normalize_order_status(
                str(_deep_get(data, self.config["fields"].get("status"), "pending"))
            ),
            raw=data,
        )

    async def check_order_status(self, external_order_id: str) -> ProtocolOrderStatus:
        data = await self._request(
            "status",
            self._method("status", "GET"),
            order_id=external_order_id,
        )
        fields = self.config["fields"]
        raw_status = _deep_get(data, fields.get("status"), "pending")
        return ProtocolOrderStatus(
            external_order_id=external_order_id,
            status=normalize_order_status(str(raw_status)),
            charge=_as_decimal(_deep_get(data, fields.get("charge"))),
            remains=_as_int_or_none(_deep_get(data, fields.get("remains"))),
            start_count=_as_int_or_none(_deep_get(data, fields.get("start_count"))),
            raw=data,
        )

    async def cancel_order(self, external_order_id: str) -> bool:
        endpoint = self.config["endpoints"].get("cancel")
        if not endpoint:
            return False
        await self._request(
            "cancel",
            self._method("cancel", "POST"),
            {"order_id": external_order_id},
            order_id=external_order_id,
        )
        return True

    async def refill_order(self, external_order_id: str) -> bool:
        endpoint = self.config["endpoints"].get("refill")
        if not endpoint:
            return False
        await self._request(
            "refill",
            self._method("refill", "POST"),
            {"order_id": external_order_id},
            order_id=external_order_id,
        )
        return True


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


class GamesGenericProtocol(GenericJsonProtocol):
    """Generic JSON defaults for game/app suppliers."""

    name = "games_generic"


class CustomJsonProtocol(GenericJsonProtocol):
    """Generic JSON protocol whose mappings come from custom_config."""

    name = "custom"
