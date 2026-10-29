"""تكامل FazerCards الرسمي لشحن الألعاب المباشر.

واجهة FazerCards ليست SMM V2 ولا تملك كتالوجاً موحداً: عروض الشحن المباشر
تُقرأ من ``/topups`` ثم ``/topups/offers``، ويُنشأ الطلب عبر
``/topups/order``. لذلك يحفظ هذا الـ adapter زوج ``category_id`` و
``offer_id`` في معرّف خدمة واحد، ويحافظ على حقول اللاعب الديناميكية التي
يرجعها المزود (مثل Player ID و Zone ID).

لا يضع هذا الملف أي مفتاح أو رصيد في المشروع. المفتاح يُدخل ويُشفّر من لوحة
المشرف مثل بقية مزودي الـ API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

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
    normalize_order_status,
)
from services.provider_input_service import decode_provider_fields, field_label_ar

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://api.fzr.cards/api/v2"
ENGINE_KEYS = ("fazercards", "fazer_cards", "fzr_cards", "fzr")
_SERVICE_SEPARATOR = "::"


def is_fazercards_config(custom_config: dict | None) -> bool:
    """هل إعداد المزود يشير إلى محرك FazerCards؟"""
    if not custom_config:
        return False
    engine = str(custom_config.get("engine") or custom_config.get("preset") or "").lower()
    return engine in ENGINE_KEYS


def is_fazercards_provider(provider) -> bool:
    """نسخة مناسبة لكائن ``ApiProvider`` المحفوظ (إعداد JSON أو dict)."""
    raw = getattr(provider, "custom_config", None)
    if not raw:
        return False
    if isinstance(raw, dict):
        return is_fazercards_config(raw)
    try:
        return is_fazercards_config(json.loads(raw))
    except (TypeError, ValueError):
        return False


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _message_from(data: Any) -> str:
    """يستخرج رسالة قابلة للعرض من صيغ أخطاء API الشائعة."""
    if isinstance(data, dict):
        for key in ("message", "error", "detail", "reason"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = _message_from(value)
                if nested:
                    return nested
        errors = data.get("errors")
        if isinstance(errors, list):
            for value in errors:
                message = _message_from(value)
                if message:
                    return message
    if isinstance(data, str) and data.strip():
        return data.strip()
    return "خطأ غير معروف من FazerCards"


def _unwrap(data: Any) -> Any:
    """يحذف علم ``ok`` من envelope الرسمي مع إبقاء محتوى الرد كاملاً."""
    if isinstance(data, dict):
        return {key: value for key, value in data.items() if key != "ok"}
    return data


def _field_required(value: Any, default: bool = True) -> bool:
    """يحوّل قيم required القادمة كنص أو bool بلا جعل "false" حقيقة."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "optional"}
    return bool(value)


def _normalize_fields(raw_fields: Any) -> list[dict[str, Any]]:
    """ينظّف حقول الإدخال ويحافظ على keys الأصلية اللازمة لإرسال الطلب."""
    fields: list[dict[str, Any]] = []
    if not isinstance(raw_fields, list):
        return fields
    for item in raw_fields:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        field = {
            "key": key,
            "label": str(item.get("label") or key),
            "label_ar": field_label_ar(key, item.get("label")),
            "type": str(item.get("type") or "text"),
            # توثيق FazerCards يصف كل fields كمدخلات مطلوبة. نبقي القيمة إن
            # أضافها المزود مستقبلاً حتى لا يفرض UI حقلاً اختيارياً.
            "required": _field_required(item.get("required"), default=True),
        }
        if isinstance(item.get("options"), list):
            field["options"] = item["options"]
        fields.append(field)
    return fields


class FazerCardsProtocol(BaseProtocol):
    """Adapter غير معتمد على SDK خارجي لـ FazerCards Top-ups API v2."""

    name = "fazercards"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        custom_config: dict | None = None,
    ):
        super().__init__(api_url or DEFAULT_API_URL, api_key, custom_config)
        config = custom_config or {}
        self.timeout = max(5, min(int(config.get("timeout", 45)), 120))
        self.catalog_limit = max(1, min(int(config.get("catalog_limit", 100)), 100))
        self.catalog_concurrency = max(
            1, min(int(config.get("catalog_concurrency", 4)), 10)
        )
        # لا نكرر طلبات الإنشاء إلا إذا حملت مفتاح idempotency نفسه.
        self.max_retries = max(0, min(int(config.get("max_retries", 2)), 4))

    # ─────────── HTTP ───────────

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "User-Agent": "sanadap-games-bot/1.0",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _url(self, path: str) -> str:
        return f"{self.api_url.rstrip('/')}/{path.lstrip('/')}"

    def _raise_api_error(self, data: Any, status: int | None = None) -> None:
        message = _message_from(data)
        message_lower = message.lower()
        if status in (401, 403):
            raise ProtocolAuthError(f"مفتاح FazerCards مرفوض: {message}")
        if is_insufficient_funds_error(message) or any(
            phrase in message_lower
            for phrase in ("insufficient balance", "not enough balance", "low balance")
        ):
            raise ProtocolInsufficientFundsError(f"رصيد FazerCards غير كافٍ: {message}")
        if status == 404:
            raise ProtocolInvalidServiceError(f"الخدمة أو الطلب غير موجود لدى FazerCards: {message}")
        if status in (400, 409):
            raise ProtocolError(f"رفض FazerCards الطلب: {message}")
        if status == 429:
            raise ProtocolConnectionError("تم تجاوز حد طلبات FazerCards؛ أعد المحاولة بعد قليل.")
        if status and status >= 500:
            raise ProtocolConnectionError(f"خطأ مؤقت من FazerCards: {message}")
        raise ProtocolError(f"خطأ من FazerCards: {message}")

    async def _request_once(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """محاولة HTTP واحدة مع تحويل أخطاء FazerCards للواجهة الموحدة."""
        try:
            async with aiohttp.ClientSession(
                headers=self._headers(idempotency_key),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as session:
                kwargs: dict[str, Any] = {}
                if params:
                    kwargs["params"] = {key: value for key, value in params.items() if value is not None}
                if payload is not None and method.upper() != "GET":
                    kwargs["json"] = payload
                async with session.request(method.upper(), self._url(path), **kwargs) as response:
                    body = await response.text()
                    try:
                        data = json.loads(body) if body else {}
                    except json.JSONDecodeError as exc:
                        raise ProtocolError(
                            f"استجابة غير صالحة من FazerCards: {body[:180]}"
                        ) from exc
                    if response.status not in (200, 201, 202):
                        self._raise_api_error(data, response.status)
                    if isinstance(data, dict) and data.get("ok") is False:
                        self._raise_api_error(data, response.status)
                    return _unwrap(data)
        except ProtocolError:
            raise
        except TimeoutError as exc:
            raise ProtocolConnectionError("انتهت مهلة الاتصال بـ FazerCards") from exc
        except aiohttp.ClientError as exc:
            raise ProtocolConnectionError(f"تعذر الاتصال بـ FazerCards: {exc}") from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """ينفّذ الطلب مع retry آمن للقراءات وطلبات الشراء ذات المفتاح الثابت.

        حدوث timeout بعد إرسال ``POST`` لا يثبت أن المزود لم ينفّذ الطلب، لذلك
        لا نعيده أبداً بلا ``Idempotency-Key``. أما طلب الشحن فيمرر المفتاح نفسه
        في كل محاولة، فيعيد FazerCards الطلب الأصلي بدلاً من خصم الرصيد مجدداً.
        """
        replay_safe = method.upper() in {"GET", "HEAD", "OPTIONS"} or bool(idempotency_key)
        for attempt in range(self.max_retries + 1):
            try:
                return await self._request_once(
                    method,
                    path,
                    payload=payload,
                    params=params,
                    idempotency_key=idempotency_key,
                )
            except ProtocolConnectionError:
                if not replay_safe or attempt >= self.max_retries:
                    raise
                # Backoff قصير؛ لا نطيل زمن رد البوت ولا نغرق API عند عطل عابر.
                await asyncio.sleep(0.25 * (2**attempt))

        # حلقة for تعود أو ترفع دائماً؛ هذا فقط لإرضاء محلل الأنواع.
        raise ProtocolConnectionError("تعذر الاتصال بـ FazerCards")

    # ─────────── اتصال ورصيد ───────────

    async def test_connection(self) -> bool:
        await self.get_balance()
        return True

    async def get_balance(self) -> ProtocolBalance:
        data = await self._request("GET", "/balance")
        if not isinstance(data, dict):
            raise ProtocolError("استجابة رصيد FazerCards ليست كائناً")
        amount = _decimal(data.get("balance", data.get("amount")))
        if amount is None:
            raise ProtocolError("قيمة رصيد FazerCards غير صالحة")
        return ProtocolBalance(
            amount=amount,
            currency=str(data.get("currency") or "USD"),
            raw=data,
        )

    # ─────────── الكتالوج ───────────

    async def _categories(self) -> list[dict[str, Any]]:
        categories: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        while True:
            page = await self._request(
                "GET",
                "/topups",
                params={"limit": self.catalog_limit, "cursor": cursor},
            )
            if not isinstance(page, dict):
                raise ProtocolError("استجابة تصنيفات شحن الألعاب غير صالحة")
            items = page.get("items", [])
            if not isinstance(items, list):
                raise ProtocolError("قائمة تصنيفات شحن الألعاب غير صالحة")
            categories.extend(item for item in items if isinstance(item, dict))
            meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
            next_cursor = meta.get("next_cursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)
            if cursor in seen_cursors:
                raise ProtocolError("تكرار cursor في كتالوج FazerCards")
            seen_cursors.add(cursor)
        return categories

    async def _offers(self, category_id: str) -> dict[str, Any]:
        data = await self._request(
            "GET",
            "/topups/offers",
            params={"category_id": category_id},
        )
        if not isinstance(data, dict):
            raise ProtocolError(f"عروض FazerCards للفئة {category_id} غير صالحة")
        return data

    @staticmethod
    def _service_id(category_id: str, offer_id: str) -> str:
        service_id = f"{category_id}{_SERVICE_SEPARATOR}{offer_id}"
        # الأعمدة الحالية للمزوّد تحفظ حتى 64 حرفاً. IDs الرسمية قصيرة، لكن
        # نرفض صيغة غير قابلة لإعادة البناء بدلاً من قصّها وإرسال طلب خاطئ.
        if len(service_id) > 64:
            raise ProtocolError("معرّف عرض FazerCards طويل جداً ولا يمكن حفظه بأمان")
        return service_id

    async def get_services(self) -> list[ProtocolService]:
        """يسحب كل عروض شحن الألعاب مع حقول العميل لكل لعبة."""
        categories = await self._categories()
        semaphore = asyncio.Semaphore(self.catalog_concurrency)

        async def load(category: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
            category_id = str(category.get("category_id") or "").strip()
            if not category_id:
                return category, None
            async with semaphore:
                return category, await self._offers(category_id)

        pairs = await asyncio.gather(*(load(category) for category in categories))
        services: list[ProtocolService] = []
        seen_ids: set[str] = set()
        for category, catalog in pairs:
            if not catalog:
                continue
            category_id = str(category.get("category_id") or catalog.get("category_id") or "").strip()
            if not category_id:
                continue
            category_name = str(catalog.get("name") or category.get("name") or category_id)
            fields = _normalize_fields(catalog.get("fields"))
            offers = catalog.get("offers", [])
            if not isinstance(offers, list):
                logger.warning("FazerCards category %s returned non-list offers", category_id)
                continue
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                offer_id = str(offer.get("offer_id") or "").strip()
                rate = _decimal(offer.get("price_usd"))
                if not offer_id or rate is None or rate < 0:
                    continue
                service_id = self._service_id(category_id, offer_id)
                if service_id in seen_ids:
                    continue
                seen_ids.add(service_id)
                offer_name = str(offer.get("name") or offer_id)
                raw = {
                    "engine": "fazercards",
                    "family": "topups",
                    "category_id": category_id,
                    "category_name": category_name,
                    "offer_id": offer_id,
                    "fields": fields,
                    "note": catalog.get("note") or category.get("note"),
                }
                services.append(
                    ProtocolService(
                        external_id=service_id,
                        name=f"{category_name} — {offer_name}",
                        category=category_name,
                        service_type="game_topup",
                        rate=rate,
                        min_quantity=1,
                        max_quantity=1,
                        description=(str(raw["note"]) if raw["note"] else None),
                        requires_link=False,
                        requires_quantity=False,
                        requires_player_id=any(field["required"] for field in fields),
                        supports_refill=False,
                        supports_cancel=False,
                        raw=raw,
                    )
                )
        return services

    # ─────────── الطلبات ───────────

    @staticmethod
    def _split_service_id(service_id: str) -> tuple[str, str]:
        category_id, separator, offer_id = str(service_id).partition(_SERVICE_SEPARATOR)
        if not separator or not category_id or not offer_id:
            raise ProtocolInvalidServiceError("معرّف عرض FazerCards غير صالح")
        return category_id, offer_id

    async def _fields_for_plain_target(self, category_id: str, target: str) -> dict[str, str]:
        """يدعم المنتجات القديمة ذات حقل واحد من دون تخمين اسم Player ID."""
        catalog = await self._offers(category_id)
        fields = [field for field in _normalize_fields(catalog.get("fields")) if field["required"]]
        if len(fields) != 1:
            raise ProtocolError(
                "هذا العرض يحتاج أكثر من حقل. أعد مزامنة الكتالوج واجمع البيانات المطلوبة."
            )
        return {str(fields[0]["key"]): target}

    async def place_order(
        self,
        service_id: str,
        target: str,
        quantity: int,
        extra_params: dict | None = None,
    ) -> ProtocolOrder:
        # عروض top-up في FazerCards حزم ثابتة؛ quantity هنا لا يغير العرض.
        # نتحقق منه فقط كي لا يرسل مسار قديم قيمة صفر/سالبة.
        if int(quantity or 1) < 1:
            raise ProtocolError("كمية عرض شحن الألعاب يجب أن تكون أكبر من صفر")
        category_id, offer_id = self._split_service_id(service_id)

        fields = decode_provider_fields(target) or {}
        if extra_params:
            fields.update(
                {
                    str(key): str(value)
                    for key, value in extra_params.items()
                    if value is not None and not str(key).startswith("_")
                }
            )
        if not fields:
            clean_target = (target or "").strip()
            if not clean_target:
                raise ProtocolError("يجب إدخال معرّف اللاعب قبل تنفيذ الشحن")
            fields = await self._fields_for_plain_target(category_id, clean_target)
        fields = {str(key): str(value).strip() for key, value in fields.items() if str(value).strip()}
        if not fields:
            raise ProtocolError("حقول اللاعب المطلوبة فارغة")

        # API يحتفظ بالمفتاح 7 أيام. يسمح مسار البوت بتمرير مفتاح ثابت
        # داخلياً (لا يُرسل ضمن fields) حتى يبقى retry لنفس نقرة الشراء آمناً.
        requested_key = str((extra_params or {}).get("_idempotency_key") or "").strip()
        idempotency_key = requested_key or str(uuid.uuid4())
        data = await self._request(
            "POST",
            "/topups/order",
            payload={
                "category_id": category_id,
                "offer_id": offer_id,
                "fields": fields,
            },
            idempotency_key=idempotency_key,
        )
        if not isinstance(data, dict):
            raise ProtocolError("استجابة إنشاء طلب FazerCards غير صالحة")
        order = data.get("order") if isinstance(data.get("order"), dict) else data
        order_id = order.get("id", order.get("order_id")) if isinstance(order, dict) else None
        if not order_id:
            raise ProtocolError("FazerCards لم يرجع رقم الطلب")
        return ProtocolOrder(
            external_order_id=str(order_id),
            status=normalize_order_status(str(order.get("status") or "created")),
            charge=_decimal(order.get("total_usd", order.get("price_usd"))),
            raw={**order, "idempotency_key": idempotency_key},
        )

    async def check_order_status(self, external_order_id: str) -> ProtocolOrderStatus:
        data = await self._request("GET", f"/orders/{external_order_id}")
        if not isinstance(data, dict):
            raise ProtocolError("استجابة حالة طلب FazerCards غير صالحة")
        order = data.get("order") if isinstance(data.get("order"), dict) else data
        if not isinstance(order, dict):
            raise ProtocolError("بيانات حالة طلب FazerCards غير صالحة")
        return ProtocolOrderStatus(
            external_order_id=str(order.get("id") or external_order_id),
            status=normalize_order_status(str(order.get("status") or "created")),
            charge=_decimal(
                order.get("total_usd", order.get("price_usd", order.get("chargedUsd")))
            ),
            raw=order,
        )
