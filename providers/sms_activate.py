"""
مزود أرقام SMS-Activate.
يعمل بنمط SMS-Activate الكلاسيكي (action-based).
https://sms-activate.org/
"""

import json
import logging
from decimal import Decimal

import aiohttp

from providers.base import BaseProvider, PurchasedNumber, OrderStatusResult
from config import settings

logger = logging.getLogger(__name__)

SMS_ACTIVATE_BASE = "https://api.sms-activate.org/stubs/handler_api.php"
SMS_ACTIVATE_RUB_TO_USD_RATE = Decimal("100")


class ProviderAPIError(Exception):
    pass


class SMSActivateProvider(BaseProvider):
    name = "sms_activate"

    def __init__(self):
        self.api_key = settings.SMS_ACTIVATE_API_KEY

    async def _request(self, params: dict) -> str:
        params = {"api_key": self.api_key, **params}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                SMS_ACTIVATE_BASE,
                params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise ProviderAPIError(f"SMS-Activate error {resp.status}: {text}")
                return text.strip()

    def _rub_to_usd(self, amount_rub: Decimal) -> Decimal:
        return (amount_rub / SMS_ACTIVATE_RUB_TO_USD_RATE).quantize(Decimal("0.0001"))

    async def get_balance(self) -> Decimal:
        result = await self._request({"action": "getBalance"})
        if result.startswith("ACCESS_BALANCE:"):
            balance_rub = Decimal(result.split(":")[1])
            return self._rub_to_usd(balance_rub)
        raise ProviderAPIError(f"استجابة غير متوقعة من SMS-Activate: {result}")

    async def get_countries_services(self) -> list[dict]:
        raise NotImplementedError("يُستخدم get_price مباشرة")

    async def get_price(self, country: str, service: str) -> Decimal | None:
        result = await self._request(
            {
                "action": "getPrices",
                "country": country,
                "service": service,
            }
        )
        try:
            data = json.loads(result)
            country_data = data.get(country, {}).get(service, {})
            if not country_data:
                return None
            first_operator = list(country_data.values())[0]
            cost = first_operator.get("cost")
            if cost is None:
                return None
            return self._rub_to_usd(Decimal(str(cost)))
        except Exception:
            return None

    async def buy_number(
        self,
        country: str,
        service: str,
        operator: str | None = None,
        max_price: Decimal | None = None,
    ) -> PurchasedNumber:
        params: dict = {
            "action": "getNumber",
            "service": service,
            "country": country,
        }
        if max_price is not None:
            params["maxPrice"] = str(max_price)
        result = await self._request(
            params
        )
        if result.startswith("ACCESS_NUMBER:"):
            parts = result.split(":")
            order_id = parts[1]
            phone = parts[2]
            price = await self.get_price(country, service)
            cost_usd = price if price is not None else Decimal("0")
            return PurchasedNumber(
                provider_order_id=order_id,
                phone_number=phone,
                cost_usd=cost_usd,
                raw={"raw_response": result},
            )
        raise ProviderAPIError(f"فشل شراء رقم من SMS-Activate: {result}")

    async def check_status(self, order_id: str) -> OrderStatusResult:
        result = await self._request(
            {
                "action": "getStatus",
                "id": order_id,
            }
        )
        mapped = "pending"
        code = None

        if result.startswith("STATUS_OK:"):
            code = result.split(":")[1]
            mapped = "code_received"
        elif result == "STATUS_CANCEL":
            mapped = "cancelled"
        elif result.startswith("STATUS_WAIT_CODE"):
            mapped = "pending"
        elif result.startswith("STATUS_WAIT_RETRY"):
            mapped = "pending"

        return OrderStatusResult(
            status=mapped,
            sms_code=code,
            full_text=result,
            raw={"raw": result},
        )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await self._request(
                {
                    "action": "setStatus",
                    "id": order_id,
                    "status": 8,
                }
            )
            return True
        except Exception as e:
            logger.error(f"فشل إلغاء الطلب {order_id} من SMS-Activate: {e}")
            return False

    async def finish_order(self, order_id: str) -> bool:
        try:
            await self._request(
                {
                    "action": "setStatus",
                    "id": order_id,
                    "status": 6,
                }
            )
            return True
        except Exception as e:
            logger.error(f"فشل إتمام الطلب {order_id} من SMS-Activate: {e}")
            return False