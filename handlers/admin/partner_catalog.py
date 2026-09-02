"""سحب قسم من بوت الصديق ووضعه في قسم محدد بنسبة ربح."""

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
    GROUPS_PER_PAGE,
    SERVICES_PER_PAGE,
    SUBS_PER_PAGE,
    PartnerCatalogService,
    apply_margin,
    parse_margin_percent,
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
        b.button(text=f"{emoji} {label}", callback_data=f"pk:k:{provider_id}:{key}:0")
    b.button(text="📋 كل الأنواع", callback_data=f"pk:k:{provider_id}:all:0")
    b.button(text="🔙 المزود", callback_data=f"admin:aprov_view:{provider_id}")
    b.adjust(1)
    return b.as_markup()


def _groups_kb(provider_id: int, type_key: str, groups, page: int):
    b = InlineKeyboardBuilder()
    start = page * GROUPS_PER_PAGE
    chunk = groups[start : start + GROUPS_PER_PAGE]
    for token, emoji, label, items in chunk:
        b.button(
            text=f"{emoji} {label} ({len(items)})",
            callback_data=f"pk:kd:{provider_id}:{type_key}:{token}",
        )
    nav = 0
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"pk:k:{provider_id}:{type_key}:{page - 1}")
        nav += 1
    if start + GROUPS_PER_PAGE < len(groups):
        b.button(text="التالي ▶️", callback_data=f"pk:k:{provider_id}:{type_key}:{page + 1}")
        nav += 1
    b.button(text="🔙 الأنواع", callback_data=f"pk:h:{provider_id}")
    rows = [1] * len(chunk)
    if nav:
        rows.append(nav)
    rows.append(1)
    if rows:
        b.adjust(*rows)
    return b.as_markup()


def _group_detail_kb(provider_id: int, type_key: str, token: str):
    b = InlineKeyboardBuilder()
    b.button(
        text="📥 سحب القسم كامل بنسبة ربح",
        callback_data=f"pk:kp:{provider_id}:{type_key}:{token}",
    )
    b.button(
        text="👁 استعراض الخدمات واحدة واحدة",
        callback_data=f"pk:kl:{provider_id}:{type_key}:{token}:0",
    )
    b.button(text="🔙 أقسام الصديق", callback_data=f"pk:k:{provider_id}:{type_key}:0")
    b.adjust(1)
    return b.as_markup()


def _list_kb(provider_id: int, type_key: str, token: str, services, page: int, total: int):
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
        b.button(
            text="◀️ السابق",
            callback_data=f"pk:kl:{provider_id}:{type_key}:{token}:{page - 1}",
        )
        nav += 1
    if page < last:
        b.button(
            text="التالي ▶️",
            callback_data=f"pk:kl:{provider_id}:{type_key}:{token}:{page + 1}",
        )
        nav += 1
    b.button(text="🔙 القسم", callback_data=f"pk:kd:{provider_id}:{type_key}:{token}")
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
    b.button(text="🔙 القائمة", callback_data=f"pk:k:{provider_id}:{type_key}:0")
    b.adjust(1)
    return b.as_markup()


def _subs_kb(
    provider_id: int,
    dest_prefix: str,
    page_prefix: str,
    back_cb: str,
    subs,
    page: int,
):
    b = InlineKeyboardBuilder()
    start = page * SUBS_PER_PAGE
    chunk = subs[start : start + SUBS_PER_PAGE]
    for sub in chunk:
        cat = getattr(sub, "category", None)
        cat_name = getattr(cat, "name_ar", "") if cat is not None else ""
        label = f"{sub.emoji or ''} {sub.name_ar}".strip()
        if cat_name:
            label = f"{cat_name} / {label}"
        b.button(text=label[:60], callback_data=f"{dest_prefix}:{sub.id}")
    nav = 0
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"{page_prefix}:{page - 1}")
        nav += 1
    if start + SUBS_PER_PAGE < len(subs):
        b.button(text="التالي ▶️", callback_data=f"{page_prefix}:{page + 1}")
        nav += 1
    b.button(text="🔙 رجوع", callback_data=back_cb)
    rows = [1] * len(chunk)
    if nav:
        rows.append(nav)
    rows.append(1)
    if rows:
        b.adjust(*rows)
    return b.as_markup()


async def _provider(session, provider_id: int) -> ApiProvider | None:
    return await session.get(ApiProvider, provider_id)


async def _load_group(provider, type_key: str, token: str):
    services = await PartnerCatalogService.fetch_type(provider, type_key)
    group = PartnerCatalogService.resolve_group(services, type_key, token)
    if group is None:
        raise ValueError("هذا القسم اختفى عند الصديق.")
    return group


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
        "📥 <b>سحب قسم من بوت الصديق</b>\n\n"
        f"المزود: <b>{provider.name}</b>\n\n"
        "مثال: أرقام تيليجرام الجاهزة.\n"
        "1) النوع عند الصديق (تيليجرام / واتساب / …)\n"
        "2) قسمه (الجاهزة أو أي تصنيف يظهر)\n"
        "3) القسم/الفرع عندك اللي بدك تنزّل فيه\n"
        "4) نسبة الربح (مثال 30 = التكلفة + 30%)\n\n"
        "ما بينزل الكتالوج كله. بس القسم اللي تختاره."
    )
    if not is_partner_v1_provider(provider):
        hint += (
            "\n\n⚠️ هذا المزود مش مضبوط على قالب بوت الصديق. "
            "أضفه من Custom ← 🤝 بوت صديق."
        )
    await callback.message.edit_text(hint, reply_markup=_types_kb(provider_id))


@router.callback_query(F.data.startswith("pk:k:"))
async def partner_groups(callback: CallbackQuery, session, state: FSMContext):
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
    try:
        services = await PartnerCatalogService.fetch_type(provider, type_key)
    except (ProtocolError, ValueError) as exc:
        await callback.answer()
        await callback.message.edit_text(
            f"❌ تعذر جلب الأقسام:\n<code>{exc}</code>",
            reply_markup=_types_kb(provider_id),
        )
        return
    groups = PartnerCatalogService.build_groups(services, type_key)
    emoji, label = partner_type_meta(type_key) if type_key not in {"all", ""} else ("📦", "كل الأنواع")
    await callback.answer()
    if not services:
        await callback.message.edit_text(
            f"{emoji} <b>{label}</b>\n\nما في خدمات بهالنوع عند الصديق.",
            reply_markup=_types_kb(provider_id),
        )
        return
    await callback.message.edit_text(
        f"{emoji} <b>{label}</b> — {len(services)} خدمة عند الصديق\n\n"
        "اختر <b>قسم الصديق</b> اللي بدك تسحبه. "
        "مثال: «أرقام تيليجرام الجاهزة» ثم حط نسبة ربح وانزله بقسمك.",
        reply_markup=_groups_kb(provider_id, type_key, groups, page),
    )


@router.callback_query(F.data.startswith("pk:kd:"))
async def partner_group_detail(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    provider_id = int(parts[2])
    type_key = parts[3]
    token = parts[4]
    provider = await _provider(session, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود", show_alert=True)
        return
    try:
        token_key, emoji, label, items = await _load_group(provider, type_key, token)
    except (ProtocolError, ValueError) as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    costs = [s.rate for s in items if s.rate is not None]
    cheapest = min(costs) if costs else 0
    dearest = max(costs) if costs else 0
    preview = "\n".join(
        f"• #{s.external_id} · {s.rate}$ · {(s.name or '')[:40]}" for s in items[:6]
    )
    extra = f"\n… و {len(items) - 6} غيرها" if len(items) > 6 else ""
    await callback.answer()
    await callback.message.edit_text(
        f"{emoji} <b>{label}</b>\n\n"
        f"📊 {len(items)} خدمة ستنزل دفعة واحدة\n"
        f"💵 تكلفة الصديق: من {cheapest}$ إلى {dearest}$\n\n"
        f"{preview}{extra}\n\n"
        "اسحب القسم كامل، حط نسبة ربح، واختر القسم عندك.\n"
        "سعر البيع = تكلفة الصديق × (1 + النسبة).",
        reply_markup=_group_detail_kb(provider_id, type_key, token_key),
    )


@router.callback_query(F.data.startswith("pk:kl:"))
async def partner_group_list(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    if len(parts) < 6:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    provider_id = int(parts[2])
    type_key = parts[3]
    token = parts[4]
    try:
        page = int(parts[5])
    except ValueError:
        page = 0
    provider = await _provider(session, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود", show_alert=True)
        return
    try:
        _token, emoji, label, items = await _load_group(provider, type_key, token)
    except (ProtocolError, ValueError) as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    start = page * SERVICES_PER_PAGE
    chunk = items[start : start + SERVICES_PER_PAGE]
    last = max(1, (len(items) + SERVICES_PER_PAGE - 1) // SERVICES_PER_PAGE)
    await callback.answer()
    await callback.message.edit_text(
        f"{emoji} <b>{label}</b>\n"
        f"صفحة {page + 1}/{last} — يمكنك سحب واحدة أو الرجوع لسحب القسم كامل.",
        reply_markup=_list_kb(provider_id, type_key, token, chunk, page, len(items)),
    )


@router.callback_query(F.data.startswith("pk:kp:"))
async def partner_bulk_start(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    await _show_bulk_subs(callback, session, state, int(parts[2]), parts[3], parts[4], 0)


@router.callback_query(F.data.startswith("pk:ks:"))
async def partner_bulk_subs_page(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    await _show_bulk_subs(
        callback, session, state, int(parts[2]), parts[3], parts[4], int(parts[5])
    )


async def _show_bulk_subs(callback, session, state, provider_id, type_key, token, page):
    provider = await _provider(session, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود", show_alert=True)
        return
    subs = await PulledServicesService.destination_subcategories(session)
    await state.clear()
    await callback.answer()
    if not subs:
        await callback.message.edit_text(
            "⚠️ ما في أقسام فرعية عندك.\nأنشئ قسماً وفروعه من «إدارة الأقسام» ثم ارجع.",
            reply_markup=_group_detail_kb(provider_id, type_key, token),
        )
        return
    await callback.message.edit_text(
        "📂 <b>وين بدك ينزل القسم بمتجرك؟</b>\n\n"
        "مثال: أرقام / تيليجرام. كل خدمات قسم الصديق رح تظهر داخل هالفرع فقط.",
        reply_markup=_subs_kb(
            provider_id,
            dest_prefix=f"pk:kc:{provider_id}:{type_key}:{token}",
            page_prefix=f"pk:ks:{provider_id}:{type_key}:{token}",
            back_cb=f"pk:kd:{provider_id}:{type_key}:{token}",
            subs=subs,
            page=page,
        ),
    )


@router.callback_query(F.data.startswith("pk:kc:"))
async def partner_bulk_sub_picked(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    provider_id = int(parts[2])
    type_key = parts[3]
    token = parts[4]
    sub_id = int(parts[5])
    provider = await _provider(session, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود", show_alert=True)
        return
    try:
        _token, emoji, label, items = await _load_group(provider, type_key, token)
    except (ProtocolError, ValueError) as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    sample = items[0].rate if items else 0
    sample_sell = apply_margin(sample, parse_margin_percent("30")) if items else 0
    await state.update_data(
        pk_mode="bulk",
        pk_provider_id=provider_id,
        pk_type=type_key,
        pk_group=token,
        pk_sub_id=sub_id,
        pk_count=len(items),
        pk_label=label,
    )
    await state.set_state(AdminPartnerPickStates.waiting_margin)
    await callback.answer()
    await callback.message.edit_text(
        "💰 <b>نسبة الربح على القسم كامل</b>\n\n"
        f"{emoji} {label}\n"
        f"📊 {len(items)} خدمة ستنزل في القسم الذي اخترته\n\n"
        f"مثال: تكلفة {sample}$ + 30% = <b>{sample_sell}$</b> سعر البيع\n\n"
        "أرسل النسبة فقط (مثال: <code>30</code> أو <code>30%</code>)."
    )


@router.message(AdminPartnerPickStates.waiting_margin)
async def partner_margin_received(message: Message, session, state: FSMContext):
    data = await state.get_data()
    try:
        margin = parse_margin_percent(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    provider = await _provider(session, int(data.get("pk_provider_id") or 0))
    if provider is None:
        await state.clear()
        await message.answer("⚠️ المزود لم يعد موجوداً.")
        return
    if data.get("pk_mode") == "one":
        await _publish_one_with_margin(message, session, state, provider, data, margin)
        return
    try:
        _token, emoji, label, items = await _load_group(
            provider, str(data.get("pk_type") or "tg"), str(data.get("pk_group") or "ready")
        )
        created, updated = await PartnerCatalogService.publish_group(
            session,
            provider,
            items,
            int(data["pk_sub_id"]),
            margin,
        )
        await session.commit()
    except Exception:
        logger.exception("Failed to publish partner group")
        await message.answer("❌ تعذر سحب القسم. راجع قناة الأخطاء.")
        return
    await state.clear()
    sample = items[0] if items else None
    sample_line = ""
    if sample is not None:
        sell = apply_margin(sample.rate, margin)
        sample_line = (
            f"\nمثال: {sample.name} · تكلفة {sample.rate}$ → بيع {sell}$"
        )
    await message.answer(
        "✅ <b>نزل قسم الصديق عندك</b>\n\n"
        f"{emoji} {label}\n"
        f"📈 نسبة الربح: {margin}%\n"
        f"🆕 منتجات جديدة: {created}\n"
        f"🔄 محدّثة (كانت موجودة): {updated}\n"
        f"{sample_line}\n\n"
        "المستخدم بيشوفهم فقط داخل القسم اللي اخترته. باقي كتالوج الصديق ما انسحب."
    )


async def _publish_one_with_margin(message, session, state, provider, data, margin):
    try:
        proto = await PartnerCatalogService.fetch_one(provider, str(data.get("pk_service_id")))
        if proto is None:
            raise ValueError("الخدمة غير موجودة عند الصديق.")
        sell = apply_margin(proto.rate, margin)
        product = await PartnerCatalogService.publish_one(
            session,
            provider,
            proto,
            int(data["pk_sub_id"]),
            sell,
            margin_percent=margin,
        )
        await session.commit()
    except Exception:
        logger.exception("Failed to publish partner service with margin")
        await message.answer("❌ تعذر إنشاء المنتج. راجع قناة الأخطاء.")
        return
    await state.clear()
    await message.answer(
        "✅ <b>انربطت الخدمة بقسمك</b>\n\n"
        f"📦 {product.name_ar}\n"
        f"📈 نسبة الربح: {margin}%\n"
        f"💰 سعر البيع: {product.price_usd}$\n"
        f"💵 التكلفة: {product.cost_price_usd}$"
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
        "لخدمة واحدة: ضعها في قسم. لسحب القسم كامل ارجع واختر سحب القسم.",
        reply_markup=_detail_kb(provider_id, proto.external_id, type_key),
    )


@router.callback_query(F.data.startswith("pk:pub:"))
async def partner_pub_start(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    await _show_one_subs(callback, session, state, int(parts[2]), parts[3], 0)


@router.callback_query(F.data.startswith("pk:ss:"))
async def partner_subs_page(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    await _show_one_subs(callback, session, state, int(parts[2]), parts[3], int(parts[4]))


async def _show_one_subs(callback, session, state, provider_id: int, service_id: str, page: int):
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
        "📂 <b>وين بدك تظهر الخدمة بالمتجر؟</b>",
        reply_markup=_subs_kb(
            provider_id,
            dest_prefix=f"pk:sc:{provider_id}:{service_id}",
            page_prefix=f"pk:ss:{provider_id}:{service_id}",
            back_cb=f"pk:v:{provider_id}:{service_id}",
            subs=subs,
            page=page,
        ),
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
        pk_mode="one",
        pk_provider_id=provider_id,
        pk_service_id=service_id,
        pk_sub_id=sub_id,
    )
    await state.set_state(AdminPartnerPickStates.waiting_margin)
    sample = apply_margin(proto.rate, parse_margin_percent("30"))
    await callback.answer()
    await callback.message.edit_text(
        "💰 <b>نسبة الربح</b>\n\n"
        f"الخدمة: {proto.name}\n"
        f"تكلفة الصديق: {proto.rate}$\n"
        f"مثال 30%: سعر البيع {sample}$\n\n"
        "أرسل النسبة (مثال: <code>30</code>)."
    )


@router.message(AdminPartnerPickStates.waiting_sell_price)
async def partner_price_received(message: Message, session, state: FSMContext):
    # Kept for compatibility if an old chat is still on sell-price state.
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
    await message.answer(
        f"✅ انربطت الخدمة بقسمك\n📦 {product.name_ar}\n💰 {product.price_usd}$"
    )
