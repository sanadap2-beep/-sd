"""
مزود أرقام HeroSMS.
يعمل بنمط SMS-Activate الكلاسيكي (action-based).
يحول الأسعار من الروبل إلى الدولار تلقائياً.
"""

import json
import logging
from decimal import Decimal

import aiohttp

from providers.base import BaseProvider, PurchasedNumber, OrderStatusResult
from config import settings

logger = logging.getLogger(__name__)

HEROSMS_BASE = "https://hero-sms.com/stubs/handler_api.php"
HEROSMS_RUB_TO_USD_RATE = Decimal("100")


class ProviderAPIError(Exception):
    pass


class HeroSMSProvider(BaseProvider):
    name = "herosms"

    def __init__(self):
        self.api_key = settings.HEROSMS_API_KEY

    async def _request(self, params: dict) -> str:
        params = {"api_key": self.api_key, **params}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                HEROSMS_BASE,
                params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise ProviderAPIError(f"HeroSMS error {resp.status}: {text}")
                return text.strip()

    def _rub_to_usd(self, amount_rub: Decimal) -> Decimal:
        """يحول الروبل للدولار بسعر صرف HeroSMS الداخلي."""
        return (amount_rub / HEROSMS_RUB_TO_USD_RATE).quantize(Decimal("0.0001"))

    async def get_balance(self) -> Decimal:
        result = await self._request({"action": "getBalance"})
        if result.startswith("ACCESS_BALANCE:"):
            balance_rub = Decimal(result.split(":")[1])
            return self._rub_to_usd(balance_rub)
        raise ProviderAPIError(f"استجابة غير متوقعة من HeroSMS: {result}")

    async def get_countries_services(self) -> list[dict]:
        raise NotImplementedError("يُستخدم get_price مباشرة")

    async def get_countries(self) -> list[dict]:
        """يجلب كتالوج الدول من HeroSMS عبر action=getCountries.

        الصيغة القياسية لنمط SMS-Activate:
        {"روسия": {"id": 0, "rus": "...", "eng": "Russia", "visible": 1, ...}}
        بعض النسخ ترجع قائمة بدلاً من قاموس — ندعم الشكلين.

        يرجع قائمة بالشكل: [{"id": "0", "eng": "Russia"}, ...]
        """
        result = await self._request({"action": "getCountries"})
        data = json.loads(result)
        countries: list[dict] = []

        if isinstance(data, dict):
            iterable = data.values()
        elif isinstance(data, list):
            iterable = data
        else:
            raise ProviderAPIError(f"استجابة getCountries غير مفهومة من HeroSMS")

        for item in iterable:
            if not isinstance(item, dict):
                continue
            cid = item.get("id")
            if cid is None:
                continue
            # visible=0 تعني دولة مخفية لدى المزود
            if int(item.get("visible", 1) or 1) == 0:
                continue
            eng = item.get("eng") or item.get("rus") or item.get("chn") or str(cid)
            countries.append({"id": str(cid), "eng": str(eng)})

        if not countries:
            raise ProviderAPIError("استجابة getCountries فارغة من HeroSMS")
        return countries

    async def get_country_prices(self, country: str) -> dict:
        """يجلب أسعار كل خدمات دولة واحدة عبر getPrices دون تحديد خدمة.

        استجابة واحدة لكل دولة تكشف توفر واتساب/تيليجرام ومخزونهما
        بدل طلب لكل خدمة على حدة.
        """
        result = await self._request(
            {"action": "getPrices", "country": country}
        )
        return json.loads(result)

    async def get_price(self, country: str, service: str) -> Decimal | None:
        """يجلب سعر التكلفة بالدولار مع فحص المخزون.

        استجابات getPrices تختلف بين النسخ المستنسخة من SMS-Activate:
        1) {country: {service: {cost, count}}}
        2) {service: {cost, count}}            (بدون غلاف الدولة)
        3) {service: {operator: {cost, count}}} (مشغلون متداخلون)

        نطبّع كل الأشكال، نتجاهل المشغلين بلا مخزون (count=0 صراحةً)،
        ونرجل أرخص مشغل متاح.
        """
        result = await self._request(
            {
                "action": "getPrices",
                "country": country,
                "service": service,
            }
        )
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return None

        node = data.get(country, data) if isinstance(data, dict) else {}
        if not isinstance(node, dict):
            return None
        payload = node.get(service)
        if not isinstance(payload, dict):
            return None

        # جمع المرشحين: سعر مباشر أو مشغلون متداخلون
        candidates: list[tuple[object, object]] = []
        if payload.get("cost") is not None:
            candidates.append((payload.get("cost"), payload.get("count")))
        else:
            for op in payload.values():
                if isinstance(op, dict) and op.get("cost") is not None:
                    candidates.append((op.get("cost"), op.get("count")))

        cheapest: Decimal | None = None
        for cost, count in candidates:
            # نفطع فقط من ينص صراحةً أن مخزونه صفر؛ إن غاب العداد نجيزه
            if count is not None:
                try:
                    if int(count) <= 0:
                        continue
                except (TypeError, ValueError):
                    pass
            try:
                cost_usd = self._rub_to_usd(Decimal(str(cost)))
            except Exception:  # noqa: BLE001 - قيمة تالفة لا تُسقط البقية
                continue
            if cheapest is None or cost_usd < cheapest:
                cheapest = cost_usd
        return cheapest

    async def buy_number(
        self,
        country: str,
        service: str,
        operator: str | None = None,
    ) -> PurchasedNumber:
        result = await self._request(
            {
                "action": "getNumber",
                "service": service,
                "country": country,
            }
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
        raise ProviderAPIError(f"فشل شراء رقم من HeroSMS: {result}")

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
            logger.error(f"فشل إلغاء الطلب {order_id} من HeroSMS: {e}")
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
            logger.error(f"فشل إتمام الطلب {order_id} من HeroSMS: {e}")
            return False
