"""
مزود أرقام 5sim.

تحديث 2026: غيّرت 5sim الـ API:
- ``guest/prices`` صار يطلب **رقم الدولة** (``country=16``) بدل الاسم
  (``country=russia`` يرجع ``400 country is incorrect``) — نفس ترقيم
  sms-activate ‏(12=أمريكا، 16=بريطانيا، 21=مصر...).
- الأسعار صارت **بالدولار مباشرة** (0.92$ مثلاً) بدل الروبل —
  انتهى التحويل القديم (‎/100).
- الرد قد يكون ``null`` عند غياب المخزون/الدولة.

لذلك ``fivesim_code`` للدول هو الاسم (slug) حسب التوثيق الرسمي
(``"england"``) — صالح للأسعار والشراء معاً.
ملاحظة: روسيا وسوريا محذوفتان من كتالوج 5sim (غير متوفرتين).
"""

import logging
from decimal import Decimal

import aiohttp

from providers.base import BaseProvider, PurchasedNumber, OrderStatusResult
from config import settings

logger = logging.getLogger(__name__)

FIVESIM_BASE = "https://5sim.net/v1/"

# حد 5sim: 100 طلب/ثانية لكل IP ثم حظر مؤقت — نمرر الطلبات عبر
# بوابة واحدة حتى لا يحظر السحب/اللوحة أنفسهما بالطلبات المتوازية.
import asyncio as _asyncio

_FIVESIM_SEMAPHORE = _asyncio.Semaphore(5)


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
        async with _FIVESIM_SEMAPHORE:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.request(
                    method,
                    url,
                    timeout=aiohttp.ClientTimeout(total=20),
                    **kwargs,
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        raise ProviderAPIError(f"5sim error {resp.status}: {text[:200]}")
                    try:
                        return await resp.json(content_type=None)
                    except Exception:
                        return text

    @staticmethod
    def _to_usd(value) -> Decimal | None:
        """الأسعار بالدولار مباشرة (تحديث API ‏2026)."""
        try:
            return Decimal(str(value)).quantize(Decimal("0.0001"))
        except Exception:
            return None

    async def get_balance(self) -> Decimal:
        data = await self._request("GET", "user/profile")
        balance = self._to_usd(data.get("balance", 0))
        return balance if balance is not None else Decimal("0")

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

    async def get_country_prices(self, country: str) -> dict:
        """كل أسعار المنتجات لدولة رقمية واحدة (مفتاح الرد اسم الدولة)."""
        data = await self._request("GET", f"guest/prices?country={country}")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _extract_service_node(data, country: str, service: str):
        """يستخرج عقدة الخدمة — الرد مفتاحه اسم الدولة لا رقمها."""
        if not isinstance(data, dict) or not data:
            return None
        node = data.get(str(country))
        if node is None:
            # الـ API الجديد يفتح باسم الدولة (england) لا برقمها (16)
            dict_vals = [v for v in data.values() if isinstance(v, dict)]
            if len(dict_vals) == 1:
                node = dict_vals[0]
            else:
                return None
        if not isinstance(node, dict):
            return None
        return node.get(str(service))

    async def get_price(self, country: str, service: str) -> Decimal | None:
        try:
            data = await self._request("GET", f"guest/prices?country={country}&product={service}")
        except Exception as e:
            logger.debug(f"5sim get_price error (country={country}, service={service}): {e}")
            return None
        try:
            service_node = self._extract_service_node(data, country, service)
            if not isinstance(service_node, dict):
                return None
            cheapest = None
            for operator, info in service_node.items():
                if not isinstance(info, dict):
                    continue
                if int(info.get("count", 0) or 0) > 0:
                    cost_usd = self._to_usd(info.get("cost"))
                    if cost_usd is not None and cost_usd > 0 and (
                        cheapest is None or cost_usd < cheapest
                    ):
                        cheapest = cost_usd
            return cheapest
        except (KeyError, TypeError, ValueError):
            return None

    async def buy_number(
        self,
        country: str,
        service: str,
        operator: str = "any",
        max_price: Decimal | None = None,  # 5sim لا يدعم سقف السعر — يُتجاهل
    ) -> PurchasedNumber:
        data = await self._request("GET", f"user/buy/activation/{country}/{operator}/{service}")
        cost_usd = self._to_usd(data.get("price", 0)) or Decimal("0")
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
        elif status == "BANNED":
            # الرقم مستخدم مسبقاً — إنهاء الطلب بدل تعليقه للأبد
            mapped = "cancelled"

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
