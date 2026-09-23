"""Notification center: inbox, preferences, templates, dedupe and delivery log."""

from __future__ import annotations

from datetime import datetime, timedelta
from string import Formatter

from sqlalchemy import desc, func, select

from database.engine import async_session_maker
from database.models import Notification, NotificationPreference, NotificationTemplate, User

CRITICAL_CATEGORIES = {"payment", "withdrawal", "security", "order", "market_dispute"}
CATEGORIES = ["order", "payment", "withdrawal", "market", "support", "promotion", "provider", "system", "security"]
PRIORITIES = ["low", "normal", "high", "critical"]

DEFAULT_TEMPLATES = {
    "deposit_approved": ("تم شحن رصيدك", "✅ تم شحن رصيدك بنجاح.\nالمبلغ: {amount}$", "payment", "high"),
    "withdraw_paid": ("تم دفع السحب", "✅ تم دفع طلب السحب #{request_id}.\nالمبلغ: {amount} {currency}", "withdrawal", "high"),
    "market_sold": ("تم بيع إعلانك", "🎉 تم بيع إعلانك: {title}\nالمبلغ: {amount}$", "market", "high"),
    "market_dispute": ("نزاع في السوق", "⚠️ يوجد نزاع على العملية #{transaction_id}. راجع لوحة السوق.", "market_dispute", "critical"),
    "order_completed": ("اكتمل الطلب", "✅ اكتمل طلبك: {product}\n{details}", "order", "high"),
    "provider_offline": ("مزود متوقف", "🔴 المزود {provider} متوقف: {error}", "provider", "critical"),
}


class NotificationCenterService:
    @staticmethod
    def _safe_title(body: str, fallback: str = "إشعار") -> str:
        first = (body or fallback).strip().splitlines()[0] if body else fallback
        return first[:120] or fallback

    @staticmethod
    async def should_send(session, user_id: int | None, category: str, priority: str = "normal") -> bool:
        if user_id is None or category in CRITICAL_CATEGORIES or priority == "critical":
            return True
        pref = (
            await session.execute(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user_id,
                    NotificationPreference.category == category,
                )
            )
        ).scalar_one_or_none()
        return True if pref is None else bool(pref.enabled)

    @staticmethod
    async def was_sent(dedupe_key: str | None) -> bool:
        """هل أُرسل إشعار بهذا المفتاح من قبل؟ يمنع تكرار منشور القناة بعد إعادة التشغيل."""
        if not dedupe_key:
            return False
        try:
            async with async_session_maker() as session:
                count = (
                    await session.execute(
                        select(func.count(Notification.id)).where(
                            Notification.dedupe_key == dedupe_key,
                            Notification.status == "sent",
                        )
                    )
                ).scalar_one()
                return int(count or 0) > 0
        except Exception:
            return False

    @staticmethod
    async def recently_sent(session, dedupe_key: str | None, minutes: int = 30) -> bool:
        if not dedupe_key:
            return False
        since = datetime.utcnow() - timedelta(minutes=max(1, minutes))
        count = (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.dedupe_key == dedupe_key,
                    Notification.created_at >= since,
                    Notification.status.in_(["sent", "queued"]),
                )
            )
        ).scalar_one()
        return count > 0

    @staticmethod
    async def record(
        session,
        *,
        user_id: int | None = None,
        recipient_chat_id: int | None = None,
        channel: str = "user",
        category: str = "system",
        priority: str = "normal",
        title: str | None = None,
        body: str,
        status: str = "queued",
        dedupe_key: str | None = None,
        error: str | None = None,
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            recipient_chat_id=recipient_chat_id,
            channel=channel,
            category=category,
            priority=priority,
            title=(title or NotificationCenterService._safe_title(body))[:128],
            body=body,
            status=status,
            dedupe_key=dedupe_key,
            error=(error or None),
            sent_at=datetime.utcnow() if status == "sent" else None,
        )
        session.add(notification)
        await session.commit()
        await session.refresh(notification)
        return notification

    @staticmethod
    async def record_user_by_telegram(
        telegram_id: int,
        body: str,
        *,
        category: str = "system",
        priority: str = "normal",
        title: str | None = None,
        status: str = "sent",
        dedupe_key: str | None = None,
        error: str | None = None,
    ) -> None:
        try:
            async with async_session_maker() as session:
                user = (
                    await session.execute(select(User).where(User.telegram_id == int(telegram_id)))
                ).scalar_one_or_none()
                user_id = user.id if user else None
                if status != "sent" or await NotificationCenterService.should_send(session, user_id, category, priority):
                    await NotificationCenterService.record(
                        session,
                        user_id=user_id,
                        recipient_chat_id=int(telegram_id),
                        channel="user",
                        category=category,
                        priority=priority,
                        title=title,
                        body=body,
                        status=status,
                        dedupe_key=dedupe_key,
                        error=error,
                    )
        except Exception:
            # إشعار الإشعار اختياري ولا يجب أن يكسر المسار الأساسي.
            return

    @staticmethod
    async def record_admin(
        chat_id: int | None,
        body: str,
        *,
        category: str = "system",
        priority: str = "high",
        title: str | None = None,
        status: str = "sent",
        dedupe_key: str | None = None,
        error: str | None = None,
    ) -> None:
        try:
            async with async_session_maker() as session:
                if await NotificationCenterService.recently_sent(session, dedupe_key):
                    return
                await NotificationCenterService.record(
                    session,
                    recipient_chat_id=chat_id,
                    channel="admin",
                    category=category,
                    priority=priority,
                    title=title,
                    body=body,
                    status=status,
                    dedupe_key=dedupe_key,
                    error=error,
                )
        except Exception:
            return

    @staticmethod
    async def inbox(session, user_id: int, category: str = "all", limit: int = 20) -> list[Notification]:
        query = select(Notification).where(Notification.user_id == user_id, Notification.channel == "user")
        if category != "all":
            query = query.where(Notification.category == category)
        result = await session.execute(query.order_by(desc(Notification.created_at)).limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def mark_read(session, user_id: int, notification_id: int | None = None) -> int:
        notifications = await NotificationCenterService.inbox(session, user_id, limit=100)
        count = 0
        for notification in notifications:
            if notification_id is not None and notification.id != notification_id:
                continue
            if notification.read_at is None:
                notification.read_at = datetime.utcnow()
                count += 1
        if count:
            await session.commit()
        return count

    @staticmethod
    async def set_preference(session, user_id: int, category: str, enabled: bool) -> bool:
        if category in CRITICAL_CATEGORIES:
            return False
        pref = (
            await session.execute(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user_id,
                    NotificationPreference.category == category,
                )
            )
        ).scalar_one_or_none()
        if pref is None:
            pref = NotificationPreference(user_id=user_id, category=category, enabled=enabled)
            session.add(pref)
        else:
            pref.enabled = enabled
        await session.commit()
        return True

    @staticmethod
    async def preferences(session, user_id: int) -> dict[str, bool]:
        rows = (
            await session.execute(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
        ).scalars().all()
        values = {category: True for category in CATEGORIES}
        values.update({row.category: row.enabled for row in rows})
        for category in CRITICAL_CATEGORIES:
            values[category] = True
        return values

    @staticmethod
    def render_template_text(template: NotificationTemplate, **data) -> tuple[str, str]:
        safe = {key: str(value) for key, value in data.items()}
        title = template.title.format_map(_SafeDict(safe))
        body = template.body.format_map(_SafeDict(safe))
        return title, body

    @staticmethod
    async def seed_templates(session) -> None:
        for key, (title, body, category, priority) in DEFAULT_TEMPLATES.items():
            if await session.get(NotificationTemplate, key) is None:
                session.add(NotificationTemplate(key=key, title=title, body=body, category=category, priority=priority))
        await session.commit()

    @staticmethod
    async def templates(session) -> list[NotificationTemplate]:
        await NotificationCenterService.seed_templates(session)
        result = await session.execute(select(NotificationTemplate).order_by(NotificationTemplate.key))
        return list(result.scalars().all())

    @staticmethod
    async def update_template(session, key: str, title: str, body: str) -> bool:
        template = await session.get(NotificationTemplate, key)
        if template is None:
            return False
        template.title = title[:128]
        template.body = body[:4000]
        await session.commit()
        return True

    @staticmethod
    async def admin_stats(session, days: int = 7) -> dict:
        since = datetime.utcnow() - timedelta(days=max(1, days))
        rows = (
            await session.execute(
                select(Notification.status, func.count(Notification.id))
                .where(Notification.created_at >= since)
                .group_by(Notification.status)
            )
        ).all()
        by_status = {status: int(count) for status, count in rows}
        failed = (
            await session.execute(
                select(Notification)
                .where(Notification.status == "failed")
                .order_by(desc(Notification.created_at))
                .limit(10)
            )
        ).scalars().all()
        return {"by_status": by_status, "failed": list(failed)}


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"
