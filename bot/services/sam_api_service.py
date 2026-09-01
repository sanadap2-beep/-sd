"""
خدمة Sam API لدفع شام كاش التلقائي.
https://www.sam-api.pro/api

الخصائص:
1) إنشاء فاتورة صالحة لـ 15 دقيقة.
2) التحقق من الدفع عبر رقم العملية (بدون webhook).
3) جلب حالة الفاتورة.
4) جلب رصيد المحفظة.
"""

import logging
from decimal import Decimal

import aiohttp

from config import settings
from services.payment_gateway_base import PaymentGatewayBase

logger = logging.getLogger(__name__)


class SamApiError(Exception):
    pass


class SamApiExpiredError(SamApiError):
    pass


class SamApiClient:
    """
    عميل Sam API.
    يستخدم API Key من config.SAM_API_KEY.
    """

    def __init__(self):
        self.api_key = settings.SAM_API_KEY
        self.base_url = settings.SAM_API_URL.rstrip("/")
        self.wallet_address = settings.SAM_API_WALLET_ADDRESS

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict | None = None,
        use_auth: bool = True,
    ) -> dict:
        url = self.base_url + path
        headers = (
            self._get_headers()
            if use_auth
            else {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.request(
                    method,
                    url,
                    json=json_data,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    text = await resp.text()

                    if resp.status == 410:
                        raise SamApiExpiredError("انتهت صلاحية الفاتورة")

                    if resp.status not in (200, 201):
                        try:
                            error_data = await resp.json(content_type=None)
                            error_msg = error_data.get(
                                "message",
                                error_data.get(
                                    "error",
                                    text[:200],
                                ),
                            )
                        except Exception:
                            error_msg = text[:200]
                        raise SamApiError(f"Sam API خطأ {resp.status}: {error_msg}")

                    try:
                        return await resp.json(content_type=None)
                    except Exception:
                        raise SamApiError(f"استجابة غير صالحة: {text[:200]}")
        except aiohttp.ClientError as e:
            raise SamApiError(f"خطأ اتصال مع Sam API: {e}")

    async def get_wallets(self) -> list[dict]:
        """يجلب كل المحافظ المربوطة بالحساب."""
        return await self._request("GET", "/v1/wallets")

    async def get_wallet_balance(self, wallet_address: str | None = None) -> list[dict]:
        """
        يجلب رصيد المحفظة.
        يعيد قائمة بالعملات المختلفة.
        """
        address = wallet_address or self.wallet_address
        if not address:
            raise SamApiError("لم يتم تحديد عنوان المحفظة")
        return await self._request(
            "GET",
            f"/v1/wallets/shamcash/{address}/balance",
        )

    async def get_wallet_transactions(
        self,
        wallet_address: str | None = None,
        direction: str = "all",
    ) -> list[dict]:
        """
        يجلب معاملات المحفظة.
        direction: in | out | all
        """
        address = wallet_address or self.wallet_address
        if not address:
            raise SamApiError("لم يتم تحديد عنوان المحفظة")
        return await self._request(
            "GET",
            f"/v1/wallets/shamcash/{address}/transactions?direction={direction}",
        )

    async def create_invoice(
        self,
        amount: Decimal,
        currency: str,
        webhook_url: str = "https://example.com/no-webhook",
    ) -> dict:
        """
        ينشئ فاتورة دفع جديدة.

        amount: المبلغ (Decimal)
        currency: USD أو SYP أو EUR
        webhook_url: رابط webhook (مطلوب في API لكن يمكن أن يكون وهمياً
                     لأننا نستخدم verify يدوياً)

        يرجع dict يحتوي:
        - invoiceId
        - paymentUrl
        - expiresAt
        """
        if not self.wallet_address:
            raise SamApiError("لم يتم تحديد عنوان محفظة الاستلام في .env")

        currency_upper = currency.upper()
        if currency_upper not in ("USD", "SYP", "EUR"):
            raise SamApiError(f"عملة غير مدعومة: {currency}")

        return await self._request(
            "POST",
            "/v1/invoices",
            json_data={
                "method": "shamcash",
                "identifier": self.wallet_address,
                "amount": str(amount),
                "currency": currency_upper,
                "webhookUrl": webhook_url,
            },
        )

    async def get_invoice_status(self, invoice_id: str) -> dict:
        """
        يجلب بيانات الفاتورة الكاملة.

        يرجع dict يحتوي:
        - id
        - method
        - identifier
        - amount
        - currency
        - status (pending / paid / expired)
        - expiresAt
        - createdAt
        - paidAt
        """
        return await self._request(
            "GET",
            f"/pay/{invoice_id}",
            use_auth=False,
        )

    async def verify_invoice(
        self,
        invoice_id: str,
        transaction_ref: str,
    ) -> dict:
        """
        يتحقق من الدفع عبر رقم العملية.

        يرجع dict يحتوي:
        - verified: True/False
        - message: رسالة توضيحية

        قد يرمي:
        - SamApiExpiredError: إذا انتهت الفاتورة
        - SamApiError: للأخطاء الأخرى
        """
        return await self._request(
            "POST",
            f"/pay/{invoice_id}/verify",
            json_data={
                "transactionRef": transaction_ref.strip(),
            },
            use_auth=False,
        )


class SamCashGateway(PaymentGatewayBase):
    """Adapter that gives SamCash the common gateway contract."""

    name = "samcash"

    def __init__(self, client: SamApiClient | None = None):
        self.client = client or sam_api_client

    async def create_payment(self, amount: Decimal, order_id: str, **kwargs) -> dict:
        return await self.client.create_invoice(
            amount,
            kwargs.get("currency", "USD"),
            kwargs.get("webhook_url", "https://example.com/no-webhook"),
        )

    async def get_payment_info(self, external_id: str) -> dict:
        return await self.client.get_invoice_status(external_id)

    def is_paid_status(self, status: str) -> bool:
        return status.lower() in {"paid", "completed", "success"}


sam_api_client = SamApiClient()
samcash_gateway = SamCashGateway(sam_api_client)
