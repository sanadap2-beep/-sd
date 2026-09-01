"""
مزود أرقام HeroSMS.
يعمل بنمط SMS-Activate الكلاسيكي (action-based).

ملاحظة العملة (مهمة جداً):
حسب التوثيق الرسمي لـ HeroSMS فإن كل الأسعار والأرصدة بالدولار
الأمريكي (USD) مباشرة — العملة الافتراضية 840 (ISO USD) —
ولا يوجد أي تحويل من الروبل هنا.
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

    async def get_balance(self) -> Decimal:
        """رصيد الحساب بالدولار مباشرة (بدون أي تحويل)."""
        result = await self._request({"action": "getBalance"})
        if result.startswith("ACCESS_BALANCE:"):
            try:
                return Decimal(result.split(":", 1)[1].strip())
            except Exception as exc:  # noqa: BLE001
                raise ProviderAPIError(f"رصيد غير مفهوم من HeroSMS: {result}") from exc
        raise ProviderAPIError(f"استجابة غير متوقعة من HeroSMS: {result}")

    async def get_countries_services(self) -> list[dict]:
        raise NotImplementedError("يُستخدم get_price مباشرة")

    async def get_countries(self) -> list[dict]:
        """يجلب كتالوج الدول من HeroSMS عبر action=getCountries.

        حسب التوثيق الرسمي الاستجابة قائمة:
        [{"id": 2, "rus": "...", "eng": "Kazakhstan", "chn": "...",
          "visible": 1, "retry": 1}, ...]
        وندعم أيضاً الشكل القاموسي القديم للحماية.

        يرجع قائمة بالشكل: [{"id": "2", "eng": "Kazakhstan"}, ...]
        """
        result = await self._request({"action": "getCountries"})
        data = json.loads(result)
        countries: list[dict] = []

        if isinstance(data, dict):
            iterable = data.values()
        elif isinstance(data, list):
            iterable = data
        else:
            raise ProviderAPIError("استجابة getCountries غير مفهومة من HeroSMS")

        for item in iterable:
            if not isinstance(item, dict):
                continue
            cid = item.get("id")
            if cid is None:
                continue
            # visible=0 تعني دولة غير متاحة لدى المزود
            try:
                if int(item.get("visible", 1)) == 0:
                    continue
            except (TypeError, ValueError):
                pass
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

        حسب التوثيق الرسمي، getPrices قد يرجع:
        1) {service: {cost, count, physicalCount}}   (الشكل الموثق)
        2) {country: {service: {cost, count}}}       (شكل SMS-Activate القديم)
        3) [{service: {cost, count}}]                (قائمة كائنات)
        4) {service: {operator: {cost, count}}}      (مشغلون متداخلون)

        نطبّع كل الأشكال، نستبعد من ينص صراحةً أن مخزونه صفر،
        ونرجع أرخص خيار متاح بالدولار كما هو دون أي تحويل.
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

        if isinstance(data, list):
            # قائمة كائنات — ندمجها في قاموس واحد
            merged: dict = {}
            for item in data:
                if isinstance(item, dict):
                    merged.update(item)
            data = merged

        if not isinstance(data, dict):
            return None
        node = data.get(country, data)
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
            # نستبعد فقط من ينص صراحةً أن مخزونه صفر؛ إن غاب العداد نجيزه
            if count is not None:
                try:
                    if int(count) <= 0:
                        continue
                except (TypeError, ValueError):
                    pass
            try:
                cost_usd = Decimal(str(cost))
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
        max_price: Decimal | None = None,
    ) -> PurchasedNumber:
        """يشتري رقماً.

        max_price اختياري: سقف السعر بالدولار (معامل maxPrice الرسمي).
        إن كان سعر المزود لحظة الشراء أعلى منه يرجع WRONG_MAX_PRICE
        ونرفع خطأ ليتحول المدير للمزود التالي بدل الشراء بخسارة.
        """
        params: dict = {
            "action": "getNumber",
            "service": service,
            "country": country,
        }
        if operator:
            params["operator"] = operator
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
            code = result.split(":", 1)[1].strip()
            mapped = "code_received"
        elif result.startswith("STATUS_WAIT_RETRY:"):
            # كود سابق وصل والمستخدم طلب كوداً إضافياً — ما زلنا ننتظر
            mapped = "pending"
        elif result == "STATUS_CANCEL":
            mapped = "cancelled"
        # STATUS_WAIT_CODE / STATUS_WAIT_RESEND تبقى pending

        return OrderStatusResult(
            status=mapped,
            sms_code=code,
            full_text=result,
            raw={"raw": result},
        )

    async def cancel_order(self, order_id: str) -> bool:
        """يلغي الطلب ويرجع True فقط إذا أكده المزود.

        مهم: HeroSMS يرفض الإلغاء خلال أول دقيقتين (EARLY_CANCEL_DENIED)
        أو بعد استلام كود — الرد بـ True خاطئ كان سيؤدي إلى استرجاع
        رصيد المستخدم دون إلغاء حقيقي لدى المزود.
        """
        try:
            result = await self._request(
                {
                    "action": "setStatus",
                    "id": order_id,
                    "status": 8,
                }
            )
        except Exception as e:
            logger.error(f"فشل إلغاء الطلب {order_id} من HeroSMS: {e}")
            return False

        if result in ("ACCESS_CANCEL", "ACCESS_READY"):
            return True
        logger.warning(f"رفض HeroSMS إلغاء الطلب {order_id}: {result}")
        return False

    async def finish_order(self, order_id: str) -> bool:
        """ينهي الطلب ويرجع True فقط إذا أكده المزود."""
        try:
            result = await self._request(
                {
                    "action": "setStatus",
                    "id": order_id,
                    "status": 6,
                }
            )
        except Exception as e:
            logger.error(f"فشل إتمام الطلب {order_id} من HeroSMS: {e}")
            return False

        if result in ("ACCESS_ACTIVATION", "ACCESS_READY"):
            return True
        logger.warning(f"رفض HeroSMS إنهاء الطلب {order_id}: {result}")
        return False
