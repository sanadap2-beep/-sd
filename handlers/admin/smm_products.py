"""
🚀 منتجات قسم الرشق — تحكم كامل من لوحة الأدمن.

الشاشة تعطي الأدمن مساراً واحداً واضحاً:

    🚀 منتجات قسم الرشق
        → كل التطبيقات التي فيها منتجات (مع عدد منتجات كل تطبيق)
            → أقسام التطبيق الفرعية (متابعون، لايكات، مشاهدات…)
                → منتجات القسم:
                    • ضغطة على المنتج = تعطيله/تفعيله
                    • 🗑 = حذفه (بتأكيد)
                    • 💵 = نسبة ربح واحدة تُطبَّق على كل منتجات القسم
                    • 🟢/⚪ = تفعيل/تعطيل كل منتجات القسم دفعة واحدة
                    • 🧹 = حذف كل منتجات القسم (تبقى الأقسام)

كل الأزرار تحمل البادئة ``smmp:``.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import AuditAction, Product, ProductStatus
from filters.admin_filter import IsAdmin
from keyboards.admin import admin_back_kb
from keyboards.admin_smm_products import (
    confirm_delete_product_kb,
    confirm_delete_unpriced_kb,
    confirm_purge_section_kb,
    margin_cancel_kb,
    smm_apps_kb,
    smm_categories_kb,
    smm_products_kb,
    smm_sections_kb,
)
from services.audit_service import AuditService
from services.margin_service import MarginService
from services.smm_admin_service import SmmAdminService
from states.states import AdminSmmProductsStates

logger = logging.getLogger(__name__)

router = Router(name="admin_smm_products")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════ أدوات مساعدة ══════════════


def _fmt(value) -> str:
    """رقم بصيغة نظيفة (0.5 بدل 0.500000)."""
    try:
        number = Decimal(str(value or 0))
    except Exception:
        return str(value)
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _parse_percent(raw: str) -> Decimal | None | bool:
    """يعيد النسبة، أو None (إلغاء الهامش الخاص)، أو False لقيمة غير صالحة."""
    raw = (raw or "").strip()
    if raw in ("", "0", "مسح", "-", "إلغاء", "inherit"):
        return None
    raw = raw.replace("٪", "").replace("%", "").replace("،", ".").strip()
    # أرقام عربية → إنجليزية
    raw = raw.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return False
    if value < MarginService.MIN_MARGIN or value > MarginService.MAX_MARGIN:
        return False
    return value


async def _safe_edit(message, text: str, reply_markup=None):
    """تعديل الرسالة، ومع أي فشل (رسالة قديمة/غير معدّلة) نرسل رسالة جديدة."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:  # pragma: no cover - يعتمد على تيليجرام
        if "message is not modified" in str(exc):
            return
        try:
            await message.answer(text, reply_markup=reply_markup)
        except Exception:
            logger.exception("تعذّر عرض شاشة منتجات الرشق")


# ══════════════ بناء الشاشات (نص + أزرار) ══════════════


async def _apps_screen(session, category, only_with_products: bool) -> tuple[str, object]:
    rows = await SmmAdminService.apps_overview(
        session, category.id, only_with_products=only_with_products
    )
    all_rows = (
        rows
        if not only_with_products
        else await SmmAdminService.apps_overview(
            session, category.id, only_with_products=False
        )
    )
    has_hidden = len(all_rows) > len(rows)

    total = sum(row.total for row in all_rows)
    active = sum(row.active for row in all_rows)
    with_products = len([row for row in all_rows if row.total > 0])

    text = (
        "🚀 <b>منتجات قسم الرشق</b>\n\n"
        f"📂 القسم: <b>{escape(category.emoji or '')} {escape(category.name_ar)}</b>\n"
        f"📱 تطبيقات فيها منتجات: <b>{with_products}</b> من {len(all_rows)}\n"
        f"📦 إجمالي المنتجات: <b>{total}</b> "
        f"(🟢 {active} مفعّل | ⚪ {max(0, total - active)} معطّل)\n\n"
    )
    if not rows:
        text += (
            "⚠️ لا توجد منتجات في هذا القسم بعد.\n"
            "اسحب خدمات من «📥 الخدمات المسحوبة» ثم عُد إلى هنا."
        )
    else:
        text += "اضغط على أي تطبيق لعرض أقسامه الفرعية ومنتجاته."

    return text, smm_apps_kb(
        category.id,
        rows,
        only_with_products=only_with_products,
        has_hidden=has_hidden,
    )


async def _sections_screen(session, app_id: int) -> tuple[str, object] | None:
    stats = await SmmAdminService.node_stats(session, app_id)
    if stats is None:
        return None
    app = stats.sub
    sections = await SmmAdminService.sections_overview(session, app_id)
    margin_text = await SmmAdminService.effective_margin_text(session, app)

    unpriced = await SmmAdminService.unpriced_count(session, app_id)
    text = (
        f"📱 <b>{escape(stats.label)}</b> — أقسام التطبيق\n\n"
        f"🗂 عدد الأقسام الفرعية: <b>{len(sections)}</b>\n"
        f"📦 منتجات التطبيق كلها: <b>{stats.total}</b> "
        f"(🟢 {stats.active} | ⚪ {stats.inactive})\n"
        f"💵 نسبة الربح الحالية: <b>{escape(margin_text)}</b>\n"
    )
    if unpriced:
        text += f"⚠️ منتجات بلا سعر: <b>{unpriced}</b> (خدمات «سيرفر» غير مسعّرة)\n"
    text += "\n"
    if not sections and not stats.direct_total:
        text += "⚠️ لا توجد أقسام فرعية ولا منتجات داخل هذا التطبيق."
    else:
        text += "اختر القسم الفرعي للتحكم بمنتجاته (تعطيل/حذف/نسبة ربح)."

    return text, smm_sections_kb(
        app_id,
        app.category_id,
        sections,
        direct_products=stats.direct_total,
        unpriced=unpriced,
    )


async def _products_screen(session, sub_id: int, page: int) -> tuple[str, object] | None:
    stats = await SmmAdminService.node_stats(session, sub_id)
    if stats is None:
        return None
    sub = stats.sub
    products, total, pages = await SmmAdminService.products_page(session, sub_id, page)
    page = max(0, min(page, pages - 1))
    margin_text = await SmmAdminService.effective_margin_text(session, sub)

    parent_label = ""
    if sub.parent_sub_category_id:
        parent = await SmmAdminService.get_sub(session, sub.parent_sub_category_id)
        if parent is not None:
            parent_label = f"📱 التطبيق: <b>{escape(parent.emoji or '')} {escape(parent.name_ar)}</b>\n"

    text = (
        f"🗂 <b>{escape(stats.label)}</b> — منتجات القسم\n\n"
        f"{parent_label}"
        f"📦 المنتجات: <b>{total}</b> "
        f"(🟢 {stats.direct_active} | ⚪ {max(0, total - stats.direct_active)})\n"
        f"💵 نسبة الربح: <b>{escape(margin_text)}</b>\n"
    )
    unpriced = await SmmAdminService.unpriced_count(
        session, sub_id, include_children=False
    )
    if unpriced:
        text += f"⚠️ منتجات بلا سعر: <b>{unpriced}</b> (خدمات «سيرفر» غير مسعّرة)\n"
    if pages > 1:
        text += f"📄 الصفحة <b>{page + 1}</b> من <b>{pages}</b>\n"
    text += "\n"

    if not products:
        text += "⚠️ لا توجد منتجات في هذا القسم."
    else:
        lines = []
        for product in products:
            mark = "🟢" if product.status == ProductStatus.ACTIVE else "⚪"
            info = SmmAdminService.provider_info(product)
            head = f"{mark} <b>{escape(product.name_ar[:44])}</b>"
            if info["unpriced"]:
                head += " ⚠️"
            money = (
                f"   🏪 عندنا: <b>{_fmt(info['sell'])}$</b> · "
                f"🏭 المزود: <b>{_fmt(info['cost'])}$</b>"
            )
            if info["margin"] is not None:
                money += f" · ربح {_fmt(info['profit'])}$ ({_fmt(info['margin'])}%)"
            provider_line = "   🔌 " + (
                escape(str(info["provider_name"]))
                if info["provider_name"]
                else "بدون مزود (يدوي)"
            )
            if info["service_id"]:
                provider_line += f" · خدمة #{escape(str(info['service_id']))}"
            if product.requires_quantity:
                provider_line += f" · كمية {product.min_quantity}–{product.max_quantity}"
            block = f"{head}\n{money}\n{provider_line}"
            if info["stale_cost"]:
                block += (
                    f"\n   ⚠️ سعر المزود الآن {_fmt(info['live_cost'])}$ "
                    "(أعد ضبط نسبة الربح للتحديث)"
                )
            elif info["unpriced"]:
                block += "\n   ⚠️ خدمة بلا سعر عند المزود — يُنصح بحذفها."
            lines.append(block)
        text += "\n\n".join(lines)
        text += (
            "\n\n👆 اضغط اسم المنتج لتعطيله/تفعيله، و🗑 لحذفه نهائياً.\n"
            "💵 «نسبة الربح» تُطبَّق فوراً على كل منتجات هذا القسم."
        )

    return text, smm_products_kb(
        sub_id,
        products,
        page=page,
        pages=pages,
        parent_id=sub.parent_sub_category_id,
        category_id=sub.category_id,
        unpriced=unpriced,
    )


# ══════════════ المدخل: التطبيقات ══════════════


@router.callback_query(F.data == "admin:smm_products")
@router.callback_query(F.data == "smmp:home")
async def smm_products_home(callback: CallbackQuery, session):
    categories = await SmmAdminService.smm_categories(session)
    if not categories:
        await callback.answer()
        await _safe_edit(
            callback.message,
            "⚠️ <b>لا يوجد قسم رشق</b>\n\n"
            "أنشئ قسماً رئيسياً من نوع «رشق سوشيال» من «📂 إدارة الأقسام» أولاً.",
            admin_back_kb(),
        )
        return

    if len(categories) == 1:
        text, kb = await _apps_screen(session, categories[0], True)
        await callback.answer()
        await _safe_edit(callback.message, text, kb)
        return

    await callback.answer()
    await _safe_edit(
        callback.message,
        "🚀 <b>منتجات قسم الرشق</b>\n\nاختر قسم الرشق الذي تريد إدارته:",
        smm_categories_kb(categories),
    )


@router.callback_query(F.data.startswith("smmp:cat:"))
async def smm_apps(callback: CallbackQuery, session):
    parts = callback.data.split(":")
    category_id = int(parts[2])
    only_with_products = parts[3] != "0" if len(parts) > 3 else True

    categories = {c.id: c for c in await SmmAdminService.smm_categories(session)}
    category = categories.get(category_id)
    if category is None:
        await callback.answer("⚠️ القسم غير موجود.", show_alert=True)
        return

    text, kb = await _apps_screen(session, category, only_with_products)
    await callback.answer()
    await _safe_edit(callback.message, text, kb)


# ══════════════ أقسام التطبيق ══════════════


@router.callback_query(F.data.startswith("smmp:app:"))
async def smm_app_sections(callback: CallbackQuery, session):
    app_id = int(callback.data.split(":")[2])
    screen = await _sections_screen(session, app_id)
    if screen is None:
        await callback.answer("⚠️ التطبيق غير موجود.", show_alert=True)
        return
    await callback.answer()
    await _safe_edit(callback.message, screen[0], screen[1])


# ══════════════ منتجات القسم ══════════════


@router.callback_query(F.data.startswith("smmp:sec:"))
async def smm_section_products(callback: CallbackQuery, session):
    parts = callback.data.split(":")
    sub_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0
    screen = await _products_screen(session, sub_id, page)
    if screen is None:
        await callback.answer("⚠️ القسم غير موجود.", show_alert=True)
        return
    await callback.answer()
    await _safe_edit(callback.message, screen[0], screen[1])


# ══════════════ تعطيل/تفعيل منتج واحد ══════════════


@router.callback_query(F.data.startswith("smmp:tg:"))
async def smm_toggle_product(callback: CallbackQuery, session, db_user=None):
    parts = callback.data.split(":")
    product_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    product = await SmmAdminService.toggle_product(session, product_id)
    if product is None:
        await callback.answer("⚠️ المنتج غير موجود.", show_alert=True)
        return

    is_active = product.status == ProductStatus.ACTIVE
    if db_user is not None:
        await AuditService.log_toggle(
            admin_id=db_user.id,
            entity_type="product",
            entity_id=product.id,
            entity_name=product.name_ar,
            new_status=is_active,
            session=session,
        )

    await callback.answer("🟢 تم التفعيل." if is_active else "⚪ تم التعطيل.")
    screen = await _products_screen(session, product.sub_category_id, page)
    if screen:
        await _safe_edit(callback.message, screen[0], screen[1])


# ══════════════ حذف منتج واحد ══════════════


@router.callback_query(F.data.startswith("smmp:del:"))
async def smm_delete_product_ask(callback: CallbackQuery, session):
    parts = callback.data.split(":")
    product_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    product = await session.get(Product, product_id)
    if product is None:
        await callback.answer("⚠️ المنتج غير موجود.", show_alert=True)
        return

    await callback.answer()
    await _safe_edit(
        callback.message,
        f"🗑 <b>حذف منتج</b>\n\n"
        f"📦 {escape(product.name_ar)}\n"
        f"💵 السعر: {_fmt(product.price_usd)}$\n\n"
        "⚠️ الحذف نهائي ولا يمكن التراجع عنه.\n"
        "لو أردت إخفاءه مؤقتاً فقط استخدم «تعطيل» بدل الحذف.",
        confirm_delete_product_kb(product_id, product.sub_category_id, page),
    )


@router.callback_query(F.data.startswith("smmp:delete_go:"))
async def smm_delete_product_go(callback: CallbackQuery, session, db_user=None):
    parts = callback.data.split(":")
    product_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 0

    product = await session.get(Product, product_id)
    if product is None:
        await callback.answer("⚠️ المنتج غير موجود.", show_alert=True)
        return

    sub_id = product.sub_category_id
    name = product.name_ar
    deleted = await SmmAdminService.delete_product(session, product_id)
    if not deleted:
        await callback.answer("⚠️ تعذّر الحذف (مرتبط بطلبات؟).", show_alert=True)
        screen = await _products_screen(session, sub_id, page)
        if screen:
            await _safe_edit(callback.message, screen[0], screen[1])
        return

    if db_user is not None:
        await AuditService.log_delete(
            admin_id=db_user.id,
            entity_type="product",
            entity_id=product_id,
            entity_name=name,
            session=session,
        )

    await callback.answer("🗑 تم حذف المنتج.")
    screen = await _products_screen(session, sub_id, page)
    if screen:
        await _safe_edit(callback.message, screen[0], screen[1])


# ══════════════ تفعيل/تعطيل كل منتجات القسم ══════════════


async def _bulk_status(callback: CallbackQuery, session, db_user, *, active: bool):
    sub_id = int(callback.data.split(":")[2])
    stats = await SmmAdminService.node_stats(session, sub_id)
    if stats is None:
        await callback.answer("⚠️ القسم غير موجود.", show_alert=True)
        return

    changed = await SmmAdminService.bulk_set_status(session, sub_id, active=active)
    if db_user is not None:
        await AuditService.log(
            admin_id=db_user.id,
            action=AuditAction.ACTIVATE if active else AuditAction.DEACTIVATE,
            entity_type="subcategory",
            entity_id=sub_id,
            entity_name=stats.sub.name_ar,
            new_value={"products_changed": changed, "active": active},
            description=(
                f"{'تفعيل' if active else 'تعطيل'} كل منتجات قسم الرشق "
                f"{stats.sub.name_ar} ({changed} منتج)"
            ),
            session=session,
        )

    await callback.answer(
        f"{'🟢 فُعِّل' if active else '⚪ عُطِّل'} {changed} منتجاً.",
        show_alert=changed == 0,
    )

    is_app = stats.sub.parent_sub_category_id is None
    screen = (
        await _sections_screen(session, sub_id)
        if is_app
        else await _products_screen(session, sub_id, 0)
    )
    if screen:
        await _safe_edit(callback.message, screen[0], screen[1])


@router.callback_query(F.data.startswith("smmp:on:"))
async def smm_bulk_activate(callback: CallbackQuery, session, db_user=None):
    await _bulk_status(callback, session, db_user, active=True)


@router.callback_query(F.data.startswith("smmp:off:"))
async def smm_bulk_deactivate(callback: CallbackQuery, session, db_user=None):
    await _bulk_status(callback, session, db_user, active=False)


# ══════════════ حذف كل منتجات القسم ══════════════


@router.callback_query(F.data.startswith("smmp:sec_delete:"))
async def smm_purge_ask(callback: CallbackQuery, session):
    sub_id = int(callback.data.split(":")[2])
    stats = await SmmAdminService.node_stats(session, sub_id)
    if stats is None:
        await callback.answer("⚠️ القسم غير موجود.", show_alert=True)
        return
    await callback.answer()
    await _safe_edit(
        callback.message,
        f"🧹 <b>حذف كل منتجات «{escape(stats.label)}»</b>\n\n"
        f"سيُحذف <b>{stats.total}</b> منتجاً.\n"
        "⚠️ القسم نفسه يبقى موجوداً — يمكنك إعادة السحب لاحقاً.\n"
        "الحذف نهائي.",
        confirm_purge_section_kb(sub_id),
    )


@router.callback_query(F.data.startswith("smmp:sec_delete_go:"))
async def smm_purge_go(callback: CallbackQuery, session, db_user=None):
    sub_id = int(callback.data.split(":")[2])
    stats = await SmmAdminService.node_stats(session, sub_id)
    if stats is None:
        await callback.answer("⚠️ القسم غير موجود.", show_alert=True)
        return

    deleted, errors = await SmmAdminService.delete_section_products(session, sub_id)
    if db_user is not None:
        await AuditService.log(
            admin_id=db_user.id,
            action=AuditAction.DELETE,
            entity_type="subcategory",
            entity_id=sub_id,
            entity_name=stats.sub.name_ar,
            new_value={"deleted": deleted, "errors": errors},
            description=(
                f"حذف كل منتجات قسم الرشق {stats.sub.name_ar} "
                f"({deleted} منتج، {errors} خطأ)"
            ),
            session=session,
        )

    await callback.answer(f"🧹 حُذف {deleted} منتجاً.")
    screen = await _products_screen(session, sub_id, 0)
    if screen:
        await _safe_edit(callback.message, screen[0], screen[1])


# ══════════════ حذف المنتجات بلا سعر ══════════════


@router.callback_query(F.data.startswith("smmp:zero_delete:"))
async def smm_delete_unpriced_ask(callback: CallbackQuery, session):
    sub_id = int(callback.data.split(":")[2])
    stats = await SmmAdminService.node_stats(session, sub_id)
    if stats is None:
        await callback.answer("⚠️ القسم غير موجود.", show_alert=True)
        return
    is_app = stats.sub.parent_sub_category_id is None
    count = await SmmAdminService.unpriced_count(session, sub_id)
    if not count:
        await callback.answer("✅ لا توجد منتجات بلا سعر هنا.", show_alert=True)
        return
    await callback.answer()
    await _safe_edit(
        callback.message,
        f"🧼 <b>تنظيف المنتجات بلا سعر — {escape(stats.label)}</b>\n\n"
        f"سيُحذف <b>{count}</b> منتجاً سعرها <b>0$</b>.\n"
        "هذه غالباً أسطر «سيرفر 1 / Server 2» أو عناوين أقسام سحبناها من "
        "المزود بلا تسعير، وبيعها بسعر صفر خسارة.\n\n"
        "⚠️ الحذف نهائي — والسحب التلقائي الجديد لن يعيدها.",
        confirm_delete_unpriced_kb(sub_id, is_app=is_app),
    )


@router.callback_query(F.data.startswith("smmp:zero_delete_go:"))
async def smm_delete_unpriced_go(callback: CallbackQuery, session, db_user=None):
    sub_id = int(callback.data.split(":")[2])
    stats = await SmmAdminService.node_stats(session, sub_id)
    if stats is None:
        await callback.answer("⚠️ القسم غير موجود.", show_alert=True)
        return

    deleted = await SmmAdminService.delete_unpriced(session, sub_id)
    if db_user is not None:
        await AuditService.log(
            admin_id=db_user.id,
            action=AuditAction.DELETE,
            entity_type="subcategory",
            entity_id=sub_id,
            entity_name=stats.sub.name_ar,
            new_value={"deleted_unpriced": deleted},
            description=(
                f"حذف المنتجات بلا سعر من {stats.sub.name_ar} ({deleted} منتج)"
            ),
            session=session,
        )

    await callback.answer(f"🧼 حُذف {deleted} منتجاً بلا سعر.")
    is_app = stats.sub.parent_sub_category_id is None
    screen = (
        await _sections_screen(session, sub_id)
        if is_app
        else await _products_screen(session, sub_id, 0)
    )
    if screen:
        await _safe_edit(callback.message, screen[0], screen[1])


# ══════════════ نسبة الربح لكل منتجات القسم ══════════════


@router.callback_query(F.data.startswith("smmp:margin:"))
async def smm_margin_start(callback: CallbackQuery, state: FSMContext, session):
    sub_id = int(callback.data.split(":")[2])
    stats = await SmmAdminService.node_stats(session, sub_id)
    if stats is None:
        await callback.answer("⚠️ القسم غير موجود.", show_alert=True)
        return

    is_app = stats.sub.parent_sub_category_id is None
    margin_text = await SmmAdminService.effective_margin_text(session, stats.sub)
    scope = "كل منتجات التطبيق وأقسامه" if is_app else "كل منتجات هذا القسم"

    await state.update_data(smm_margin_sub_id=sub_id, smm_margin_is_app=is_app)
    await state.set_state(AdminSmmProductsStates.waiting_margin)

    await callback.answer()
    await _safe_edit(
        callback.message,
        f"💵 <b>نسبة الربح — {escape(stats.label)}</b>\n\n"
        f"الحالية: <b>{escape(margin_text)}</b>\n"
        f"ستُطبَّق على: <b>{scope}</b> ({stats.total} منتج)\n\n"
        "أرسل النسبة المئوية الآن (مثال: <code>50</code> أي التكلفة + 50%).\n"
        "أرسل <b>0</b> لإلغاء النسبة الخاصة والعودة لوراثة القسم الأعلى.\n\n"
        "ℹ️ يُعاد حساب سعر كل منتج فوراً = التكلفة × (1 + النسبة ÷ 100).",
        margin_cancel_kb(sub_id, is_app=is_app),
    )


@router.message(AdminSmmProductsStates.waiting_margin)
async def smm_margin_received(message: Message, state: FSMContext, session, db_user=None):
    data = await state.get_data()
    sub_id = data.get("smm_margin_sub_id")
    is_app = bool(data.get("smm_margin_is_app"))

    sub = await SmmAdminService.get_sub(session, int(sub_id)) if sub_id else None
    if sub is None:
        await state.clear()
        await message.answer("⚠️ القسم غير موجود.", reply_markup=admin_back_kb())
        return

    percent = _parse_percent(message.text or "")
    if percent is False:
        await message.answer(
            "⚠️ أرسل نسبة صحيحة بين "
            f"{_fmt(MarginService.MIN_MARGIN)} و{_fmt(MarginService.MAX_MARGIN)} "
            "(مثال: 50)، أو 0 للإلغاء."
        )
        return

    report = await SmmAdminService.apply_margin(session, sub, percent)
    await state.clear()

    if db_user is not None:
        await AuditService.log(
            admin_id=db_user.id,
            action=AuditAction.PRICE_CHANGE,
            entity_type="subcategory",
            entity_id=sub.id,
            entity_name=sub.name_ar,
            new_value={
                "margin_percent": str(report["percent"]) if report["percent"] is not None else None,
                "products": report["products"],
                "repriced": report["repriced"],
            },
            description=(
                f"ضبط نسبة ربح قسم الرشق {sub.name_ar} = "
                f"{report['percent'] if report['percent'] is not None else 'وراثة'} "
                f"({report['repriced']} سعر محدَّث من {report['products']} منتج)"
            ),
            session=session,
        )

    percent_text = (
        f"{_fmt(report['percent'])}%" if report["percent"] is not None else "وراثة القسم الأعلى"
    )
    summary = (
        "✅ <b>تم ضبط نسبة الربح</b>\n\n"
        f"🗂 القسم: <b>{escape(sub.emoji or '')} {escape(sub.name_ar)}</b>\n"
        f"💵 النسبة: <b>{percent_text}</b>\n"
        f"📦 المنتجات المشمولة: <b>{report['products']}</b>\n"
        f"🔁 أسعار أُعيد حسابها: <b>{report['repriced']}</b>"
    )
    if report["no_cost"]:
        summary += (
            f"\n⚠️ <b>{report['no_cost']}</b> منتج بلا سعر تكلفة — "
            "لم يتغيّر سعره (عدّله يدوياً)."
        )
    await message.answer(summary)

    screen = (
        await _sections_screen(session, sub.id)
        if is_app
        else await _products_screen(session, sub.id, 0)
    )
    if screen:
        await message.answer(screen[0], reply_markup=screen[1])
