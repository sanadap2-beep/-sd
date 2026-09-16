"""
إدارة المسارات الاحتياطية للمنتجات (Backup Providers).

كل منتج مربوط بمزود أساسي عبر Product.api_provider_id. إذا تعطّل ذلك المزود
أو نفدت خدمته، ينتقل النظام تلقائياً إلى المزود الاحتياطي من
product_provider_routes. هذه الواجهة تسمح للأدمن بإدارة تلك المسارات.
"""

import logging
from decimal import InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from database.models import ApiProvider, ProductProviderRoute
from services.catalog_routing_service import CatalogRoutingService
from services.dynamic_service import DynamicService
from states.states import AdminProductRouteStates
from keyboards.admin import admin_back_kb
from filters.admin_filter import IsAdmin

logger = logging.getLogger(__name__)

router = Router(name="admin_product_routes")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════════════════════════════════════
# عرض المسارات الاحتياطية
# ══════════════════════════════════════════════


async def _render_routes(session, product, target, edit: bool = True):
    """عرض شاشة المسارات الاحتياطية لمنتج معين."""
    from keyboards.admin import admin_product_detail_kb

    routes = await CatalogRoutingService.routes_for(session, product)
    failover_enabled = await CatalogRoutingService.enabled()
    status_icon = "🟢" if failover_enabled else "⚪"

    provider_name = "—"
    if product.api_provider_id:
        primary = await session.get(ApiProvider, product.api_provider_id)
        if primary:
            provider_name = primary.name

    text = (
        f"🔁 <b>المزودون الاحتياطيون</b>\n"
        f"📦 المنتج: <b>{product.name_ar}</b>\n"
        f"🔌 المزود الأساسي: <b>{provider_name}</b>\n"
        f"{status_icon} الميزة: {'مفعّلة' if failover_enabled else 'معطّلة (فعّلها من مركز الإضافات)'}\n"
        f"📊 عدد المسارات الإجمالي: <b>{len(routes)}</b>\n"
    )

    b = InlineKeyboardBuilder()

    if len(routes) > 1:
        text += "\n<b>المسارات الاحتياطية:</b>\n"
        for route in routes[1:]:
            prov = await session.get(ApiProvider, route.api_provider_id)
            pname = prov.name if prov else f"#{route.api_provider_id}"
            icon = "🟢" if route.is_active else "⚪"
            b.button(
                text=f"{icon} {pname} — svc:{route.provider_service_id} (p{route.priority})",
                callback_data=f"pr:view:{route.id}",
            )
        b.button(text="➕ إضافة مزود احتياطي", callback_data=f"pr:add:{product.id}", style="success")
    else:
        text += (
            "\n⚠️ لا يوجد مزودون احتياطيون بعد.\n"
            "اضغط الزر أدناه لإضافة مزود بديل يُجرَّب تلقائياً عند فشل الأساسي."
        )
        b.button(text="➕ إضافة مزود احتياطي", callback_data=f"pr:add:{product.id}", style="success")

    b.button(text="🔙 رجوع للمنتج", callback_data=f"admin:prod_view:{product.id}")
    b.adjust(1)

    try:
        if edit:
            await target.edit_text(text, reply_markup=b.as_markup())
        else:
            await target.answer(text, reply_markup=b.as_markup())
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin:prod_routes:"))
async def prod_routes_list(callback: CallbackQuery, session):
    prod_id = int(callback.data.split(":")[2])
    product = await DynamicService.get_product(session, prod_id)
    if not product:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    await _render_routes(session, product, callback.message, edit=True)
    await callback.answer()


# ══════════════════════════════════════════════
# إضافة مسار جديد
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("pr:add:"))
async def prod_route_add_pick(callback: CallbackQuery, session, state: FSMContext):
    prod_id = int(callback.data.split(":")[2])
    product = await DynamicService.get_product(session, prod_id)
    if not product:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    result = await session.execute(
        select(ApiProvider).where(ApiProvider.is_active.is_(True))
    )
    providers = [p for p in result.scalars().all() if p.id != product.api_provider_id]
    if not providers:
        await callback.answer(
            "⚠️ لا يوجد مزودون مفعلون آخرون غير المزود الأساسي.", show_alert=True
        )
        return

    b = InlineKeyboardBuilder()
    for p in providers:
        b.button(text=f"🔌 {p.name}", callback_data=f"pr:pick:{prod_id}:{p.id}")
    b.button(text="🔙 رجوع", callback_data=f"admin:prod_routes:{prod_id}")
    b.adjust(1)
    await callback.message.edit_text(
        f"🔌 <b>اختر المزود الاحتياطي:</b>\n\n"
        f"📦 المنتج: <b>{product.name_ar}</b>",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pr:pick:"))
async def prod_route_ask_svc(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    prod_id = int(parts[2])
    provider_id = int(parts[3])
    await state.update_data(route_prod_id=prod_id, route_provider_id=provider_id)
    await callback.message.edit_text(
        "🔢 <b>أرسل آيدي الخدمة</b> عند المزود الاحتياطي المختار\n"
        "(مثل: <code>1234</code> أو <code>smm_service_1</code>):",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminProductRouteStates.waiting_service_id)
    await callback.answer()


@router.message(AdminProductRouteStates.waiting_service_id)
async def prod_route_svc_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    value = message.text.strip()
    if not value:
        await message.answer("⚠️ أرسل نصاً غير فارغ.")
        return

    # ── تعديل مسار موجود ──
    edit_field = data.get("route_edit_field")
    if edit_field == "svc":
        route_id = data.get("route_edit_id")
        route = await session.get(ProductProviderRoute, route_id)
        if route:
            route.provider_service_id = value
            await session.commit()
            product = await DynamicService.get_product(session, route.product_id)
            await message.answer(f"✅ تم تحديث آيدي الخدمة إلى: {value}")
            if product:
                await _render_routes(session, product, message, edit=False)
        await state.clear()
        return

    # ── مسار جديد: اسأل الأولوية ──
    await state.update_data(route_service_id=value)
    await message.answer(
        "📊 <b>أرسل الأولوية</b> (رقم أقل = أعلى أولوية):\n"
        "أرسل <code>/</code> للاستخدام الافتراضي (<b>100</b>).",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminProductRouteStates.waiting_priority)


@router.message(AdminProductRouteStates.waiting_priority)
async def prod_route_prio_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    raw = message.text.strip()

    # ── تعديل أولوية مسار موجود ──
    edit_field = data.get("route_edit_field")
    if edit_field == "prio":
        route_id = data.get("route_edit_id")
        route = await session.get(ProductProviderRoute, route_id)
        if route:
            if raw in ("/", "-", ""):
                await message.answer("لم يتم تغيير الأولوية.")
            else:
                try:
                    prio = int(raw)
                    if prio < 1:
                        raise InvalidOperation
                except InvalidOperation:
                    await message.answer("⚠️ أرقماً صحيحاً أكبر من صفر، أو ارسل / للاحتفاظ بالحالي.")
                    return
                route.priority = prio
                await session.commit()
                await message.answer(f"✅ تم تحديث الأولوية إلى {prio}")
            product = await DynamicService.get_product(session, route.product_id)
            if product:
                await _render_routes(session, product, message, edit=False)
        await state.clear()
        return

    # ── مسار جديد ──
    prod_id = data.get("route_prod_id")
    provider_id = data.get("route_provider_id")
    service_id = data.get("route_service_id")

    if raw in ("/", "-", "", "0"):
        priority = 100
    else:
        try:
            priority = int(raw)
            if priority < 1:
                raise InvalidOperation
        except InvalidOperation:
            await message.answer("⚠️ أرقماً صحيحاً أكبر من صفر، أو ارسل / للاختيار الافتراضي.")
            return

    try:
        await CatalogRoutingService.add_route(
            session, prod_id, provider_id, service_id, priority,
        )
    except Exception as e:
        logger.error("خطأ في إضافة مسار: %s", e)
        await message.answer("⚠️ حدث خطأ — تأكد من عدم تكرار المزود لهذا المنتج.")
        await state.clear()
        return

    product = await DynamicService.get_product(session, prod_id)
    await message.answer(f"✅ تم إضافة المزود الاحتياطي (خدمة: {service_id})")
    if product:
        await _render_routes(session, product, message, edit=False)
    await state.clear()


# ══════════════════════════════════════════════
# عرض/تعديل/حذف مسار احتياطي
# ══════════════════════════════════════════════


@router.callback_query(F.data.startswith("pr:view:"))
async def prod_route_view(callback: CallbackQuery, session):
    route_id = int(callback.data.split(":")[2])
    route = await session.get(ProductProviderRoute, route_id)
    if not route:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return

    provider = await session.get(ApiProvider, route.api_provider_id)
    pname = provider.name if provider else f"#{route.api_provider_id}"
    icon = "🟢" if route.is_active else "⚪"

    text = (
        f"{icon} <b>مسار احتياطي</b>\n\n"
        f"🔌 المزود: <b>{pname}</b>\n"
        f"🔢 آيدي الخدمة: <code>{route.provider_service_id}</code>\n"
        f"📊 الأولوية: <b>{route.priority}</b>"
    )
    b = InlineKeyboardBuilder()
    if route.is_active:
        b.button(text="⚪ تعطيل", callback_data=f"pr:toggle:{route.id}")
    else:
        b.button(text="🟢 تفعيل", callback_data=f"pr:toggle:{route.id}")
    b.button(text="🔢 آيدي الخدمة", callback_data=f"pr:svc:{route.id}", style="primary")
    b.button(text="📊 الأولوية", callback_data=f"pr:prio:{route.id}", style="primary")
    b.button(text="🗑 حذف", callback_data=f"pr:del:{route.id}", style="danger")
    b.button(text="🔙 رجوع", callback_data=f"admin:prod_routes:{route.product_id}")
    b.adjust(2, 2, 1, 1)
    await callback.message.edit_text(text, reply_markup=b.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("pr:toggle:"))
async def prod_route_toggle(callback: CallbackQuery, session):
    route_id = int(callback.data.split(":")[2])
    route = await session.get(ProductProviderRoute, route_id)
    if not route:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    route.is_active = not route.is_active
    await session.commit()
    await callback.answer("✅ تم التحديث.")
    product = await DynamicService.get_product(session, route.product_id)
    if product:
        await _render_routes(session, product, callback.message)


@router.callback_query(F.data.startswith("pr:del:"))
async def prod_route_delete(callback: CallbackQuery, session):
    route_id = int(callback.data.split(":")[2])
    route = await session.get(ProductProviderRoute, route_id)
    if not route:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    pid = route.product_id
    await CatalogRoutingService.remove_route(session, route_id)
    await callback.answer("🗑 تم الحذف.")
    product = await DynamicService.get_product(session, pid)
    if product:
        await _render_routes(session, product, callback.message)


@router.callback_query(F.data.startswith("pr:svc:"))
async def prod_route_edit_svc(callback: CallbackQuery, state: FSMContext):
    route_id = int(callback.data.split(":")[2])
    await state.update_data(route_edit_id=route_id, route_edit_field="svc")
    await callback.message.edit_text(
        "🔢 <b>أرسل آيدي الخدمة الجديد:</b>",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminProductRouteStates.waiting_service_id)
    await callback.answer()


@router.callback_query(F.data.startswith("pr:prio:"))
async def prod_route_edit_prio(callback: CallbackQuery, state: FSMContext):
    route_id = int(callback.data.split(":")[2])
    await state.update_data(route_edit_id=route_id, route_edit_field="prio")
    await callback.message.edit_text(
        "📊 <b>أرسل الأولوية الجديدة:</b>\n"
        "(رقم أقل = أعلى أولوية) — أرسل <code>/</code> للاحتفاظ بالحالي.",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminProductRouteStates.waiting_priority)
    await callback.answer()
