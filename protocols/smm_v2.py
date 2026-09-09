"""
بروتوكول SMM V2 القياسي.

هذا البروتوكول يُستخدم في 90%+ من مواقع SMM حول العالم.
كل مواقع SMM تستخدم نفس الـ endpoint (/api/v2) ونفس الـ actions.

مواقع مختبرة تعمل بهذا البروتوكول:
- JumboSMM
- SMMGold
- Justanotherpanel
- PeakSMM
- SMMKings
- SMMShop
- MediaMister (بعض النسخ)

الطلبات كلها POST إلى نفس الـ URL
مع action مختلف في body.

Actions:
- balance   : جلب الرصيد
- services  : جلب كل الخدمات
- add       : إرسال طلب جديد
- status    : فحص حالة طلب
- refill    : إعادة ملء طلب
- cancel    : إلغاء طلب
"""

import json
import logging
from decimal import Decimal

import aiohttp

from protocols.base import (
    BaseProtocol,
    ProtocolBalance,
    ProtocolService,
    ProtocolOrder,
    ProtocolOrderStatus,
    ProtocolError,
    ProtocolAuthError,
    ProtocolConnectionError,
    ProtocolInsufficientFundsError,
    ProtocolInvalidServiceError,
    is_insufficient_funds_error,
    is_invalid_service_error,
    normalize_order_status,
)

logger = logging.getLogger(__name__)


class SmmV2Protocol(BaseProtocol):
    """
    بروتوكول SMM V2 القياسي.

    الاستخدام:
        protocol = SmmV2Protocol(
            api_url="https://jumbosmm.com/api/v2",
            api_key="your_api_key",
        )

        balance = await protocol.get_balance()
        services = await protocol.get_services()
        order = await protocol.place_order(
            service_id="123",
            target="https://instagram.com/user",
            quantity=1000,
        )
    """

    name = "smm_v2"

    def __init__(
        self,
        api_url: str,
        api_key: str,
        custom_config: dict | None = None,
    ):
        super().__init__(api_url, api_key, custom_config)
        self.timeout = 30

    async def _request(
        self,
        data: dict,
    ) -> dict | list:
        """
        يرسل طلب POST إلى الـ API.
        كل طلبات SMM V2 تُرسل بـ POST مع بيانات في form-data.

        نفتح جلسة aiohttp لكل طلب (``async with``) ونغلقها تلقائياً عند
        انتهاء الطلب/الاستجابة، تماماً كبقية البروتوكولات، حتى لا تتسرب
        جلسات مفتوحة وتظهر ``RuntimeError: Unclosed client session``.
        """
        data_with_key = {
            "key": self.api_key,
            **data,
        }

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:
            try:
                async with session.post(
                    self.api_url,
                    data=data_with_key,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    text = await resp.text()

                    if resp.status == 401 or resp.status == 403:
                        raise ProtocolAuthError("مفتاح API غير صالح")

                    if resp.status not in (200, 201):
                        raise ProtocolConnectionError(f"HTTP {resp.status}: {text[:200]}")

                    try:
                        result = await resp.json(content_type=None)
                    except (json.JSONDecodeError, Exception):
                        raise ProtocolError(
                            f"استجابة غير صالحة (JSON parsing failed): {text[:200]}"
                        )

                    if isinstance(result, dict):
                        error = result.get("error")
                        if error:
                            if is_insufficient_funds_error(error):
                                raise ProtocolInsufficientFundsError(str(error))
                            if is_invalid_service_error(error):
                                raise ProtocolInvalidServiceError(str(error))
                            raise ProtocolError(str(error))

                    return result

            except ProtocolError:
                raise
            except TimeoutError as e:
                raise ProtocolConnectionError(
                    f"انتهت مهلة الاتصال ({self.timeout} ثانية)"
                ) from e
            except aiohttp.ClientError as e:
                raise ProtocolConnectionError(f"خطأ اتصال: {e}") from e

    async def test_connection(self) -> bool:
        """
        يختبر الاتصال بجلب الرصيد.
        """
        try:
            await self.get_balance()
            return True
        except Exception as e:
            logger.warning(f"فشل اختبار الاتصال مع SMM V2: {e}")
            return False

    async def close(self):
        """متوافق مع الواجهة القديمة — لم يعد هناك جلسة دائمة تُغلق."""
        return None

    async def get_balance(self) -> ProtocolBalance:
        """
        يجلب رصيد الحساب.

        الاستجابة المتوقعة:
        {
            "balance": "100.05",
            "currency": "USD"
        }
        """
        data = await self._request({"action": "balance"})

        if not isinstance(data, dict):
            raise ProtocolError("استجابة غير متوقعة (ليست dict)")

        balance_raw = data.get("balance", "0")
        currency = data.get("currency", "USD")

        try:
            balance_amount = Decimal(str(balance_raw))
        except Exception:
            balance_amount = Decimal("0")

        return ProtocolBalance(
            amount=balance_amount,
            currency=currency,
            raw=data,
        )

    async def get_services(self) -> list[ProtocolService]:
        """
        يجلب كل الخدمات المتاحة.

        الاستجابة المتوقعة:
        [
            {
                "service": 1,
                "name": "Followers",
                "type": "Default",
                "category": "First Category",
                "rate": "0.90",
                "min": "50",
                "max": "10000",
                "refill": true,
                "cancel": true
            },
            ...
        ]
        """
        data = await self._request({"action": "services"})

        if not isinstance(data, list):
            if isinstance(data, dict) and "services" in data:
                data = data["services"]
            elif isinstance(data, dict) and "data" in data:
                data = data["data"]
            else:
                raise ProtocolError("استجابة الخدمات ليست قائمة")

        services = []
        for item in data:
            if not isinstance(item, dict):
                continue

            try:
                service = self._parse_service(item)
                services.append(service)
            except Exception as e:
                logger.warning(f"فشل تحليل خدمة: {item} - {e}")
                continue

        return services

    def _parse_service(self, item: dict) -> ProtocolService:
        """يحول dict خدمة إلى ProtocolService."""
        external_id = str(item.get("service", item.get("id", "")))

        if not external_id:
            raise ProtocolError("خدمة بدون معرّف")

        name = str(item.get("name", "Unknown Service"))
        category = item.get("category")
        service_type = item.get("type")

        rate_raw = item.get("rate", "0")
        try:
            rate = Decimal(str(rate_raw))
        except Exception:
            rate = Decimal("0")

        try:
            min_qty = int(item.get("min", 1))
        except (ValueError, TypeError):
            min_qty = 1

        try:
            max_qty = int(item.get("max", 1000000))
        except (ValueError, TypeError):
            max_qty = 1000000

        description = item.get("description")
        refill = bool(item.get("refill", False))
        cancel = bool(item.get("cancel", False))

        return ProtocolService(
            external_id=external_id,
            name=name,
            category=str(category) if category else None,
            service_type=(str(service_type) if service_type else None),
            rate=rate,
            min_quantity=min_qty,
            max_quantity=max_qty,
            description=(str(description) if description else None),
            requires_link=True,
            requires_quantity=True,
            requires_player_id=False,
            supports_refill=refill,
            supports_cancel=cancel,
            raw=item,
        )

    async def place_order(
        self,
        service_id: str,
        target: str,
        quantity: int,
        extra_params: dict | None = None,
    ) -> ProtocolOrder:
        """
        يرسل طلب جديد.

        الاستجابة المتوقعة:
        {
            "order": 23501
        }
        """
        request_data = {
            "action": "add",
            "service": service_id,
            "link": target,
            "quantity": quantity,
        }

        if extra_params:
            request_data.update(extra_params)

        data = await self._request(request_data)

        if not isinstance(data, dict):
            raise ProtocolError("استجابة غير متوقعة عند الطلب")

        order_id = data.get("order")
        if not order_id:
            raise ProtocolError(f"لم يُرجع المزود order_id: {data}")

        return ProtocolOrder(
            external_order_id=str(order_id),
            status="pending",
            raw=data,
        )

    async def check_order_status(self, external_order_id: str) -> ProtocolOrderStatus:
        """
        يفحص حالة طلب معين.

        الاستجابة المتوقعة:
        {
            "charge": "0.27819",
            "start_count": "3572",
            "status": "Partial",
            "remains": "157",
            "currency": "USD"
        }
        """
        data = await self._request(
            {
                "action": "status",
                "order": external_order_id,
            }
        )

        if not isinstance(data, dict):
            raise ProtocolError("استجابة غير متوقعة عند فحص الحالة")

        raw_status = data.get("status", "pending")
        normalized = normalize_order_status(raw_status)

        charge = None
        if "charge" in data:
            try:
                charge = Decimal(str(data["charge"]))
            except Exception:
                pass

        remains = None
        if "remains" in data:
            try:
                remains = int(data["remains"])
            except (ValueError, TypeError):
                pass

        start_count = None
        if "start_count" in data:
            try:
                start_count = int(data["start_count"])
            except (ValueError, TypeError):
                pass

        return ProtocolOrderStatus(
            external_order_id=external_order_id,
            status=normalized,
            charge=charge,
            remains=remains,
            start_count=start_count,
            raw=data,
        )

    async def check_multiple_orders(self, order_ids: list[str]) -> dict[str, ProtocolOrderStatus]:
        """
        يفحص حالة عدة طلبات دفعة واحدة.
        SMM V2 يدعم هذا عبر إرسال orders (بصيغة CSV).
        """
        if not order_ids:
            return {}

        if len(order_ids) == 1:
            try:
                status = await self.check_order_status(order_ids[0])
                return {order_ids[0]: status}
            except Exception:
                return {}

        orders_csv = ",".join(order_ids)

        try:
            data = await self._request(
                {
                    "action": "status",
                    "orders": orders_csv,
                }
            )
        except Exception as e:
            logger.warning(f"فشل فحص متعدد للطلبات: {e}")
            return await super().check_multiple_orders(order_ids)

        if not isinstance(data, dict):
            return {}

        results = {}
        for order_id, order_data in data.items():
            if not isinstance(order_data, dict):
                continue

            raw_status = order_data.get("status", "pending")
            normalized = normalize_order_status(raw_status)

            charge = None
            if "charge" in order_data:
                try:
                    charge = Decimal(str(order_data["charge"]))
                except Exception:
                    pass

            remains = None
            if "remains" in order_data:
                try:
                    remains = int(order_data["remains"])
                except (ValueError, TypeError):
                    pass

            start_count = None
            if "start_count" in order_data:
                try:
                    start_count = int(order_data["start_count"])
                except (ValueError, TypeError):
                    pass

            results[str(order_id)] = ProtocolOrderStatus(
                external_order_id=str(order_id),
                status=normalized,
                charge=charge,
                remains=remains,
                start_count=start_count,
                raw=order_data,
            )

        return results

    async def cancel_order(self, external_order_id: str) -> bool:
        """يلغي طلباً معيناً."""
        try:
            data = await self._request(
                {
                    "action": "cancel",
                    "orders": external_order_id,
                }
            )

            if isinstance(data, dict):
                error = data.get("error")
                if error:
                    logger.warning(f"فشل إلغاء الطلب {external_order_id}: {error}")
                    return False
            return True
        except Exception as e:
            logger.error(f"خطأ في إلغاء الطلب {external_order_id}: {e}")
            return False

    async def refill_order(self, external_order_id: str) -> bool:
        """يعيد ملء طلب معين."""
        try:
            data = await self._request(
                {
                    "action": "refill",
                    "order": external_order_id,
                }
            )

            if isinstance(data, dict):
                error = data.get("error")
                if error:
                    logger.warning(f"فشل refill للطلب {external_order_id}: {error}")
                    return False
            return True
        except Exception as e:
            logger.error(f"خطأ في refill الطلب {external_order_id}: {e}")
            return False
