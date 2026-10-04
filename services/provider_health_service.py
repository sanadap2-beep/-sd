"""صحة المزودين: تنبيه مبكّر قبل أن يتوقف البيع.

الوضع السابق (``update_provider_status``): تنبيه واحد كل ٢٤ ساعة
**بعد** أن ينزل الرصيد تحت الحد — أي بعد أن يكون المزود قد بدأ يفشل.

هذه الخدمة تسبق ذلك بخطوة:

1. تحسب «مدة الصمود»: الرصيد ÷ متوسط استهلاك آخر ١٤ يوماً.
2. تُصنّف كل مزود إلى: ``ok`` / ``early`` (قريب من الحد) /
   ``low`` (تحت الحد) / ``critical`` (شبه فارغ).
3. تعرض ذلك داخل لوحة المزودين مباشرة، وتبعث تنبيهاً مبكراً للأدمن
   (بتهدئة ١٢ ساعة لكل مستوى خطورة) قبل نفاد الرصيد لا بعده.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    ApiProvider,
    NumberOrder,
    ProviderName,
    ProviderStatus,
)
from services.feature_service import FeatureService
from services.html_guard import esc
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

FEATURE_KEY = "provider_health_watch"

# كم يوماً نحسب متوسط الاستهلاك عليه
BURN_WINDOW_DAYS = 14
# نبدأ التنبيه المبكر عند هذه النسبة من الحد (٢ = ضعف الحد)
EARLY_RATIO = Decimal("2")

LEVEL_LABELS = {
    "ok": "🟢",
    "early": "🟡 يقترب من الحد",
    "low": "🟠 تحت الحد",
    "critical": "🔴 شبه فارغ",
    "unknown": "⚪️ غير معروف",
}


class ProviderHealthService:
    """لقطة صحة المزودين + تنبيه مبكر."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY, default=True)

    @staticmethod
    async def threshold(session=None) -> Decimal:
        try:
            value = await SettingsService.get_decimal(
                "provider_low_balance_threshold", Decimal("10")
            )
        except Exception:  # noqa: BLE001
            value = Decimal("10")
        return value if value and value > 0 else Decimal("10")

    @staticmethod
    async def daily_burn(session, provider: ProviderName) -> Decimal:
        """متوسط الإنفاق اليومي على مزود الأرقام في آخر ١٤ يوماً."""
        since = datetime.utcnow() - timedelta(days=BURN_WINDOW_DAYS)
        try:
            total = await session.scalar(
                select(func.coalesce(func.sum(NumberOrder.price_provider_usd), 0)).where(
                    NumberOrder.provider == provider,
                    NumberOrder.purchased_at >= since,
                )
            )
        except Exception:  # noqa: BLE001
            return Decimal("0")
        try:
            return Decimal(str(total or 0)) / Decimal(str(BURN_WINDOW_DAYS))
        except Exception:  # noqa: BLE001
            return Decimal("0")

    @staticmethod
    def level(balance: Decimal | None, threshold: Decimal) -> str:
        if balance is None:
            return "unknown"
        if threshold <= 0:
            threshold = Decimal("10")
        if balance <= 0:
            return "critical"
        if balance < threshold:
            return "low"
        if balance < threshold * EARLY_RATIO:
            return "early"
        return "ok"

    @classmethod
    async def number_providers(cls, session, refresh: bool = False) -> list[dict]:
        """حالة مزودي الأرقام مع مدة الصمود."""
        threshold = await cls.threshold(session)
        now = datetime.utcnow()
        rows: list[dict] = []
        for provider in ProviderName:
            status = await session.get(ProviderStatus, provider)
            if status is None:
                continue
            if refresh:
                try:
                    from providers.manager import provider_manager

                    balance = await provider_manager.get_balance(provider)
                    status.balance = balance
                    status.is_online = True
                    status.last_error = None
                except Exception:  # noqa: BLE001
                    status.is_online = False
                status.last_checked_at = now
            balance = status.balance
            burn = await cls.daily_burn(session, provider)
            days_left = (Decimal(str(balance)) / burn) if (balance and burn > 0) else None
            rows.append(
                {
                    "kind": "number",
                    "key": provider.value,
                    "name": provider.value,
                    "balance": balance,
                    "threshold": threshold,
                    "days_left": days_left,
                    "level": cls.level(balance, threshold),
                    "is_online": bool(status.is_online),
                }
            )
        if refresh:
            await session.commit()
        return rows

    @classmethod
    async def smm_providers(cls, session) -> list[dict]:
        """حالة مزودي الخدمات (SMM) من جدول ``api_providers``."""
        rows: list[dict] = []
        providers = (
            (
                await session.execute(
                    select(ApiProvider).where(ApiProvider.is_active.is_(True))
                )
            )
            .scalars()
            .all()
        )
        for provider in providers:
            if provider.balance is None:
                continue
            threshold = provider.low_balance_threshold or Decimal("10")
            rows.append(
                {
                    "kind": "smm",
                    "key": f"api:{provider.id}",
                    "name": provider.name,
                    "balance": provider.balance,
                    "threshold": threshold,
                    "days_left": None,
                    "level": cls.level(provider.balance, threshold),
                    "is_online": provider.last_error is None,
                }
            )
        return rows

    @classmethod
    async def snapshot(cls, session, refresh: bool = False) -> list[dict]:
        rows = await cls.number_providers(session, refresh=refresh)
        try:
            rows += await cls.smm_providers(session)
        except Exception:  # noqa: BLE001
            logger.debug("تعذّر قراءة مزودي الخدمات")
        return rows

    @classmethod
    def render_block(cls, rows: list[dict]) -> str:
        """سطر صحة لكل مزود: الرصيد + مدة الصمود + حالة مبكرة."""
        lines: list[str] = []
        for row in rows:
            name = esc(str(row["name"]))
            balance = row.get("balance")
            level = row.get("level", "unknown")
            if level == "ok":
                continue
            label = LEVEL_LABELS.get(level, "")
            balance_text = f"{Decimal(str(balance)):.2f}$" if balance is not None else "—"
            line = f"{label} <b>{name}</b> — 💰 {balance_text}"
            days_left = row.get("days_left")
            if days_left is not None:
                line += f"\n   ⏳ يكفي تقريباً: {days_left:.1f} يوم بمعدل الاستهلاك الحالي"
            if level == "early":
                line += "\n   💡 اقترب من حد التنبيه — اشحنه قبل أن يتوقف البيع."
            elif level == "low":
                line += "\n   ⚠️ تحت الحد: الطلبات قد تتحول إلى معالجة يدوية."
            elif level == "critical":
                line += "\n   ⛔ الرصيد شبه معدوم: البيع سيتوقف فوراً."
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def compose_alert(row: dict) -> str:
        name = esc(str(row["name"]))
        balance = row.get("balance")
        balance_text = f"{Decimal(str(balance)):.2f}$" if balance is not None else "—"
        threshold = Decimal(str(row.get("threshold") or 0))
        level = row.get("level", "unknown")
        titles = {
            "early": "🟡 تنبيه مبكر: رصيد يقترب من الحد",
            "low": "🟠 رصيد تحت الحد",
            "critical": "🔴 رصيد شبه معدوم",
        }
        lines = [
            f"<b>{titles.get(level, '⚠️ تنبيه رصيد مزود')}</b>",
            f"المزود: <b>{name}</b>",
            f"💰 الرصيد: <b>{balance_text}</b> · حد التنبيه: {threshold:.2f}$",
        ]
        days_left = row.get("days_left")
        if days_left is not None:
            lines.append(f"⏳ يكفي تقريباً: <b>{days_left:.1f} يوم</b> بمعدل الاستهلاك الحالي")
        if level == "early":
            lines.append("💡 اشحنه الآن لتفادي توقف البيع بدل إصلاحه لاحقاً.")
        elif level == "low":
            lines.append("⚠️ الطلبات الجديدة قد تتحول إلى «بانتظار المعالجة اليدوية».")
        else:
            lines.append("⛔ البيع سيتوقف فوراً حتى تُشحن الرصيد.")
        lines.append("⚙️ لوحة التحكم ← 🌐 المزودون")
        return "\n".join(lines)

    @classmethod
    async def cycle(cls, bot) -> int:
        """يحدّث الأرصدة ويرسل تنبيهاً مبكراً لكل مزود متأزم."""
        if not await cls.enabled():
            return 0
        from database.engine import async_session_maker
        from services.notification_service import NotificationService

        async with async_session_maker() as session:
            rows = await cls.snapshot(session, refresh=True)

        notifier = NotificationService(bot)
        sent = 0
        for row in rows:
            if row.get("level") in (None, "ok", "unknown"):
                continue
            try:
                ok = await notifier.notify_admin(
                    cls.compose_alert(row),
                    dedupe_key=f"provider_health:{row['key']}:{row['level']}",
                )
            except Exception:  # noqa: BLE001
                logger.debug("تعذّر إرسال تنبيه صحة مزود %s", row.get("key"))
                continue
            if ok:
                sent += 1
        return sent
