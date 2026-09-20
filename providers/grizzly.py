"""
مزود أرقام GrizzlySMS.
https://grizzlysms.com/docs — الـ API متوافق مع sms-activate.

لماذا GrizzlySMS؟
- واتساب يُباع openly وبدون شروط أهلية (عكس SMSPool الذي يطلب حساباً
  مؤهلاً)، ومخزون ضخم بأكثر من 100 دولة.
- الأسعار واضحة: تُعرض لكل دولة/خدمة على الموقع وعبر getPrices، وتبدأ
  من ~$0.04 مع استرداد تلقائي إذا لم تصل رسالة.
- الشحن بالكارت والكريبتو (USDT وغيرها) وطرق محلية.

العملة: الدولار مباشرة (أسعار الموقع والتطبيق بالـ $، ومثال التوثيق
``activationCost: 0.4`` بالدولار). لا يوجد تحويل روبل هنا — إذا لاحظت
فرقاً بين رصيد اللوحة ورصيد البوت، أخبرني لأضبط المعامل.

ملاحظات سلوكية موثقة من الـ API الحقيقي:
- جدول الأسعار قد يحوي مخزوناً وهمياً (count كبير وسعر منخفض ثم
  NO_NUMBERS عند الشراء) — مدير المزودين عندنا يتجاوز تلقائياً للمزود
  التالي عند الفشل.
- الإلغاء المبكر قد يُرفض أول ~دقيقتين (EARLY_CANCEL_DENIED) — نرجع
  False ليُعاد الطلب ضمن مهلة الطلب.
"""

import json
import logging
from decimal import Decimal

import asyncio as _asyncio

import aiohttp

from providers.base import BaseProvider, PurchasedNumber, OrderStatusResult
from config import settings

logger = logging.getLogger(__name__)

GRIZZLY_BASE = "https://api.grizzlysms.com/stubs/handler_api.php"

# بوابة طلبات واحدة: فحص 150 دولة × خدمتين بالتوازي الكامل قد
# يُقابل بحد مزود — نحد التوازي بدل الحظر.
_GRIZZLY_SEMAPHORE = _asyncio.Semaphore(5)


class ProviderAPIError(Exception):
    pass


class GrizzlyProvider(BaseProvider):
    name = "grizzly"

    def __init__(self):
        self.api_key = settings.GRIZZLY_API_KEY

    async def _request(self, params: dict) -> str:
        if not self.api_key:
            raise ProviderAPIError("GRIZZLY_API_KEY غير مضبوط في الإعدادات")
        params = {"api_key": self.api_key, **params}
        async with _GRIZZLY_SEMAPHORE:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    GRIZZLY_BASE,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        raise ProviderAPIError(f"Grizzly error {resp.status}: {text[:300]}")
                    return text.strip()

    async def get_balance(self) -> Decimal:
        result = await self._request({"action": "getBalance"})
        if result.startswith("ACCESS_BALANCE:"):
            try:
                return Decimal(result.split(":")[1]).quantize(Decimal("0.0001"))
            except Exception:
                pass
        if result in ("BAD_KEY", "NO_KEY"):
            raise ProviderAPIError("مفتاح Grizzly غير صالح (تحقق من GRIZZLY_API_KEY)")
        raise ProviderAPIError(f"استجابة رصيد غير متوقعة من Grizzly: {result[:200]}")

    async def get_countries_services(self) -> list[dict]:
        raise NotImplementedError("يُستخدم get_price مباشرة")

    async def list_countries(self) -> list[str]:
        """قائمة مرجعية بأكواد الدول (للوحة الأدمن فقط)."""
        try:
            result = await self._request({"action": "getCountries"})
            data = json.loads(result)
            out = []
            items = data.values() if isinstance(data, dict) else data
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                cid = item.get("id")
                if cid is None:
                    continue
                if int(item.get("visible", 1) or 1) == 0:
                    continue
                name = item.get("eng") or item.get("rus") or str(cid)
                out.append(f"<code>{cid}</code> — {name}")
            if out:
                return sorted(out)[:60]
        except Exception as e:
            logger.debug(f"Grizzly list_countries error: {e}")
        return ["انسخ كود الدولة الرقمي من موقع Grizzly (مثال: <code>12</code>)"]

    async def get_price(self, country: str, service: str) -> Decimal | None:
        try:
            result = await self._request(
                {
                    "action": "getPrices",
                    "country": str(country),
                    "service": str(service),
                }
            )
            data = json.loads(result)
            if isinstance(data, list) and data:
                data = data[0]
            country_dict = data.get(str(country), data) if isinstance(data, dict) else {}
            service_dict = (
                country_dict.get(str(service), country_dict)
                if isinstance(country_dict, dict)
                else None
            )
            if not isinstance(service_dict, dict):
                return None
            cheapest: Decimal | None = None
            for val in service_dict.values():
                if not isinstance(val, dict):
                    continue
                try:
                    cost = Decimal(str(val["cost"]))
                    count = int(val.get("count", 1) or 1)
                except Exception:
                    continue
                if count > 0 and (cheapest is None or cost < cheapest):
                    cheapest = cost
            if cheapest is None:
                return None
            return cheapest.quantize(Decimal("0.0001"))
        except Exception as e:
            logger.debug(f"Grizzly get_price error (country={country}, service={service}): {e}")
            return None

    async def buy_number(
        self,
        country: str,
        service: str,
        operator: str | None = None,
        max_price: Decimal | None = None,
    ) -> PurchasedNumber:
        params = {
            "action": "getNumber",
            "service": str(service),
            "country": str(country),
        }
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
        if "NO_NUMBERS" in result:
            raise ProviderAPIError("لا توجد أرقام متوفرة حالياً (مخزون وهمي محتمل — جرّب دولة أخرى)")
        if "NO_BALANCE" in result:
            raise ProviderAPIError("رصيد Grizzly غير كافٍ — اشحن المحفظة")
        if result in ("BAD_KEY", "NO_KEY"):
            raise ProviderAPIError("مفتاح Grizzly غير صالح")
        if "BAD_SERVICE" in result or "prohibited" in result:
            raise ProviderAPIError("الخدمة غير مدعومة لدى Grizzly")
        raise ProviderAPIError(f"فشل شراء رقم من Grizzly: {result[:200]}")

    async def check_status(self, order_id: str) -> OrderStatusResult:
        result = await self._request({"action": "getStatus", "id": str(order_id)})
        mapped = "pending"
        code = None
        if result.startswith("STATUS_OK:"):
            code = result.split(":", 1)[1].strip()
            mapped = "code_received"
        elif result in ("STATUS_CANCEL", "ACCESS_CANCEL"):
            mapped = "cancelled"
        elif result.startswith(("STATUS_WAIT_CODE", "STATUS_WAIT_RETRY")):
            mapped = "pending"
        return OrderStatusResult(
            status=mapped,
            sms_code=code,
            full_text=result,
            raw={"raw": result},
        )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            res = await self._request(
                {"action": "setStatus", "id": str(order_id), "status": 8}
            )
            # EARLY_CANCEL_DENIED = ما زال ضمن قفل الدقيقتين — يُعاد ضمن مهلة الطلب
            return "ACCESS_CANCEL" in res or "STATUS_CANCEL" in res
        except Exception as e:
            logger.error(f"فشل إلغاء الطلب {order_id} من Grizzly: {e}")
            return False

    async def finish_order(self, order_id: str) -> bool:
        try:
            res = await self._request(
                {"action": "setStatus", "id": str(order_id), "status": 6}
            )
            return "ACCESS_ACTIVATION" in res
        except Exception as e:
            logger.error(f"فشل إتمام الطلب {order_id} من Grizzly: {e}")
            return False
