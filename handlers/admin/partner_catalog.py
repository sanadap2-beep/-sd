"""اختيار خدمة واحدة من بوت الصديق ووضعها في قسم محدد."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import ApiProvider
from filters.admin_filter import IsAdmin
from protocols.base import ProtocolError
from protocols.partner_v1 import is_partner_v1_provider, partner_type_meta
from services.partner_catalog_service import (
    SERVICES_PER_PAGE,
    SUBS_PER_PAGE,
    PartnerCatalogService,
)
from services.pulled_services_service import PulledServicesService
from states.states import AdminPartnerPickStates

logger = logging.getLogger(__name__)

router = Router(name="admin_partner_catalog")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _types_kb(provider_id: int):
    b = InlineKeyboardBuilder()
    for key, emoji, label in PartnerCatalogService.type_rows():
        b.button(text=f"{emoji} {label}", callback_data=f"pk:l:{provider_id}:{key}:0")
    b.button(text="📋 كل الأنواع", callback_data=f"pk:l:{provider_id}:all:0")
    b.button(text="🔙 المزود", callback_data=f"admin:aprov_view:{provider_id}")
    b.adjust(1)
    return b.as_markup()


def _list_kb(provider_id: int, type_key: str, services, page: int, total: int):
    b = InlineKeyboardBuilder()
    for svc in services:
        name = (svc.name or "خدمة")[:36]
        b.button(
            text=f"#{svc.external_id} · {svc.rate}$ · {name}",
            callback_data=f"pk:v:{provider_id}:{svc.external_id}",
        )
    nav = 0
    last = max(0, (total - 1) // SERVICES_PER_PAGE) if total else 0
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"pk:l:{provider_id}:{type_key}:{page - 1}")
        nav += 1
    if page < last:
        b.button(text="التالي ▶️", callback_data=f"pk:l:{provider_id}:{type_key}:{page + 1}")
        nav += 1
    b.button(text="🔙 الأنواع", callback_data=f"pk:h:{provider_id}")
    rows = [1] * len(services)
    if nav:
        rows.append(nav)
    rows.append(1)
    if rows:
        b.adjust(*rows)
    return b.as_markup()


def _detail_kb(provider_id: int, service_id: str, type_key: str):
    b = InlineKeyboardBuilder()
    b.button(text="📂 وضعها في قسم عندي", callback_data=f"pk:pub:{provider_id}:{service_id}")
    b.button(text="🔙 القائمة", callback_data=f"pk:l:{provider_id}:{type_key}:0")
    b.adjust(1)
    return b.as_markup()


def _subs_kb(provider_id: int, service_id: str, subs, page: int):
    b = InlineKeyboardBuilder()
    start = page * SUBS_PER_PAGE
    chunk = subs[start : start + SUBS_PER_PAGE]
    for sub in chunk:
        cat = getattr(sub, "category", None)
        cat_name = getattr(cat, "name_ar", "") if cat is not None else ""
        label = f"{sub.emoji or ''} {sub.name_ar}".strip()
        if cat_name:
            label = f"{cat_name} / {label}"
        b.button(text=label[:60], callback_data=f"pk:sc:{provider_id}:{service_id}:{sub.id}")
    nav = 0
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"pk:ss:{provider_id}:{service_id}:{page - 1}")
        nav += 1
    if start + SUBS_PER_PAGE < len(subs):
        b.button(text="التالي ▶️", callback_data=f"pk:ss:{provider_id}:{service_id}:{page + 1}")
        nav += 1
    b.button(text="🔙 الخدمة", callback_data=f"pk:v:{provider_id}:{service_id}")
    rows = [1] * len(chunk)
    if nav:
        rows.append(nav)
    rows.append(1)
    if rows:
        b.adjust(*rows)
    return b.as_markup()


async def _provider(session, provider_id: int) -> ApiProvider | None:
    return await session.get(ApiProvider, provider_id)


@router.callback_query(F.data.startswith("pk:h:"))
async def partner_home(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    provider_id = int(callback.data.split(":")[2])
    provider = await _provider(session, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود", show_alert=True)
        return
    await callback.answer()
    hint = (
        "🤝 <b>اختيار خدمة واحدة</b>\n\n"
        f"المزود: <b>{provider.name}</b>\n\n"
        "ما راح ينزل الكتالوج كله في البوت.\n"
        "اختر النوع → اختر خدمة → حطها في القسم/الفرع اللي بدك ياه → سعر البيع.\n"
        "المستخدم ما بيشوف شي إلا بعد هالخطوة."
    )
    if not is_partner_v1_provider(provider):
        hint += (
            "\n\n⚠️ هذا المزود مش مضبوط على قالب بوت الصديق. "
            "أضفه من Custom ← 🤝 بوت صديق."
        )
    await callback.message.edit_text(hint, reply_markup=_types_kb(provider_id))


@router.callback_query(F.data.startswith("pk:l:"))
async def partner_list(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    provider_id = int(parts[2])
    type_key = parts[3]
    try:
        page = int(parts[4])
    except ValueError:
        page = 0
    provider = await _provider(session, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود", show_alert=True)
        return
    live_type = None if type_key in {"all", "-", ""} else type_key
    try:
        services, total = await PartnerCatalogService.list_live(
            provider, live_type or "", page=page
        )
    except (ProtocolError, ValueError) as exc:
        await callback.answer()
        await callback.message.edit_text(
            f"❌ تعذر جلب الخدمات:\n<code>{exc}</code>",
            reply_markup=_types_kb(provider_id),
        )
        return
    emoji, label = partner_type_meta(type_key) if live_type else ("📦", "كل الأنواع")
    last = max(1, (total + SERVICES_PER_PAGE - 1) // SERVICES_PER_PAGE)
    await callback.answer()
    await callback.message.edit_text(
        f"{emoji} <b>{label}</b>\n"
        f"📊 {total} خدمة عند الصديق · صفحة {page + 1}/{last}\n"
        "السعر الظاهر تكلفة الصديق. لن تظهر في متجرك حتى تضعها في قسم.",
        reply_markup=_list_kb(provider_id, type_key, services, page, total),
    )


@router.callback_query(F.data.startswith("pk:v:"))
async def partner_view(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    provider_id = int(parts[2])
    service_id = parts[3]
    provider = await _provider(session, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود", show_alert=True)
        return
    try:
        proto = await PartnerCatalogService.fetch_one(provider, service_id)
    except (ProtocolError, ValueError) as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    if proto is None:
        await callback.answer("الخدمة غير موجودة عند الصديق", show_alert=True)
        return
    type_key = proto.service_type or "all"
    emoji, label = partner_type_meta(type_key)
    needs = []
    if proto.requires_link:
        needs.append("رابط")
    if proto.requires_quantity:
        needs.append(f"كمية {proto.min_quantity}-{proto.max_quantity}")
    need_text = " | ".join(needs) if needs else "بدون إدخال من المستخدم (سعر ثابت)"
    await callback.answer()
    await callback.message.edit_text(
        "🎯 <b>خدمة من بوت الصديق</b>\n\n"
        f"الاسم: {proto.name}\n"
        f"النوع: {emoji} {label}\n"
        f"التصنيف: {proto.category or '—'}\n"
        f"🆔 آيدي الصديق: <code>{proto.external_id}</code>\n"
        f"💵 تكلفته: <b>{proto.rate}$</b>\n"
        f"📥 المطلوب من المشتري: {need_text}\n\n"
        "اضغط «وضعها في قسم» واختر القسم الفرعي عندك ثم سعر البيع.",
        reply_markup=_detail_kb(provider_id, proto.external_id, type_key),
    )


@router.callback_query(F.data.startswith("pk:pub:"))
async def partner_pub_start(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    await _show_subs(callback, session, state, int(parts[2]), parts[3], 0)


@router.callback_query(F.data.startswith("pk:ss:"))
async def partner_subs_page(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    await _show_subs(callback, session, state, int(parts[2]), parts[3], int(parts[4]))


async def _show_subs(callback, session, state, provider_id: int, service_id: str, page: int):
    provider = await _provider(session, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود", show_alert=True)
        return
    subs = await PulledServicesService.destination_subcategories(session)
    await state.clear()
    await callback.answer()
    if not subs:
        await callback.message.edit_text(
            "⚠️ ما في أقسام فرعية.\nأنشئ قسماً وفروعه من «إدارة الأقسام» ثم ارجع لهون.",
            reply_markup=_detail_kb(provider_id, service_id, "all"),
        )
        return
    await callback.message.edit_text(
        "📂 <b>وين بدك تظهر الخدمة بالمتجر؟</b>\n\n"
        "القسم الرئيسي / الفرع. مثال: أرقام → تيليجرام، أو رشق → إنستغرام.",
        reply_markup=_subs_kb(provider_id, service_id, subs, page),
    )


@router.callback_query(F.data.startswith("pk:sc:"))
async def partner_sub_picked(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    provider_id = int(parts[2])
    service_id = parts[3]
    sub_id = int(parts[4])
    provider = await _provider(session, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود", show_alert=True)
        return
    try:
        proto = await PartnerCatalogService.fetch_one(provider, service_id)
    except (ProtocolError, ValueError) as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    if proto is None:
        await callback.answer("الخدمة اختفت عند الصديق", show_alert=True)
        return
    await state.update_data(
        pk_provider_id=provider_id,
        pk_service_id=service_id,
        pk_sub_id=sub_id,
    )
    await state.set_state(AdminPartnerPickStates.waiting_sell_price)
    unit = "لكل 1000" if proto.requires_quantity else "للطلب"
    await callback.answer()
    await callback.message.edit_text(
        "💰 <b>سعر البيع عندك</b>\n\n"
        f"الخدمة: {proto.name}\n"
        f"تكلفة الصديق: {proto.rate}$\n"
        f"الوحدة: {unit}\n\n"
        "أرسل سعر البيع بالدولار (مثال: 1.50)."
    )


@router.message(AdminPartnerPickStates.waiting_sell_price)
async def partner_price_received(message: Message, session, state: FSMContext):
    data = await state.get_data()
    try:
        sell_price = PulledServicesService.parse_sell_price(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    provider = await _provider(session, int(data.get("pk_provider_id") or 0))
    if provider is None:
        await state.clear()
        await message.answer("⚠️ المزود لم يعد موجوداً.")
        return
    try:
        proto = await PartnerCatalogService.fetch_one(provider, str(data.get("pk_service_id")))
        if proto is None:
            raise ValueError("الخدمة غير موجودة عند الصديق.")
        product = await PartnerCatalogService.publish_one(
            session,
            provider,
            proto,
            int(data["pk_sub_id"]),
            sell_price,
        )
        await session.commit()
    except Exception:
        logger.exception("Failed to publish partner service")
        await message.answer("❌ تعذر إنشاء المنتج. راجع قناة الأخطاء.")
        return
    await state.clear()
    unit = " / 1000" if product.requires_quantity else ""
    await message.answer(
        "✅ <b>انربطت الخدمة بقسمك — الباقي ما انسحب</b>\n\n"
        f"📦 {product.name_ar}\n"
        f"💰 سعر البيع: {product.price_usd}${unit}\n"
        f"💵 التكلفة: {product.cost_price_usd}$\n"
        f"🆔 المنتج: <code>{product.id}</code>\n\n"
        "المستخدم بيشوفها فقط داخل القسم اللي اخترته."
    )
