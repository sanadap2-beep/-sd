"""
قسم واتساب: اشتراك يومي + ربط جلسة واتساب عبر «الجسر» (البوت الثاني).

كيف يعمل الربط؟
  هذا البوت لا يتكلم مع واتساب مباشرة — بل مع بوتك الثاني عبر HTTP.
  البوت الثاني (الذي يدير جلسات واتساب) يوفّر واجهة صغيرة (عقد الجسر v1):

    X-Bridge-Key: <wa_bridge_api_key>          في كل الطلبات
    GET    /health                             → {"ok": true}
    POST   /sessions            {"phone": ...} → {"session_id","status","pairing_code"}
    GET    /sessions/{id}                      → {"status": pending|linked|...}
    POST   /sessions/{id}/command {"action"}   → {"text","buttons":[{"text","action"}]}
    DELETE /sessions/{id}                      → {"ok": true}

  بمجرد أن يطبّق مطوّر البوت الثاني هذه المسارات الخمسة (موجود تنفيذ
  مرجعي كامل في scripts/wa_bridge_reference_server.py) تظهر أزرار
  البوت الثاني هنا داخل هذا البوت: نعرض القائمة التي يرجعها الجسر،
  ونمرّر ضغطات المستخدم إليه، ونعيد له رده — أي أن كل أوامر البوت
  الثاني تعمل من داخل هذا البوت بدون فتحه.

كل الإعدادات من لوحة الأدمن (جدول settings):
  wa_bridge_enabled     — تفعيل/تعطيل القسم بالكامل
  wa_bridge_base_url    — عنوان سيرفر الجسر
  wa_bridge_api_key     — مفتاح المصادقة
  wa_daily_price        — سعر اليوم الواحد (افتراضي 1.00$)
  wa_description        — وصف القسم الذي كتبه الأدمن (يظهر للمستخدم)
  wa_bot_username       — (اختياري) يوزر البوت الثاني كرابط مباشر احتياطي
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import aiohttp
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TransactionType, User, WALinkStatus, WhatsAppLink, WhatsAppSubscription
from services.balance_service import BalanceService, InsufficientBalanceError
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

PHONE_RE = re.compile(r"^\+?\d{8,15}$")


class WABridgeError(Exception):
    """خطأ صالح للعرض على المستخدم."""


def normalize_phone(raw: str) -> str:
    phone = (raw or "").strip().replace(" ", "").replace("-", "")
    if not PHONE_RE.match(phone):
        raise WABridgeError(
            "صيغة الرقم غير صحيحة. أرسل الرقم بالصيغة الدولية بدون مسافات، مثال: +963955123456"
        )
    if not phone.startswith("+"):
        phone = f"+{phone}"
    return phone


# ══════════════ إعدادات القسم ══════════════


class WASettings:
    @staticmethod
    async def enabled() -> bool:
        return await SettingsService.get_bool("wa_bridge_enabled", False)

    @staticmethod
    async def base_url() -> str:
        return ((await SettingsService.get("wa_bridge_base_url", "")) or "").strip().rstrip("/")

    @staticmethod
    async def api_key() -> str:
        return ((await SettingsService.get("wa_bridge_api_key", "")) or "").strip()

    @staticmethod
    async def daily_price() -> Decimal:
        from decimal import Decimal

        return await SettingsService.get_decimal("wa_daily_price", Decimal("1.0"))

    @staticmethod
    async def description() -> str:
        return (await SettingsService.get("wa_description", "")) or ""

    @staticmethod
    async def bot_username() -> str:
        return ((await SettingsService.get("wa_bot_username", "")) or "").strip().lstrip("@")

    @staticmethod
    async def configured() -> bool:
        return bool(await WASettings.base_url()) and bool(await WASettings.api_key())


# ══════════════ عميل الجسر ══════════════


class WABridgeClient:
    @staticmethod
    async def _request(method: str, path: str, payload: dict | None = None, timeout: int = 30) -> dict:
        base_url = await WASettings.base_url()
        api_key = await WASettings.api_key()
        if not base_url or not api_key:
            raise WABridgeError("جسر واتساب غير مضبوط. راجع الإدارة.")

        url = f"{base_url}{path}"
        headers = {"X-Bridge-Key": api_key, "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as http:
                async with http.request(method, url, json=payload, headers=headers) as response:
                    if response.status == 401:
                        raise WABridgeError("مفتاح جسر واتساب غير صالح (401).")
                    if response.status == 404:
                        raise WABridgeError("الجلسة غير موجودة عند الجسر (404).")
                    if response.status >= 400:
                        body = (await response.text())[:200]
                        logger.warning("WA bridge %s %s → %s: %s", method, path, response.status, body)
                        raise WABridgeError(f"سيرفر الجسر رجع خطأ ({response.status}).")
                    data = await response.json(content_type=None)
                    return data if isinstance(data, dict) else {}
        except WABridgeError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("فشل الاتصال بجسر واتساب: %s", exc)
            raise WABridgeError("تعذّر الاتصال بسيرفر الجسر. تأكد أنه يعمل.") from exc

    @staticmethod
    async def health() -> dict:
        return await WABridgeClient._request("GET", "/health", timeout=15)

    @staticmethod
    async def start_session(phone: str) -> dict:
        """يطلب كود اقتران لرقم جديد من الجسر."""
        return await WABridgeClient._request("POST", "/sessions", {"phone": phone})

    @staticmethod
    async def session_status(bridge_session_id: str) -> dict:
        return await WABridgeClient._request("GET", f"/sessions/{bridge_session_id}")

    @staticmethod
    async def send_command(bridge_session_id: str, action: str, text: str | None = None) -> dict:
        payload: dict = {"action": action}
        if text:
            payload["text"] = text[:4000]
        return await WABridgeClient._request(
            "POST", f"/sessions/{bridge_session_id}/command", payload, timeout=60
        )

    @staticmethod
    async def logout(bridge_session_id: str) -> dict:
        return await WABridgeClient._request("DELETE", f"/sessions/{bridge_session_id}")


# ══════════════ الاشتراك اليومي ══════════════


class WASubscriptionService:
    @staticmethod
    async def get(db: AsyncSession, user_id: int) -> WhatsAppSubscription | None:
        result = await db.execute(
            select(WhatsAppSubscription).where(WhatsAppSubscription.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def active_until(db: AsyncSession, user_id: int) -> datetime | None:
        sub = await WASubscriptionService.get(db, user_id)
        if sub is None:
            return None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return sub.paid_until if sub.paid_until > now else None

    @staticmethod
    async def subscribe(db: AsyncSession, user: User, days: int = 1) -> tuple[WhatsAppSubscription, Decimal]:
        """
        يدفع سعر اليوم × عدد الأيام من الرصيد ويمدّ الاشتراك.
        التمديد يُحسب من نهاية الاشتراك الحالي إن كان ما زال فعالاً.
        """
        from decimal import Decimal

        price_per_day = await WASettings.daily_price()
        amount = (price_per_day * days).quantize(Decimal("0.01"))
        if amount <= 0:
            raise WABridgeError("سعر الاشتراك غير مضبوط. راجع الإدارة.")

        balance = await BalanceService.get_balance(db, user.id)
        if balance < amount:
            raise InsufficientBalanceError(
                f"رصيدك غير كافٍ. اشتراك يوم = ${amount}، ورصيدك ${balance}."
            )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        sub = await WASubscriptionService.get(db, user.id)
        base = sub.paid_until if (sub and sub.paid_until > now) else now
        paid_until = base + timedelta(days=days)

        if sub is None:
            sub = WhatsAppSubscription(user_id=user.id)
            db.add(sub)
        sub.paid_until = paid_until
        sub.total_paid = (sub.total_paid or Decimal("0")) + amount
        sub.last_charged_at = now

        await BalanceService.deduct_balance(
            db,
            user.id,
            amount,
            TransactionType.WA_SUBSCRIPTION,
            description=f"اشتراك قسم واتساب ({days} يوم × ${price_per_day})",
            related_table="whatsapp_subscriptions",
        )
        await db.commit()
        await db.refresh(sub)
        return sub, amount


# ══════════════ الروابط (جلسات واتساب) ══════════════


class WALinkService:
    @staticmethod
    async def get_link(db: AsyncSession, user_id: int) -> WhatsAppLink | None:
        result = await db.execute(
            select(WhatsAppLink)
            .where(WhatsAppLink.user_id == user_id)
            .order_by(desc(WhatsAppLink.id))
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def start_pairing(db: AsyncSession, user_id: int, raw_phone: str) -> WhatsAppLink:
        """يطلب كود اقتران من الجسر وينشئ/يحدّث سجل الربط."""
        phone = normalize_phone(raw_phone)
        # جلسة قديمة؟ نحاول فصلها عند الجسر بأدب ثم نستبدلها.
        old = await WALinkService.get_link(db, user_id)
        if old is not None and old.bridge_session_id and old.status == WALinkStatus.LINKED:
            try:
                await WABridgeClient.logout(old.bridge_session_id)
            except WABridgeError:
                pass

        data = await WABridgeClient.start_session(phone)
        session_id = str(data.get("session_id") or "").strip()
        if not session_id:
            raise WABridgeError("الجسر لم يعطِ معرّف جلسة. تأكد من إعداد سيرفر الجسر.")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        link = old if old is not None else WhatsAppLink(user_id=user_id, phone=phone)
        link.phone = phone
        link.bridge_session_id = session_id
        link.pairing_code = str(data.get("pairing_code") or "").strip() or None
        link.status = WALinkStatus.PENDING
        link.last_menu_json = None
        link.linked_at = None
        link.last_check_at = now
        if link.id is None:
            db.add(link)
        await db.commit()
        await db.refresh(link)
        return link

    @staticmethod
    async def refresh_status(db: AsyncSession, link: WhatsAppLink) -> WALinkStatus:
        """يسأل الجسر عن حالة الجلسة ويحدّث السجل. يرجع الحالة الجديدة."""
        if not link.bridge_session_id:
            return link.status
        data = await WABridgeClient.session_status(link.bridge_session_id)
        raw = str(data.get("status") or "").strip().lower()
        mapping = {
            "pending": WALinkStatus.PENDING,
            "linked": WALinkStatus.LINKED,
            "connected": WALinkStatus.LINKED,
            "expired": WALinkStatus.EXPIRED,
            "disconnected": WALinkStatus.DISCONNECTED,
            "logged_out": WALinkStatus.DISCONNECTED,
        }
        link.status = mapping.get(raw, link.status)
        link.last_check_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if link.status == WALinkStatus.LINKED and link.linked_at is None:
            link.linked_at = link.last_check_at
        await db.commit()
        return link.status

    @staticmethod
    async def run_command(
        db: AsyncSession, link: WhatsAppLink, action: str, text: str | None = None
    ) -> dict:
        """
        يمرّر ضغطة زر/أمر إلى الجسر ويحفظ القائمة المرجعة.

        الجواب المتوقع من الجسر:
          {"text": "نص القائمة/النتيجة", "buttons": [{"text": "…", "action": "…"}]}
        """
        data = await WABridgeClient.send_command(link.bridge_session_id, action, text)
        buttons = data.get("buttons")
        if isinstance(buttons, list) and buttons:
            link.last_menu_json = json.dumps(
                {"buttons": buttons}, ensure_ascii=False
            )
        else:
            link.last_menu_json = None
        await db.commit()
        return data

    @staticmethod
    def parse_buttons(link: WhatsAppLink) -> list[dict]:
        if not link.last_menu_json:
            return []
        try:
            data = json.loads(link.last_menu_json)
            buttons = data.get("buttons") or []
            return [b for b in buttons if isinstance(b, dict) and b.get("text") and b.get("action")]
        except (ValueError, TypeError):
            return []

    @staticmethod
    async def unlink(db: AsyncSession, link: WhatsAppLink) -> None:
        if link.bridge_session_id:
            try:
                await WABridgeClient.logout(link.bridge_session_id)
            except WABridgeError:
                pass
        link.status = WALinkStatus.DISCONNECTED
        link.last_menu_json = None
        await db.commit()
