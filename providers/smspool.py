"""
مزود أرقام SMSPool — هولندا (SMSPool B.V.).
https://www.smspool.net / https://smspool.org/api

لماذا SMSPool؟
- أرقام non-VoIP حقيقية (مش VoIP رخيص) → نسبة قبول أعلى بكثير على
  واتساب/تيليجرام/غوغل التي ترفض أرقام HeroSMS/5sim الرخيصة برسالة
  "لا يمكننا تسجيل دخولك".
- الأسعار بالدولار مباشرة (بلا تحويل روبل).
- وضع الجودة العالية إجباري: كل طلب شراء يُرسل مع
  ``pricing_option=1`` أي "أعلى نسبة نجاح" بدل الأرخص، حتى لو كان أغلى.

الـ API أصلي (REST + JSON) على ``https://api.smspool.net``:
- GET /request/balance?key=XXX            → {"balance": "12.34"} (دولار)
- GET /purchase/sms?key&country&service&pricing_option=1[&pool&max_price]
                                          → {success:1, cc, phonenumber,
                                             order_id, cost, expires_in}
- GET /sms/check?key&orderid=XXX          → {status: 1..8, sms, full_sms}
  حالات الطلب: 1 pending, 2 expired, 3 completed, 4 resend,
               5 cancelled, 6 refunded, 7 processing, 8 activating
- GET /sms/cancel?key&orderid=XXX         → {success:1}
- الإنهاء بعد استلام الكود لا يحتاج调用 (الطلب يُؤرشف تلقائياً).

السعر المعروض (get_price) يُجلب عبر واجهة التوافق مع SMS-Activate:
- GET https://api.smspool.net/stubs/handler_api.php?action=getPrices&...
  مع ``setting=smspool`` لاستعمال معرفات SMSPool الأصلية، ونأخذ أرخص
  مشغّل متاح. العملة دولار مباشرة.
"""

import json
import logging
from decimal import Decimal

import aiohttp

from providers.base import BaseProvider, PurchasedNumber, OrderStatusResult
from config import settings

logger = logging.getLogger(__name__)

SMSPOOL_API = "https://api.smspool.net"
SMSPOOL_STUBS = "https://api.smspool.net/stubs/handler_api.php"


class ProviderAPIError(Exception):
    pass


class SMSPoolProvider(BaseProvider):
    """مزود SMSPool بوضع الجودة العالية دائماً."""

    name = "smspool"

    def __init__(self):
        self.api_key = settings.SMSPOOL_API_KEY

    def _ensure_key(self):
        if not self.api_key:
            raise ProviderAPIError("SMSPOOL_API_KEY غير مضبوط في الإعدادات")

    async def _native(self, endpoint: str, params: dict) -> dict:
        """طلب GET على الـ API الأصلي، يرجع JSON dict."""
        self._ensure_key()
        query = {"key": self.api_key, **params}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                SMSPOOL_API + endpoint,
                params=query,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise ProviderAPIError(f"SMSPool error {resp.status}: {text[:300]}")
                try:
                    data = json.loads(text)
                except Exception as e:
                    raise ProviderAPIError(f"رد غير صالح من SMSPool: {text[:300]} ({e})")
                if isinstance(data, dict):
                    return data
                return {"_raw": data}

    async def get_balance(self) -> Decimal:
        data = await self._native("/request/balance", {})
        raw = data.get("balance", data.get("_raw"))
        try:
            return Decimal(str(raw)).quantize(Decimal("0.0001"))
        except Exception:
            raise ProviderAPIError(f"استجابة رصيد غير متوقعة من SMSPool: {data}")

    async def get_countries_services(self) -> list[dict]:
        raise NotImplementedError("يُستخدم get_price مباشرة")

    async def list_countries(self) -> list[str]:
        """قائمة مرجعية للدول (للوحة الأدمن فقط)."""
        for endpoint in ("/request/countries", "/request/country"):
            try:
                data = await self._native(endpoint, {})
                items = data if isinstance(data, list) else data.get("_raw") or []
                out = []
                if isinstance(items, list):
                    for item in items[:60]:
                        if isinstance(item, dict):
                            code = item.get("ID") or item.get("id") or item.get("code")
                            name = item.get("name") or code
                            out.append(f"<code>{code}</code> — {name}")
                if out:
                    return sorted(out)
            except Exception:
                continue
        return ["انسخ كود الدولة من لوحة SMSPool (مثال: <code>US</code> أو <code>United States</code>)"]

    async def get_price(self, country: str, service: str) -> Decimal | None:
        """أرخص سعر متاح بالدولار عبر واجهة getPrices المتوافقة."""
        self._ensure_key()
        params = {
            "api_key": self.api_key,
            "action": "getPrices",
            "country": str(country),
            "service": str(service),
            "setting": "smspool",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    SMSPOOL_STUBS,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        return None
                    try:
                        data = json.loads(text)
                    except Exception:
                        return None
            cheapest: Decimal | None = None

            def _walk(node) -> None:
                nonlocal cheapest
                if isinstance(node, dict):
                    if "cost" in node:
                        try:
                            cost = Decimal(str(node["cost"]))
                            count = int(node.get("count", 1) or 1)
                            if count > 0 and (cheapest is None or cost < cheapest):
                                cheapest = cost
                        except Exception:
                            pass
                    for val in node.values():
                        _walk(val)
                elif isinstance(node, list):
                    for val in node:
                        _walk(val)

            # نطاق الدولة/الخدمة أولاً، ثم كل الشجرة كاحتياط
            scoped = data
            if isinstance(data, dict):
                scoped = data.get(str(country), data)
                if isinstance(scoped, dict):
                    scoped = scoped.get(str(service), scoped)
            _walk(scoped)
            if cheapest is None and scoped is not data:
                _walk(data)
            if cheapest is None:
                return None
            return cheapest.quantize(Decimal("0.0001"))
        except Exception as e:
            logger.debug(f"SMSPool get_price error (country={country}, service={service}): {e}")
            return None

    async def buy_number(
        self,
        country: str,
        service: str,
        operator: str | None = None,
        max_price: Decimal | None = None,
    ) -> PurchasedNumber:
        """شراء رقم بوضع أعلى نسبة نجاح (pricing_option=1) دائماً.

        ``operator`` يُفسَّر كـ pool في SMSPool (اختياري، يُترك فارغاً
        للاختيار التلقائي لأفضل pool).
        """
        params: dict = {
            "country": str(country),
            "service": str(service),
            # 1 = أعلى نسبة نجاح (أغلى)، 0 = الأرخص. نثبت 1 للجودة.
            "pricing_option": 1,
        }
        if operator:
            params["pool"] = str(operator)
        if max_price is not None:
            params["max_price"] = str(max_price)
        data = await self._native("/purchase/sms", params)
        if data.get("success") == 1 and data.get("order_id"):
            cc = str(data.get("cc", "") or "")
            number = str(data.get("number") or data.get("phonenumber") or "")
            phone = f"+{cc}{number}" if cc and number and not number.startswith("+") else (number or cc)
            try:
                cost = Decimal(str(data.get("cost", "0") or "0")).quantize(Decimal("0.0001"))
            except Exception:
                cost = Decimal("0")
            return PurchasedNumber(
                provider_order_id=str(data["order_id"]),
                phone_number=phone,
                cost_usd=cost,
                raw=data,
            )
        msg = data.get("message") or data.get("error") or json.dumps(data)[:300]
        raise ProviderAPIError(f"فشل شراء رقم من SMSPool: {msg}")

    async def check_status(self, order_id: str) -> OrderStatusResult:
        data = await self._native("/sms/check", {"orderid": str(order_id)})
        try:
            status_code = int(data.get("status", 1))
        except Exception:
            status_code = 1
        sms_code = data.get("sms") or None
        if isinstance(sms_code, str) and sms_code.strip() in ("0", "", "null"):
            sms_code = None
        full_text = data.get("full_sms") or data.get("full_code") or None

        mapped = "pending"
        if sms_code or status_code == 3:
            mapped = "code_received"
        elif status_code in (5, 6):
            mapped = "cancelled"
        elif status_code == 2:
            mapped = "expired"

        return OrderStatusResult(
            status=mapped,
            sms_code=sms_code,
            full_text=str(full_text) if full_text else json.dumps(data)[:500],
            raw=data,
        )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            data = await self._native("/sms/cancel", {"orderid": str(order_id)})
            return data.get("success") == 1
        except Exception as e:
            logger.error(f"فشل إلغاء الطلب {order_id} من SMSPool: {e}")
            return False

    async def finish_order(self, order_id: str) -> bool:
        """SMSPool لا يحتاج إنهاء صريح — الطلب يُؤرشف تلقائياً بعد الكود."""
        return True
