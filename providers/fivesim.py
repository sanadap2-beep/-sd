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
from time import monotonic

import aiohttp

from providers.base import BaseProvider, PurchasedNumber, OrderStatusResult
from config import settings

logger = logging.getLogger(__name__)

FIVESIM_BASE = "https://5sim.net/v1/"

# حد 5sim: 100 طلب/ثانية لكل IP ثم حظر مؤقت — نمرر الطلبات عبر
# بوابة واحدة مع إعادة محاولة عند 429/503.
import asyncio as _asyncio

_FIVESIM_SEMAPHORE = _asyncio.Semaphore(5)
# لقطة الأسعار الجماعية: طلب واحد لكل منتج يكفي اللوحة كلها (122 دولة
# = طلب واحد بدل 122) — صالحة دقيقتين.
_BULK_TTL_SECONDS = 120


class ProviderAPIError(Exception):
    pass


class FiveSimProvider(BaseProvider):
    name = "fivesim"

    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {settings.FIVESIM_API_KEY}",
            "Accept": "application/json",
        }
        # {product: (timestamp, {slug: operators})}
        self._bulk: dict[str, tuple[float, dict]] = {}

    async def _request(self, method: str, path: str, **kwargs):
        url = FIVESIM_BASE + path
        async with _FIVESIM_SEMAPHORE:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                for attempt in range(3):
                    async with session.request(
                        method,
                        url,
                        timeout=aiohttp.ClientTimeout(total=20),
                        **kwargs,
                    ) as resp:
                        text = await resp.text()
                        if resp.status in (429, 503) and attempt < 2:
                            await _asyncio.sleep(2 * (attempt + 1))
                            continue
                        if resp.status != 200:
                            raise ProviderAPIError(f"5sim error {resp.status}: {text[:200]}")
                        try:
                            return await resp.json(content_type=None)
                        except Exception:
                            return text
        raise ProviderAPIError("5sim: تعذر الاتصال بعد عدة محاولات")

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

    async def _bulk_snapshot(self, product: str) -> dict:
        """لقطة أسعار كل الدول لمنتج واحد (طلب واحد، صالحة دقيقتين).

        اللوحة كانت تجلب 122 طلباً حياً دفعة واحدة فيتعرض معظمها للحظر
        الجزئي فتظهر دول قليلة — الآن طلب واحد يكفي الجميع.
        """
        now = monotonic()
        cached = self._bulk.get(str(product))
        if cached and now - cached[0] < _BULK_TTL_SECONDS:
            return cached[1]
        data = await self._request("GET", f"guest/prices?product={product}")
        node = data.get(str(product)) if isinstance(data, dict) else None
        node = node if isinstance(node, dict) else {}
        self._bulk[str(product)] = (now, node)
        return node

    @staticmethod
    def _cheapest(ops) -> Decimal | None:
        if not isinstance(ops, dict):
            return None
        cheapest = None
        for info in ops.values():
            if not isinstance(info, dict):
                continue
            try:
                count = int(info.get("count", 0) or 0)
                cost = Decimal(str(info.get("cost", 0)))
            except Exception:
                continue
            if count > 0 and cost > 0 and (cheapest is None or cost < cheapest):
                cheapest = cost
        return cheapest.quantize(Decimal("0.0001")) if cheapest is not None else None

    async def get_price(self, country: str, service: str) -> Decimal | None:
        try:
            node = await self._bulk_snapshot(service)
            price = self._cheapest(node.get(str(country)))
            if price is not None:
                return price
            # اللقطة لا تحوي الدولة (اسم بديل؟) — طلب مباشر احتياطي
            data = await self._request("GET", f"guest/prices?country={country}&product={service}")
            return self._cheapest(self._extract_service_node(data, country, service))
        except Exception as e:
            logger.debug(f"5sim get_price error (country={country}, service={service}): {e}")
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
