"""
Protocol for the ggsoma Partner API v1 (digital subscriptions).

Base URL:  https://ggsoma.store/api/partner/v1
Auth:      ``Authorization: Bearer sk_live_...``
Currency:  USD — same wallet balance as the seller's Telegram bot.

Endpoints (per official Partner API docs):
  GET  /health              service status (no auth)
  GET  /balance             wallet balance
  GET  /catalog/providers   provider list (each provider = one app brand)
  GET  /catalog/products    product catalog (+ optional ?provider=key)
  GET  /catalog/products/:ref
  POST /orders              create order (needs unique externalOrderId)
  GET  /orders              list API orders
  GET  /orders/:orderCode   order detail + delivery content
  GET  /usage               usage statistics

Delivery types: LINK (delivery.link), COUPON (delivery.code),
READY_ACCOUNT (delivery.content — sensitive, never log).

Products here are *instant digital subscriptions* (Netflix/Gemini/CapCut …),
not per-1000 SMM services: unit price is per item, quantity defaults to 1 and
orders complete instantly with the delivery content in the response.
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal

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

ENGINE_KEYS = ("ggsoma", "ggsoma_partner", "ggsoma_v1", "gg_soma")
DEFAULT_API_URL = "https://ggsoma.store/api/partner/v1"

# رموز الخطأ الموثقة من المزود → استثناءاتنا
_AUTH_CODES = {"INVALID_API_KEY", "API_KEY_EXPIRED", "USER_BLOCKED", "API_ACCESS_DISABLED", "PARTNER_SUSPENDED"}
_FUNDS_CODES = {"INSUFFICIENT_BALANCE"}
_SERVICE_CODES = {"PRODUCT_NOT_FOUND", "PRODUCT_NOT_ALLOWED", "PRODUCT_UNAVAILABLE", "OUT_OF_STOCK", "INVALID_QUANTITY", "UNSUPPORTED_DELIVERY_TYPE"}

_DELIVERY_LABELS = {"LINK", "COUPON", "READY_ACCOUNT"}


def is_ggsoma_v1_config(custom_config: dict | None) -> bool:
    if not custom_config:
        return False
    engine = str(custom_config.get("engine") or custom_config.get("preset") or "").lower()
    return engine in ENGINE_KEYS


def is_ggsoma_provider(provider) -> bool:
    raw = getattr(provider, "custom_config", None)
    if not raw:
        return False
    if isinstance(raw, dict):
        return is_ggsoma_v1_config(raw)
    try:
        import json

        return is_ggsoma_v1_config(json.loads(raw))
    except (TypeError, ValueError):
        return False


class GgsomaPartnerProtocol(BaseProtocol):
    name = "ggsoma_v1"

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
    def _raise_error(error, default_message: str = "خطأ من المزود"):
        """يحوّل كائن خطأ المزود إلى استثناء مع تمرير requestId للدعم."""
        if isinstance(error, str):
            code, message = "", error
        elif isinstance(error, dict):
            code = str(error.get("code") or "")
            message = str(error.get("message") or code or default_message)
        else:
            code, message = "", default_message
        request_id = ""
        if isinstance(error, dict):
            request_id = str(error.get("requestId") or error.get("request_id") or "").strip()
        if request_id and request_id not in message:
            message = f"{message} (requestId: {request_id})"
        exc: ProtocolError = ProtocolError(
            f"{code}: {message}".strip(": ") or default_message
        )
        if code in _AUTH_CODES:
            exc = ProtocolAuthError(message)
        elif code in _FUNDS_CODES or is_insufficient_funds_error(f"{code} {message}"):
            exc = ProtocolInsufficientFundsError(message)
        elif code in _SERVICE_CODES or is_invalid_service_error(f"{code} {message}"):
            exc = ProtocolInvalidServiceError(message)
        exc.request_id = request_id  # نوع إضافي: يُمرَّر لسجل الدعم
        raise exc

    @staticmethod
    def unwrap(result):
        """يفك مغلف الرد: النجاح {ok:true, ...} أو {data:[...]}، والخطأ {ok:false,error:{...}}."""
        if not isinstance(result, dict):
            return result
        if result.get("ok") is False or "error" in result:
            error = result.get("error") or {}
            GgsomaPartnerProtocol._raise_error(error)
            return None  # pragma: no cover - _raise_error لا يعود أبداً
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
        try:
            async with aiohttp.ClientSession(
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as session:
                kwargs = {}
                if params:
                    kwargs["params"] = params
                if payload is not None and method.upper() != "GET":
                    kwargs["json"] = payload
                async with session.request(method.upper(), url, **kwargs) as response:
                    body = await response.text()
                    if response.status in (401, 403):
                        raise ProtocolAuthError(f"مفتاح API مرفوض: HTTP {response.status}")
                    try:
                        parsed = json.loads(body) if body else {}
                    except Exception as exc:
                        raise ProtocolError(f"استجابة غير JSON: {body[:200]}") from exc
                    if response.status not in (200, 201, 202):
                        try:
                            self.unwrap(parsed)
                        except ProtocolError:
                            raise
                        request_hint = ""
                        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                            rid = str(parsed["error"].get("requestId") or "").strip()
                            if rid:
                                request_hint = f" (requestId: {rid})"
                        raise ProtocolConnectionError(
                            f"HTTP {response.status}: {body[:200]}{request_hint}"
                        )
                    return self.unwrap(parsed)
        except ProtocolError:
            raise
        except TimeoutError as exc:
            raise ProtocolConnectionError("انتهت مهلة الاتصال بمزود ggsoma") from exc
        except aiohttp.ClientError as exc:
            raise ProtocolConnectionError(f"تعذر الاتصال بمزود ggsoma: {exc}") from exc

    async def test_connection(self) -> bool:
        # /health بدون مصادقة؛ لو فشل نجرب /balance (يتطلب المفتاح) لتمييز مشاكل الشبكة.
        try:
            await self._request("GET", "/health")
            return True
        except ProtocolAuthError:
            raise
        except ProtocolError:
            await self.get_balance()
            return True

    async def get_balance(self) -> ProtocolBalance:
        data = await self._request("GET", "/balance")
        if not isinstance(data, dict):
            raise ProtocolError("رصيد المزود ليس كائناً")
        try:
            amount = Decimal(str(data.get("balance", data.get("amount", 0))))
        except Exception as exc:
            raise ProtocolError("قيمة الرصيد غير صالحة") from exc
        return ProtocolBalance(
            amount=amount,
            currency=str(data.get("currency") or "USD"),
            raw=data,
        )

    async def get_providers(self) -> list[dict]:
        """قائمة التطبيقات/المزودين: [{key, name, emoji:{normal,...}}]"""
        data = await self._request("GET", "/catalog/providers")
        items = data if isinstance(data, list) else []
        providers = []
        for item in items:
            if not isinstance(item, dict) or not item.get("key"):
                continue
            emoji = item.get("emoji") or {}
            providers.append(
                {
                    "key": str(item["key"]),
                    "name": str(item.get("name") or item.get("key")),
                    "emoji": str(emoji.get("normal") or "") if isinstance(emoji, dict) else "",
                    "raw": item,
                }
            )
        return providers

    async def get_services(
        self,
        service_type: str | None = None,
        category: str | None = None,
    ) -> list[ProtocolService]:
        params = {"provider": service_type} if service_type else None
        data = await self._request("GET", "/catalog/products", params=params)
        items = data if isinstance(data, list) else []
        services = []
        for item in items:
            svc = self._parse_product(item)
            if svc is not None:
                services.append(svc)
        return services

    async def get_service(self, service_id: str | int) -> ProtocolService | None:
        try:
            data = await self._request("GET", f"/catalog/products/{service_id}")
        except ProtocolError:
            return None
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return None
        return self._parse_product(data)

    def _parse_product(self, item: dict) -> ProtocolService | None:
        if not isinstance(item, dict):
            return None
        slug = item.get("slug") or item.get("productCode") or item.get("id")
        if slug is None:
            return None
        your_price = item.get("yourPrice", item.get("pricing", {}).get("yourUnitPrice"))
        if your_price is None:
            your_price = item.get("catalogPrice", item.get("price", 0))
        try:
            rate = Decimal(str(your_price))
        except Exception:
            rate = Decimal("0")

        provider = item.get("provider") or {}
        provider_key = (
            str(provider.get("key") or "")
            if isinstance(provider, dict)
            else str(item.get("providerKey") or "")
        )
        provider_name = (
            str(provider.get("name") or provider_key)
            if isinstance(provider, dict)
            else (provider_key or "مزود")
        )
        provider_emoji = ""
        if isinstance(provider, dict):
            emoji = provider.get("emoji")
            provider_emoji = str(emoji.get("normal") or "") if isinstance(emoji, dict) else ""

        delivery_type = str(item.get("deliveryType") or "LINK").upper()
        stock = item.get("stock") or {}
        if not isinstance(stock, dict):
            stock = {}
        in_stock = bool(stock.get("inStock", True)) if stock.get("inStock") is not None else True

        name = str(item.get("name") or item.get("title") or f"منتج {slug}")
        description_parts = []
        duration = item.get("durationDays")
        if duration:
            description_parts.append(f"المدة: {duration} يوم")
        description_parts.append(f"التوصيل: {delivery_type}")
        if item.get("flags") and isinstance(item["flags"], dict) and item["flags"].get("hasInstructions"):
            description_parts.append("يتضمن تعليمات")

        return ProtocolService(
            external_id=str(slug),
            name=name[:500],
            category=delivery_type,
            service_type=provider_key or None,
            rate=rate,
            min_quantity=1,
            max_quantity=1,
            description=" · ".join(description_parts) or None,
            requires_link=False,
            requires_quantity=False,
            requires_player_id=False,
            supports_refill=False,
            supports_cancel=False,
            raw={
                "provider_key": provider_key,
                "provider_name": provider_name,
                "provider_emoji": provider_emoji,
                "delivery_type": delivery_type,
                "in_stock": in_stock,
                "duration_days": duration,
                "warranty": item.get("warranty"),
                "slug": str(slug),
                "product_code": item.get("productCode"),
                "sort_order": item.get("sortOrder", 0),
                "source": item,
            },
        )

    async def place_order(
        self,
        service_id: str,
        target: str = "",
        quantity: int = 1,
        extra_params: dict | None = None,
    ) -> ProtocolOrder:
        """ينشئ طلباً فورياً لدى ggsoma ويعيد محتوى التوصيل فور اكتماله.

        ``service_id`` = slug المنتج. المزود يتطلب externalOrderId فريداً لكل
        طلب (يدفع مرة واحدة فقط؛ إعادة نفس المعرّف ونفس البيانات لا تكرر
        الخصم — تُعطى ضمانة التكرار عبر تمرير معرّف ثابت في
        ``extra_params={"externalOrderId": ...}`` عند الحاجة لإعادة الإرسال).
        """
        if not str(service_id):
            raise ProtocolError("لا يوجد معرّف منتج عند المزود")
        payload: dict = {
            "productSlug": str(service_id),
            "quantity": max(1, int(quantity or 1)),
        }
        external_id = str(
            (extra_params or {}).get("externalOrderId")
            or f"bot-{uuid.uuid4().hex[:20]}"
        )
        payload["externalOrderId"] = external_id
        if extra_params:
            payload.update(extra_params)
            # معرّف مستقر من المتصل يبقى كما هو مهما مررنا الباقي.
            payload["externalOrderId"] = external_id
        data = await self._request("POST", "/orders", payload=payload)
        if not isinstance(data, dict):
            raise ProtocolError("المزود لم يرجع بيانات الطلب")
        order_code = data.get("orderCode")
        if not order_code:
            raise ProtocolError(f"المزود لم يرجع orderCode: {data}")
        status = normalize_order_status(str(data.get("status") or "pending"))
        charge = None
        try:
            charge = Decimal(str(data.get("totalCharged")))
        except Exception:
            pass
        return ProtocolOrder(
            external_order_id=str(order_code),
            status=status,
            charge=charge,
            raw=data,
        )

    async def check_order_status(self, external_order_id: str) -> ProtocolOrderStatus:
        data = await self._request("GET", f"/orders/{external_order_id}")
        if not isinstance(data, dict):
            data = {}
        status = str(data.get("status") or "pending")
        return ProtocolOrderStatus(
            external_order_id=str(external_order_id),
            status=normalize_order_status(status),
            charge=_as_decimal(data.get("totalCharged") or data.get("unitPrice")),
            remains=None,
            start_count=None,
            raw=data,
        )

    async def cancel_order(self, external_order_id: str) -> bool:
        return False  # الاشتراكات الرقمية لحظية ولا تُلغى بعد التسليم


def _as_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None
