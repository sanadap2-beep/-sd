"""عرض سجل تعديلات الإدارة للأدمن."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from filters.admin_filter import IsAdmin
from keyboards.admin import admin_audit_kb
from services.audit_service import AuditService

router = Router(name="admin_audit")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_PAGE_SIZE = 6


async def _render_audit(callback: CallbackQuery, page: int = 0):
    logs = await AuditService.get_recent_logs(
        limit=_PAGE_SIZE + 1,
        offset=max(0, page) * _PAGE_SIZE,
    )
    has_next = len(logs) > _PAGE_SIZE
    logs = logs[:_PAGE_SIZE]

    if logs:
        body = "\n\n".join(AuditService.format_log_entry(log) for log in logs)
    else:
        body = "لا توجد تعديلات مسجلة بعد."

    await callback.message.edit_text(
        f"📜 <b>سجل الإدارة</b>\n\n{body}",
        reply_markup=admin_audit_kb(page, has_next),
    )


@router.callback_query(F.data == "admin:audit")
async def audit_list(callback: CallbackQuery):
    await callback.answer()
    await _render_audit(callback, 0)


@router.callback_query(F.data.startswith("admin:audit:"))
async def audit_page(callback: CallbackQuery):
    try:
        page = max(0, int(callback.data.split(":")[2]))
    except (ValueError, IndexError):
        page = 0
    await callback.answer()
    await _render_audit(callback, page)
