"""
مزود أرقام 5sim.
يحول الأسعار من الروبل إلى الدولار تلقائياً.
"""

import logging
from decimal import Decimal

import aiohttp

from providers.base import BaseProvider, PurchasedNumber, OrderStatusResult
from config import settings

logger = logging.getLogger(__name__)

FIVESIM_BASE = "https://5sim.net/v1/"
FIVESIM_RUB_TO_USD_RATE = Decimal("100")


class ProviderAPIError(Exception):
    pass


class FiveSimProvider(BaseProvider):
    name = "fivesim"

    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {settings.FIVESIM_API_KEY}",
            "Accept": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs):
        url = FIVESIM_BASE + path
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.request(
                method,
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                **kwargs,
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise ProviderAPIError(f"5sim error {resp.status}: {text}")
                try:
                    return await resp.json(content_type=None)
                except Exception:
                    return text

    def _rub_to_usd(self, amount_rub: Decimal) -> Decimal:
        """يحول الروبل للدولار بسعر صرف 5sim الداخلي."""
        return (amount_rub / FIVESIM_RUB_TO_USD_RATE).quantize(Decimal("0.0001"))

    async def get_balance(self) -> Decimal:
        data = await self._request("GET", "user/profile")
        balance_rub = Decimal(str(data.get("balance", 0)))
        return self._rub_to_usd(balance_rub)

    async def get_countries_services(self) -> list[dict]:
        raise NotImplementedError("يُستخدم get_price مباشرة")

    async def list_countries(self) -> list[str]:
        """
        قائمة مرجعية بأكواد الدول المتاحة لدى 5sim.
        تُستخدم فقط لمساعدة الأدمن.
        """
        data = await self._request("GET", "guest/countries")
        result = []
        for code, info in data.items():
            name = (info or {}).get("text_en") or code
            result.append(f"<code>{code}</code> — {name}")
        return sorted(result)

    async def get_price(self, country: str, service: str) -> Decimal | None:
        data = await self._request("GET", f"guest/prices?country={country}&product={service}")
        try:
            country_data = data[country][service]
            cheapest = None
            for operator, info in country_data.items():
                if info.get("count", 0) > 0:
                    cost_rub = Decimal(str(info["cost"]))
                    cost_usd = self._rub_to_usd(cost_rub)
                    if cheapest is None or cost_usd < cheapest:
                        cheapest = cost_usd
            return cheapest
        except (KeyError, TypeError):
            return None

    async def buy_number(
        self,
        country: str,
        service: str,
        operator: str = "any",
        max_price: Decimal | None = None,  # 5sim لا يدعم سقف السعر — يُتجاهل
    ) -> PurchasedNumber:
        data = await self._request("GET", f"user/buy/activation/{country}/{operator}/{service}")
        cost_rub = Decimal(str(data["price"]))
        cost_usd = self._rub_to_usd(cost_rub)
        return PurchasedNumber(
            provider_order_id=str(data["id"]),
            phone_number=data["phone"],
            cost_usd=cost_usd,
            raw=data,
        )

    async def check_status(self, order_id: str) -> OrderStatusResult:
        data = await self._request("GET", f"user/check/{order_id}")
        status = data.get("status")
        sms_list = data.get("sms") or []
        code = None
        full_text = None

        if sms_list:
            code = sms_list[0].get("code")
            full_text = sms_list[0].get("text")

        mapped = "pending"
        if code:
            mapped = "code_received"
        elif status == "CANCELED":
            mapped = "cancelled"
        elif status in ("TIMEOUT", "EXPIRED"):
            mapped = "expired"

        return OrderStatusResult(
            status=mapped,
            sms_code=code,
            full_text=full_text,
            raw=data,
        )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await self._request("GET", f"user/cancel/{order_id}")
            return True
        except Exception as e:
            logger.error(f"فشل إلغاء الطلب {order_id} من 5sim: {e}")
            return False

    async def finish_order(self, order_id: str) -> bool:
        try:
            await self._request("GET", f"user/finish/{order_id}")
            return True
        except Exception as e:
            logger.error(f"فشل إتمام الطلب {order_id} من 5sim: {e}")
            return False
