"""
عميل Plisio API لمعالجة مدفوعات USDT التلقائية.

بديل نظيف عن Cryptomus:
- بدون KYC
- يدعم عدة عملات (USDT_TRX, USDT_BSC, BNB)
- Polling للتحقق من الحالة
- واجهة متوافقة مع الكود القديم
"""

import asyncio
import logging
from decimal import Decimal

import aiohttp

from config import settings
from services.payment_gateway_base import PaymentGatewayBase

logger = logging.getLogger(__name__)


class PlisioError(Exception):
    """خطأ عام في Plisio."""

    pass


class PlisioConnectionError(PlisioError):
    """خطأ اتصال مع Plisio."""

    pass


class PlisioAPIError(PlisioError):
    """خطأ من Plisio API."""

    pass


class PlisioClient(PaymentGatewayBase):
    """
    عميل Plisio API.

    الاستخدام:
        client = PlisioClient()
        invoice = await client.create_payment(
            amount=10.5,
            order_id="user_123_1234567890",
            currency="USDT_TRX",
        )
        status = await client.get_payment_info(invoice["uuid"])
    """

    BASE_URL = "https://api.plisio.net/api/v1"

    # حالات Plisio والمقابل لها في نظامنا
    PAID_STATUSES = {"completed", "mismatch"}
    FAILED_STATUSES = {"error", "expired", "cancelled"}
    PENDING_STATUSES = {"new", "pending", "confirming"}

    def __init__(self):
        self.secret_key = settings.PLISIO_SECRET_KEY
        self.base_url = settings.PLISIO_API_URL.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """يحصل على session نشطة."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def close(self):
        """يغلق الـ session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
    ) -> dict:
        """
        يرسل طلب لـ Plisio API.

        Args:
            method: GET أو POST
            endpoint: مسار الـ API
            params: البيانات

        Returns:
            رد Plisio كـ dict
        """
        url = f"{self.base_url}{endpoint}"

        if params is None:
            params = {}
        params["api_key"] = self.secret_key

        try:
            session = await self._get_session()

            if method == "GET":
                async with session.get(url, params=params) as response:
                    data = await response.json()
            else:
                async with session.post(url, data=params) as response:
                    data = await response.json()

            if data.get("status") == "error":
                error_msg = data.get("data", {}).get("message", "Unknown error")
                logger.error(f"Plisio API error: {error_msg}")
                raise PlisioAPIError(error_msg)

            return data.get("data", {})

        except aiohttp.ClientError as e:
            logger.error(f"Plisio connection error: {e}")
            raise PlisioConnectionError(f"فشل الاتصال بـ Plisio: {e}")
        except asyncio.TimeoutError:
            logger.error("Plisio request timeout")
            raise PlisioConnectionError("انتهت مهلة الاتصال بـ Plisio")
        except Exception as e:
            logger.error(f"Plisio unexpected error: {e}")
            raise PlisioError(str(e))

    async def create_payment(
        self,
        amount: Decimal | float | str,
        order_id: str,
        currency: str = "USDT_TRX",
        order_name: str | None = None,
        callback_url: str | None = None,
        email: str | None = None,
        lifetime: int = 1800,
    ) -> dict:
        """
        ينشئ فاتورة دفع جديدة.

        Args:
            amount: المبلغ (بالدولار)
            order_id: معرف الطلب الفريد
            currency: العملة (USDT_TRX, USDT_BSC, BNB)
            order_name: اسم الطلب
            callback_url: رابط الـ callback (اختياري)
            email: بريد العميل (اختياري)
            lifetime: مدة الصلاحية بالثواني (افتراضي: 30 دقيقة)

        Returns:
            dict فيها:
            - uuid: معرف الفاتورة في Plisio
            - address: عنوان الدفع
            - amount: المبلغ المطلوب
            - invoice_total_sum: المبلغ الإجمالي
            - expected_confirmations: عدد التأكيدات
            - qr_code: QR code
            - invoice_url: رابط صفحة الفاتورة
            - expire_utc: وقت الانتهاء
        """
        params = {
            "source_amount": str(amount),
            "source_currency": "USD",
            "order_number": order_id,
            "currency": currency,
        }

        if order_name:
            params["order_name"] = order_name
        else:
            params["order_name"] = f"Deposit {order_id}"

        if callback_url:
            params["callback_url"] = callback_url

        if email:
            params["email"] = email

        result = await self._request("GET", "/invoices/new", params)

        # تحويل الاستجابة لتتوافق مع الواجهة القديمة
        # (لسهولة استبدال Cryptomus بدون تعديل الكود القديم)
        return {
            "uuid": result.get("txn_id"),
            "address": result.get("wallet_hash"),
            "amount": result.get("amount"),
            "payer_amount": result.get("amount"),
            "currency": result.get("currency"),
            "network": self._get_network_name(currency),
            "url": result.get("invoice_url"),
            "qr_code": result.get("qr_code"),
            "expired_at": result.get("expire_utc"),
            "raw": result,
        }

    async def get_payment_info(self, uuid: str) -> dict:
        """
        يجلب معلومات فاتورة.

        Args:
            uuid: معرف الفاتورة (txn_id في Plisio)

        Returns:
            dict فيها:
            - status: حالة الفاتورة
            - amount: المبلغ
            - address: العنوان
            - confirmations: عدد التأكيدات
            - وغيرها
        """
        result = await self._request("GET", f"/operations/{uuid}")

        # تحويل الاستجابة لتتوافق مع Cryptomus
        status_map = {
            "completed": "paid",
            "mismatch": "paid",
            "new": "process",
            "pending": "process",
            "confirming": "check",
            "expired": "cancel",
            "cancelled": "cancel",
            "error": "fail",
        }

        plisio_status = result.get("status", "new")
        mapped_status = status_map.get(plisio_status, plisio_status)

        return {
            "status": mapped_status,
            "uuid": result.get("id"),
            "amount": result.get("amount"),
            "address": result.get("wallet_hash"),
            "confirmations": result.get("confirmations", 0),
            "raw": result,
        }

    def is_paid_status(self, status: str) -> bool:
        """يتحقق إذا كانت الحالة تعني الدفع الناجح."""
        return status in {"paid", "completed", "mismatch"}

    def is_failed_status(self, status: str) -> bool:
        """يتحقق إذا كانت الحالة تعني الفشل."""
        return status in {"fail", "cancel", "expired", "error", "cancelled"}

    def is_pending_status(self, status: str) -> bool:
        """يتحقق إذا كانت الحالة تعني الانتظار."""
        return status in {
            "process",
            "check",
            "new",
            "pending",
            "confirming",
        }

    def _get_network_name(self, currency: str) -> str:
        """يحول رمز العملة لاسم الشبكة."""
        network_map = {
            "USDT_TRX": "TRC20",
            "USDT_BSC": "BEP20",
            "USDT_ETH": "ERC20",
            "BNB": "BEP20",
            "BTC": "BTC",
            "ETH": "ERC20",
        }
        return network_map.get(currency, currency)

    async def get_supported_currencies(self) -> list:
        """يجلب قائمة العملات المدعومة."""
        try:
            result = await self._request("GET", "/currencies")
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.error(f"فشل جلب العملات: {e}")
            return []

    async def get_balance(self, currency: str = "USDT_TRX") -> Decimal:
        """
        يجلب رصيد محفظتك في Plisio.

        Args:
            currency: العملة

        Returns:
            الرصيد كـ Decimal
        """
        try:
            result = await self._request(
                "GET",
                "/balances",
                {"currency": currency},
            )
            balance = result.get("balance", "0")
            return Decimal(str(balance))
        except Exception as e:
            logger.error(f"فشل جلب الرصيد: {e}")
            return Decimal("0")


# ══════════════════════════════════════════════
# ══════════════ Instance جاهز للاستخدام ══════════════
# ══════════════════════════════════════════════

plisio_client = PlisioClient()

# للتوافق مع الكود القديم (استخدام نفس الأسماء)
cryptomus_client = plisio_client  # ⭐ Alias للسهولة
CryptomusError = PlisioError  # ⭐ Alias للاستثناءات
