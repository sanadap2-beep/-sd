"""
مزود أرقام HeroSMS — متوافق بالكامل مع مواصفات HeroSMS API الرسمية.
https://hero-sms.com/stubs/handler_api.php
"""

import json
import logging
from decimal import Decimal

import aiohttp

from providers.base import BaseProvider, PurchasedNumber, OrderStatusResult
from config import settings

logger = logging.getLogger(__name__)

HEROSMS_BASE = "https://hero-sms.com/stubs/handler_api.php"


class ProviderAPIError(Exception):
    pass


def _extract_cost_and_count(data) -> tuple[Decimal | None, int]:
    """
    استخراج سعر التكلفة بالدولار وعدد الأرقام المتاحة حسب توثيق HeroSMS الرسمي.
    """
    if data is None:
        return None, 0

    if isinstance(data, (int, float, str)):
        try:
            return Decimal(str(data)), 1
        except Exception:
            return None, 0

    if isinstance(data, list):
        for item in data:
            c, cnt = _extract_cost_and_count(item)
            if c is not None and cnt > 0:
                return c, cnt

    if isinstance(data, dict):
        if "cost" in data:
            try:
                cost = Decimal(str(data["cost"]))
                count = int(data.get("count", 1) or 1)
                return cost, count
            except Exception:
                pass

        cheapest_cost = None
        total_count = 0
        for val in data.values():
            c, cnt = _extract_cost_and_count(val)
            if c is not None:
                if cheapest_cost is None or c < cheapest_cost:
                    cheapest_cost = c
                total_count += cnt

        return cheapest_cost, total_count

    return None, 0


class HeroSMSProvider(BaseProvider):
    name = "herosms"

    def __init__(self):
        self.api_key = settings.HEROSMS_API_KEY

    async def _request(self, params: dict) -> str:
        if not self.api_key:
            raise ProviderAPIError("HEROSMS_API_KEY غير مضبوط في الإعدادات")
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

    async def get_balance(self) -> Decimal:
        """
        جلب رصيد الحساب بالدولار (ACCESS_BALANCE:100.5).
        """
        result = await self._request({"action": "getBalance"})
        if result.startswith("ACCESS_BALANCE:"):
            try:
                balance_val = Decimal(result.split(":")[1])
                return balance_val.quantize(Decimal("0.0001"))
            except Exception:
                pass
        raise ProviderAPIError(f"استجابة رصيد غير متوقعة من HeroSMS: {result}")

    async def get_countries_services(self) -> list[dict]:
        raise NotImplementedError("يُستخدم get_price مباشرة")

    async def get_countries(self) -> list[dict]:
        """
        جلب قائمة الدول من HeroSMS عبر action=getCountries.
        """
        result = await self._request({"action": "getCountries"})
        try:
            data = json.loads(result)
        except Exception as e:
            raise ProviderAPIError(f"فشل قراءة قائمة الدول من HeroSMS: {e}")

        countries: list[dict] = []
        if isinstance(data, dict):
            iterable = data.values()
        elif isinstance(data, list):
            iterable = data
        else:
            raise ProviderAPIError("صيغة getCountries غير صالحة من HeroSMS")

        for item in iterable:
            if not isinstance(item, dict):
                continue
            cid = item.get("id")
            if cid is None:
                continue
            # إذا كانت الدولة مخفية في المزود نتخطاها
            if int(item.get("visible", 1) or 1) == 0:
                continue
            eng = item.get("eng") or item.get("rus") or item.get("chn") or str(cid)
            countries.append({"id": str(cid), "eng": str(eng)})

        return countries

    async def get_country_prices(self, country: str) -> dict:
        """جلب كل أسعار الخدمات لدولة معينة."""
        result = await self._request(
            {"action": "getPrices", "country": str(country)}
        )
        try:
            return json.loads(result)
        except Exception:
            return {}

    async def get_price(self, country: str, service: str) -> Decimal | None:
        """
        جلب سعر التكلفة المباشر بالدولار ومطابقة المخزون.
        """
        try:
            result = await self._request(
                {
                    "action": "getPrices",
                    "country": str(country),
                    "service": str(service),
                }
            )
            data = json.loads(result)
            
            # معالجة رد getPrices (يدعم القاموس والمصفوفة)
            if isinstance(data, list) and len(data) > 0:
                data = data[0]

            country_dict = data.get(str(country), data) if isinstance(data, dict) else {}
            service_dict = country_dict.get(str(service), country_dict) if isinstance(country_dict, dict) else None

            if service_dict is None:
                return None

            cost_usd, count = _extract_cost_and_count(service_dict)
            if cost_usd is None or count <= 0:
                return None

            return cost_usd.quantize(Decimal("0.0001"))
        except Exception as e:
            logger.debug(f"HeroSMS get_price error (country={country}, service={service}): {e}")
            return None

    async def get_stock_count(self, country: str, service: str) -> int | None:
        """المخزون الحي عبر getNumbersStatus — نفس عقد Grizzly (متوافق sms-activate)."""
        try:
            result = await self._request(
                {
                    "action": "getNumbersStatus",
                    "country": str(country),
                    "service": str(service),
                }
            )
            data = json.loads(result)
            if isinstance(data, dict):
                for key in (f"{country}_{service}", f"{country}_{service}".lower()):
                    if key in data:
                        try:
                            return max(0, int(str(data[key])))
                        except (TypeError, ValueError):
                            pass
                node = data.get(str(country))
                if isinstance(node, dict) and str(service) in node:
                    try:
                        return max(0, int(str(node[str(service)])))
                    except (TypeError, ValueError):
                        pass
                if len(data) == 1:
                    try:
                        return max(0, int(str(next(iter(data.values())))))
                    except (TypeError, ValueError):
                        pass
            return None
        except Exception as e:
            logger.debug(f"HeroSMS getNumbersStatus error: {e}")
            return None

    async def buy_number(
        self,
        country: str,
        service: str,
        operator: str | None = None,
        max_price: Decimal | None = None,
    ) -> PurchasedNumber:
        """
        طلب شراء رقم جديد عبر action=getNumber.
        """
        params = {
            "action": "getNumber",
            "service": str(service),
            "country": str(country),
            "currency": "840",  # دولار أمريكي حسب التوثيق
        }
        if operator:
            params["operator"] = str(operator)
        if max_price is not None:
            params["maxPrice"] = str(max_price)

        result = await self._request(params)

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

        # التعامل مع أخطاء HeroSMS الموثقة
        if "NO_NUMBERS" in result:
            raise ProviderAPIError("لا توجد أرقام متوفرة حالياً لهذه الدولة")
        if "NO_BALANCE" in result:
            raise ProviderAPIError("رصيد حساب المزود HeroSMS غير كافٍ")
        if "BAD_SERVICE" in result or "WRONG_SERVICE" in result:
            raise ProviderAPIError("كود الخدمة غير صالح لدى المزود")
        if "BANNED" in result:
            raise ProviderAPIError("تم تقييد الحساب مؤقتاً لدى المزود")

        raise ProviderAPIError(f"فشل شراء رقم من HeroSMS: {result}")

    async def check_status(self, order_id: str) -> OrderStatusResult:
        """
        فحص حالة الطلب واستلام كود الـ SMS عبر action=getStatus.
        """
        result = await self._request(
            {
                "action": "getStatus",
                "id": str(order_id),
            }
        )
        mapped = "pending"
        code = None

        if result.startswith("STATUS_OK:"):
            code = result.split(":", 1)[1].strip()
            mapped = "code_received"
        elif result in ("STATUS_CANCEL", "ACCESS_CANCEL", "CANCELED"):
            mapped = "cancelled"
        elif result.startswith("STATUS_WAIT_CODE") or result.startswith("STATUS_WAIT_RETRY"):
            mapped = "pending"

        return OrderStatusResult(
            status=mapped,
            sms_code=code,
            full_text=result,
            raw={"raw": result},
        )

    async def cancel_order(self, order_id: str) -> bool:
        """
        إلغاء الطلب واسترجاع الرصيد عبر action=setStatus&status=8.
        """
        try:
            res = await self._request(
                {
                    "action": "setStatus",
                    "id": str(order_id),
                    "status": 8,
                }
            )
            return "ACCESS_CANCEL" in res or "STATUS_CANCEL" in res
        except Exception as e:
            logger.error(f"فشل إلغاء الطلب {order_id} من HeroSMS: {e}")
            return False

    async def finish_order(self, order_id: str) -> bool:
        """
        إتمام الطلب وتأكيد استلام الكود عبر action=setStatus&status=6.
        """
        try:
            res = await self._request(
                {
                    "action": "setStatus",
                    "id": str(order_id),
                    "status": 6,
                }
            )
            return "ACCESS_ACTIVATION" in res
        except Exception as e:
            logger.error(f"فشل إتمام الطلب {order_id} من HeroSMS: {e}")
            return False