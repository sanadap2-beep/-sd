"""
خدمة سجل تعديلات الأدمن (Audit Log).
تُستخدم لتسجيل كل عملية مهمة يقوم بها أي أدمن.

الفائدة:
- تتبع من عدل ماذا ومتى
- إمكانية استرجاع الأخطاء
- شفافية الإدارة عند وجود أكثر من أدمن
"""

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, desc, and_
from sqlalchemy.orm import selectinload

from database.engine import async_session_maker
from database.models import AuditLog, AuditAction

logger = logging.getLogger(__name__)


class AuditService:
    """خدمة تسجيل وقراءة سجل تعديلات الأدمن."""

    @staticmethod
    def _serialize_value(value: Any) -> str | None:
        """يحول القيمة إلى نص قابل للحفظ."""
        if value is None:
            return None
        if isinstance(value, (str, int, bool)):
            return str(value)
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (list, dict)):
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value)

    @staticmethod
    async def log(
        admin_id: int,
        action: AuditAction,
        entity_type: str,
        entity_id: int | None = None,
        entity_name: str | None = None,
        old_value: Any = None,
        new_value: Any = None,
        description: str | None = None,
        session=None,
    ) -> None:
        """
        يسجل عملية جديدة في السجل.

        Args:
            admin_id: آيدي الأدمن (users.id)
            action: نوع العملية (create/update/delete/إلخ)
            entity_type: نوع الكائن (category/product/provider/إلخ)
            entity_id: آيدي الكائن (اختياري)
            entity_name: اسم الكائن (اختياري)
            old_value: القيمة القديمة (اختياري)
            new_value: القيمة الجديدة (اختياري)
            description: وصف تفصيلي (اختياري)
            session: جلسة قاعدة بيانات (اختيارية)
        """
        try:
            audit_entry = AuditLog(
                admin_id=admin_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_name=(entity_name[:255] if entity_name else None),
                old_value=(AuditService._serialize_value(old_value)),
                new_value=(AuditService._serialize_value(new_value)),
                description=(description[:500] if description else None),
            )

            if session:
                session.add(audit_entry)
                await session.commit()
            else:
                async with async_session_maker() as new_session:
                    new_session.add(audit_entry)
                    await new_session.commit()

            logger.info(
                f"AUDIT: admin={admin_id} action={action.value} entity={entity_type}#{entity_id}"
            )
        except Exception as e:
            logger.error(f"فشل تسجيل Audit Log: {e}")

    @staticmethod
    async def log_create(
        admin_id: int,
        entity_type: str,
        entity_id: int,
        entity_name: str,
        new_value: Any = None,
        session=None,
    ) -> None:
        """اختصار لتسجيل عملية إنشاء."""
        await AuditService.log(
            admin_id=admin_id,
            action=AuditAction.CREATE,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            new_value=new_value,
            description=f"إنشاء {entity_type}: {entity_name}",
            session=session,
        )

    @staticmethod
    async def log_update(
        admin_id: int,
        entity_type: str,
        entity_id: int,
        entity_name: str,
        field: str,
        old_value: Any,
        new_value: Any,
        session=None,
    ) -> None:
        """اختصار لتسجيل عملية تعديل."""
        await AuditService.log(
            admin_id=admin_id,
            action=AuditAction.UPDATE,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            old_value={"field": field, "value": old_value},
            new_value={"field": field, "value": new_value},
            description=(f"تعديل {field} في {entity_type}: {entity_name}"),
            session=session,
        )

    @staticmethod
    async def log_delete(
        admin_id: int,
        entity_type: str,
        entity_id: int,
        entity_name: str,
        session=None,
    ) -> None:
        """اختصار لتسجيل عملية حذف."""
        await AuditService.log(
            admin_id=admin_id,
            action=AuditAction.DELETE,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            description=f"حذف {entity_type}: {entity_name}",
            session=session,
        )

    @staticmethod
    async def log_toggle(
        admin_id: int,
        entity_type: str,
        entity_id: int,
        entity_name: str,
        new_status: bool,
        session=None,
    ) -> None:
        """اختصار لتسجيل تفعيل/تعطيل."""
        action = AuditAction.ACTIVATE if new_status else AuditAction.DEACTIVATE
        status_ar = "تفعيل" if new_status else "تعطيل"
        await AuditService.log(
            admin_id=admin_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            new_value=str(new_status),
            description=(f"{status_ar} {entity_type}: {entity_name}"),
            session=session,
        )

    @staticmethod
    async def log_price_change(
        admin_id: int,
        entity_type: str,
        entity_id: int,
        entity_name: str,
        old_price: Decimal,
        new_price: Decimal,
        session=None,
    ) -> None:
        """اختصار لتسجيل تغيير سعر."""
        change_percent = 0
        if old_price > 0:
            change_percent = (new_price - old_price) / old_price * 100

        await AuditService.log(
            admin_id=admin_id,
            action=AuditAction.PRICE_CHANGE,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            old_value=str(old_price),
            new_value=str(new_price),
            description=(
                f"تغيير سعر {entity_name} من {old_price}$ إلى {new_price}$ ({change_percent:+.1f}%)"
            ),
            session=session,
        )

    @staticmethod
    async def log_sync(
        admin_id: int,
        entity_type: str,
        entity_id: int,
        entity_name: str,
        description: str,
        session=None,
    ) -> None:
        """اختصار لتسجيل عملية مزامنة."""
        await AuditService.log(
            admin_id=admin_id,
            action=AuditAction.SYNC,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            description=description,
            session=session,
        )

    @staticmethod
    async def get_recent_logs(
        limit: int = 50,
        offset: int = 0,
        admin_id: int | None = None,
        entity_type: str | None = None,
        action: AuditAction | None = None,
    ) -> list[AuditLog]:
        """يجلب آخر السجلات مع دعم الفلترة."""
        async with async_session_maker() as session:
            query = select(AuditLog).options(selectinload(AuditLog.admin))

            filters = []
            if admin_id is not None:
                filters.append(AuditLog.admin_id == admin_id)
            if entity_type:
                filters.append(AuditLog.entity_type == entity_type)
            if action:
                filters.append(AuditLog.action == action)

            if filters:
                query = query.where(and_(*filters))

            query = query.order_by(desc(AuditLog.created_at)).limit(limit).offset(offset)

            result = await session.execute(query)
            return list(result.scalars().all())

    @staticmethod
    async def get_entity_history(
        entity_type: str,
        entity_id: int,
        limit: int = 20,
    ) -> list[AuditLog]:
        """يجلب كل تعديلات كائن معين."""
        async with async_session_maker() as session:
            result = await session.execute(
                select(AuditLog)
                .options(selectinload(AuditLog.admin))
                .where(
                    and_(
                        AuditLog.entity_type == entity_type,
                        AuditLog.entity_id == entity_id,
                    )
                )
                .order_by(desc(AuditLog.created_at))
                .limit(limit)
            )
            return list(result.scalars().all())

    @staticmethod
    def format_log_entry(log: AuditLog) -> str:
        """يحول سجل إلى نص قابل للعرض."""
        action_emoji = {
            AuditAction.CREATE: "➕",
            AuditAction.UPDATE: "✏️",
            AuditAction.DELETE: "🗑",
            AuditAction.ACTIVATE: "🟢",
            AuditAction.DEACTIVATE: "🔴",
            AuditAction.SYNC: "🔄",
            AuditAction.PRICE_CHANGE: "💰",
            AuditAction.OTHER: "📝",
        }.get(log.action, "📝")

        admin_name = "غير معروف"
        if log.admin:
            admin_name = (
                log.admin.full_name or f"@{log.admin.username}"
                if log.admin.username
                else str(log.admin.telegram_id)
            )

        time_str = log.created_at.strftime("%Y-%m-%d %H:%M")

        text = f"{action_emoji} <b>{log.action.value}</b>\n👤 {admin_name}\n📁 {log.entity_type}"
        if log.entity_name:
            text += f" - {log.entity_name}"
        text += f"\n📅 {time_str}"

        if log.description:
            text += f"\n💬 {log.description}"

        return text
