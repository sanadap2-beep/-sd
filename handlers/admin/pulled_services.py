"""Admin catalog of pulled SMM services.

Pulled provider services stay hidden from the storefront. The admin browses
platform → type → cheapest-to-expensive, picks a destination section, sets a
sell price (per 1000), and only then is a product created.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from database.models import ApiProvider, Category, ProviderService, ProviderServiceStatus, SubCategory
from filters.admin_filter import IsAdmin
from services.pulled_services_service import SERVICES_PER_PAGE, PulledServicesService
from services.smm_catalog import kind_meta, platform_meta
from services.smm_sections_service import SmmSectionsService
from states.states import AdminPulledServicesStates

logger = logging.getLogger(__name__)

router = Router(name="admin_pulled_services")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

SUBS_PER_PAGE = 8


def _platforms_kb(rows: list[tuple[str, str, str, int]]):
    b = InlineKeyboardBuilder()
    for key, emoji, label, count in rows:
        b.button(text=f"{emoji} {label} ({count})", callback_data=f"ps:pl:{key}")
    b.button(
        text="🚀 إنشاء أقسام الرشق تلقائياً (أرخص 5 لكل نوع)",
        callback_data="ps:build",
    )
    b.button(
        text="🛍 مزامنة الاشتراكات الرقمية (ggsoma) الآن",
        callback_data="ps:subsync",
    )
    b.button(
        text="🏬 مزامنة متجر كامل ← قسم باسم المتجر",
        callback_data="ps:storesync",
    )
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    layout = [2] * (len(rows) // 2)
    if len(rows) % 2:
        layout.append(1)
    layout.extend([1, 1, 1, 1])
    b.adjust(*layout)
    return b.as_markup()


def _kinds_kb(platform_key: str, rows: list[tuple[str, str, str, int]]):
    b = InlineKeyboardBuilder()
    for key, emoji, label, count in rows:
        b.button(
            text=f"{emoji} {label} ({count})",
            callback_data=f"ps:kd:{platform_key}:{key}:0",
        )
    b.button(text="🔙 المنصات", callback_data="admin:pulled_services")
    b.adjust(2)
    return b.as_markup()


def _services_kb(platform_key: str, kind_key: str, services, page: int, total: int):
    from services.service_localization_service import display_service_name

    b = InlineKeyboardBuilder()
    for service in services:
        rate = service.rate_usd or 0
        name = display_service_name(
            service.name, service.category, service.service_type
        )[:36]
        b.button(
            text=f"{rate}$ · {name}",
            callback_data=f"ps:sv:{service.id}",
        )
    nav = []
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"ps:kd:{platform_key}:{kind_key}:{page - 1}")
        nav.append(1)
    last_page = max(0, (total - 1) // SERVICES_PER_PAGE)
    if page < last_page:
        b.button(text="التالي ▶️", callback_data=f"ps:kd:{platform_key}:{kind_key}:{page + 1}")
        nav.append(1)
    b.button(text="🔙 الأنواع", callback_data=f"ps:pl:{platform_key}")
    rows = [1] * len(services)
    if nav:
        rows.append(len(nav))
    rows.append(1)
    if rows:
        b.adjust(*rows)
    return b.as_markup()


def _service_detail_kb(service_id: int, platform_key: str, kind_key: str, page: int):
    b = InlineKeyboardBuilder()
    b.button(text="📤 نشر في البوت", callback_data=f"ps:pb:{service_id}")
    b.button(text="🔙 القائمة", callback_data=f"ps:kd:{platform_key}:{kind_key}:{page}")
    b.adjust(1)
    return b.as_markup()


def _sections_dest_kb(
    service_id: int,
    sections: list[tuple[SubCategory, int]],
    platform_key: str,
    kind_key: str,
    page: int,
):
    """قائمة الأقسام الداخلية لتطبيق (المكان الصحيح لنشر خدمة SMM)."""
    b = InlineKeyboardBuilder()
    for section, count in sections:
        b.button(
            text=f"{section.emoji or ''} {section.name_ar} ({count})",
            callback_data=f"ps:sc:{service_id}:{section.id}",
        )
    b.button(
        text="🗂 كل الأقسام المتاحة",
        callback_data=f"ps:subs:{service_id}:0",
    )
    b.button(text="🔙 تفاصيل الخدمة", callback_data=f"ps:sv:{service_id}")
    b.adjust(1)
    return b.as_markup()


def _no_sections_dest_kb(service_id: int):
    b = InlineKeyboardBuilder()
    b.button(
        text="🚀 أنشئ أقسام الرشق تلقائياً (أرخص 5 لكل نوع)",
        callback_data="ps:build",
    )
    b.button(
        text="🗂 كل الأقسام المتاحة",
        callback_data=f"ps:subs:{service_id}:0",
    )
    b.button(text="🔙 تفاصيل الخدمة", callback_data=f"ps:sv:{service_id}")
    b.adjust(1)
    return b.as_markup()


def _build_report_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📥 الخدمات المسحوبة", callback_data="admin:pulled_services")
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


def _subs_kb(service_id: int, subs, page: int):
    b = InlineKeyboardBuilder()
    start = page * SUBS_PER_PAGE
    chunk = subs[start : start + SUBS_PER_PAGE]
    for sub in chunk:
        cat = getattr(sub, "category", None)
        cat_name = getattr(cat, "name_ar", "") if cat is not None else ""
        # قسم داخلي داخل تطبيق؟ اعرض «التطبيق / القسم الداخلي» ليظهر سياقه.
        parent = getattr(sub, "parent", None)
        if parent is not None:
            cat_name = getattr(parent, "name_ar", "") or cat_name
        label = f"{sub.emoji or ''} {sub.name_ar}".strip()
        if cat_name:
            label = f"{cat_name} / {label}"
        b.button(text=label[:60], callback_data=f"ps:sc:{service_id}:{sub.id}")
    nav = []
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"ps:ss:{service_id}:{page - 1}")
        nav.append(1)
    if start + SUBS_PER_PAGE < len(subs):
        b.button(text="التالي ▶️", callback_data=f"ps:ss:{service_id}:{page + 1}")
        nav.append(1)
    b.button(text="🔙 تفاصيل الخدمة", callback_data=f"ps:sv:{service_id}")
    rows = [1] * len(chunk)
    if nav:
        rows.append(len(nav))
    rows.append(1)
    if rows:
        b.adjust(*rows)
    return b.as_markup()


async def _show_platforms(callback: CallbackQuery, session) -> None:
    rows = await PulledServicesService.platform_counts(session)
    if not rows:
        await callback.message.edit_text(
            "📥 <b>خدمات مسحوبة</b>\n\n"
            "لا توجد خدمات مسحوبة حالياً.\n"
            "اسحب الخدمات من «مزودو المتجر» أولاً.\n\n"
            "⚠️ المزامنة العادية <b>لا تنشر</b> شيئاً — باستثناء زر "
            "«🛍 مزامنة الاشتراكات الرقمية» الذي ينشر كتالوج ggsoma تلقائياً.",
            reply_markup=_platforms_kb([]),
        )
        return
    total = sum(row[3] for row in rows)
    await callback.message.edit_text(
        "📥 <b>خدمات مسحوبة من المزودين</b>\n\n"
        f"المجموع: <b>{total}</b> خدمة مخفية عن المتجر.\n"
        "اختر المنصة ثم النوع (لايكات / مشاهدات / متابعون…).\n"
        "الترتيب داخل كل نوع: الأرخص ← الأغلى.\n\n"
        "🚀 <b>البناء التلقائي</b> ينشئ لكل تطبيق أقسامه الداخلية "
        "(متابعون/لايكات/مشاهدات...) وينشر أرخص 5 خدمات في كل نوع، "
        "وتستطيع بعده نشر أي خدمة يدوياً بسعرك الخاص داخل قسمها.",
        reply_markup=_platforms_kb(rows),
    )


@router.callback_query(F.data == "admin:pulled_services")
async def pulled_home(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _show_platforms(callback, session)


@router.callback_query(F.data == "ps:build")
async def pulled_build_sections(callback: CallbackQuery, session, state: FSMContext):
    """🚀 البناء التلقائي: أقسام داخلية لكل تطبيق + أول 5 خدمات أرخص بكل نوع."""
    await state.clear()
    await callback.answer("🚀 جارٍ الإنشاء والتحديث...")
    report = await SmmSectionsService.build(session)
    lines = [
        "🚀 <b>تم تنفيذ البناء التلقائي لأقسام الرشق</b>\n",
        f"📱 التطبيقات المعالجة: <b>{report['apps']}</b>",
        f"📂 أقسام داخلية جديدة: <b>{report['sections_created']}</b>",
        f"📦 منتجات جديدة منشورة: <b>{report['products_created']}</b>",
        f"🔄 منتجات أُعيد ترتيبها: <b>{report['reordered']}</b>",
        f"♻️ منتجات أُعيد تفعيلها: <b>{report['products_reactivated']}</b>",
        f"⏸ منتجات تلقائية خارجة عن أول 5 عُطّلت: <b>{report['products_deactivated']}</b>",
        f"⏭ خدمات منشورة مسبقاً (لم تتكرر): <b>{report['skipped_existing']}</b>",
    ]
    if report["errors"]:
        lines.append(f"\n⚠️ أخطاء جزئية: <b>{report['errors']}</b> (راجع السجل)")
    lines.append(
        "\nالسعر = تكلفة المزود + هامش الربح المحدد، والترتيب من الأرخص للأغلى.\n"
        "إعادة الضغط لا تكرر المنتجات ولا تمس منتجاتك اليدوية."
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=_build_report_kb())


@router.callback_query(F.data == "ps:subsync")
async def pulled_subscriptions_sync(callback: CallbackQuery, session, state: FSMContext):
    """🛍 مزامنة فورية: منتجات الاشتراكات الرقمية (ggsoma) بسعر + هامش الربح."""
    from services.subscriptions_sync_service import SubscriptionsSyncService

    await state.clear()
    await callback.answer("🛍 جارٍ سحب كتالوج ggsoma ونشره...")
    reports = await SubscriptionsSyncService.sync_all(session)
    if not reports:
        await callback.message.edit_text(
            "🛍 <b>مزامنة الاشتراكات الرقمية</b>\n\n"
            "⚠️ لا يوجد مزود ggsoma مربوط بعد.\n\n"
            "أضفه من «🔌 مزودو المتجر» → مزود جديد → قالب "
            "«✨ ggsoma — اشتراكات رقمية»، ثم املأ الرابط "
            "https://ggsoma.store/api/partner/v1 والمفتاح (Bearer).",
            reply_markup=_build_report_kb(),
        )
        return

    lines = ["🛍 <b>نتيجة مزامنة الاشتراكات الرقمية</b>\n"]
    for report in reports:
        lines.append(
            f"🔌 <b>{report.get('provider_name', report.get('provider_id'))}</b>"
        )
        if report.get("errors"):
            lines.append(f"⚠️ فشل جلب/نشر الكتالوج (أخطاء: {report['errors']})")
            lines.append("")
            continue
        lines.append(f"📱 تطبيقات/علامات: <b>{report.get('apps', 0)}</b>")
        lines.append(f"📂 أقسام جديدة: <b>{report.get('sections_created', 0)}</b>")
        lines.append(f"📦 منتجات جديدة: <b>{report.get('products_created', 0)}</b>")
        lines.append(
            f"♻️ أُعيد تفعيلها: <b>{report.get('products_reactivated', 0)}</b> | "
            f"⏸ عُطّلت (نفد مخزونها): <b>{report.get('products_deactivated', 0)}</b>"
        )
        lines.append(
            f"💱 أُعيد تسعيرها بالهامش: <b>{report.get('products_repriced', 0)}</b> | "
            f"⏭ منتجات يدوية لم تُمس: <b>{report.get('skipped_manual', 0)}</b>"
        )
        lines.append("")
    lines.append(
        "السعر = تكلفتك عند المزود + هامش الربح المحدد في «مركز الإضافات» "
        "(إضافة الاشتراكات). إعادة الضغط لا تكرر المنتجات ولا تحذف شيئاً."
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=_build_report_kb())


@router.callback_query(F.data == "ps:storesync")
async def pulled_storesync_pickers(callback: CallbackQuery, session):
    """اختيار المتجر الذي سيتم مزامنته كاملة إلى قسم باسمه."""
    from sqlalchemy import func

    from database.models import ApiProvider, ProviderService

    await callback.answer()
    result = await session.execute(
        select(ApiProvider, func.count(ProviderService.id))
        .outerjoin(
            ProviderService,
            ProviderService.api_provider_id == ApiProvider.id,
        )
        .where(ProviderService.status == ProviderServiceStatus.ACTIVE)
        .group_by(ApiProvider.id)
        .order_by(func.count(ProviderService.id).desc())
    )
    rows = result.all()
    b = InlineKeyboardBuilder()
    for provider, count in rows:
        if not count:
            continue
        b.button(
            text=f"🏬 {provider.name} ({count} خدمة)",
            callback_data=f"ps:storesync_p:{provider.id}",
        )
    b.button(text="🔙 رجوع", callback_data="admin:pulled_services")
    if not rows or all(not count for _p, count in rows):
        await callback.message.edit_text(
            "🏬 <b>مزامنة متجر كامل</b>\n\n"
            "لا توجد خدمات مزامنة لأي مزود بعد.\n"
            "اسحب الخدمات أولاً من «🔌 مزودو المتجر» (زر مزامنة الخدمات).",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:pulled_services")]]
            ),
        )
        return
    await callback.message.edit_text(
        "🏬 <b>مزامنة متجر كامل ← قسم باسم المتجر</b>\n\n"
        "اختر المتجر (المزود) الذي تريد نشر كل خدماته:\n\n"
        "سيُنشأ قسم باسم المتجر مع كل خدماته (تكلفة + الهامش العالمي) "
        "بالعربية ومن الأرخص للأغلى.\n\n"
        "بعدها تملك السيطرة الكاملة: أوقف ما لا تريد بيعه، عدّل هامش أي "
        "منتج/قسم، أو انشر أي خدمة بسعرك في أي قسم آخر.",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data.startswith("ps:storesync_p:"))
async def pulled_storesync_run(callback: CallbackQuery, session):
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return
    await callback.answer("⏳ جارٍ مزامنة المتجر...")
    try:
        report = await PulledServicesService.sync_store_to_section(session, provider)
    except Exception:
        logger.exception("فشل مزامنة متجر %s", provider.id)
        await callback.message.edit_text(
            f"❌ <b>فشل مزامنة متجر {provider.name}</b>\nراجع السجل.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:pulled_services")]]
            ),
        )
        return
    category: Category = report["category"]
    section: SubCategory = report["section"]
    await callback.message.edit_text(
        "🏬 <b>تمت مزامنة المتجر</b>\n\n"
        f"🔌 المتجر: <b>{provider.name}</b>\n"
        f"📂 القسم: <code>cat:{category.id}</code> — {category.emoji} {category.name_ar}\n"
        f"🗂 القسم الفرعي: <code>subcat:{section.id}</code>\n\n"
        f"🆕 منتجات جديدة: <b>{report['created']}</b>\n"
        f"♻️ أعيد تفعيلها: <b>{report['reactivated']}</b>\n"
        f"🔀 أعيد ترتيبها: <b>{report['reordered']}</b>\n"
        f"⏭ منتجات يدوية لم تُمس: <b>{report['skipped_manual']}</b>\n\n"
        "الأسعار = تكلفة المزود + الهامش العالمي حالياً.\n"
        "لضبط هامش القسم/المنتج: «📂 إدارة الأقسام»، ولإيقاف أي منتج: "
        "«📦 إدارة المنتجات».",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📂 فتح القسم", callback_data=f"cat:{category.id}")],
                [InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:pulled_services")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("ps:pl:"))
async def pulled_platform(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    platform_key = callback.data.split(":", 2)[2]
    rows = await PulledServicesService.kind_counts(session, platform_key)
    emoji, label = platform_meta(platform_key)
    await callback.answer()
    if not rows:
        await callback.message.edit_text(
            f"{emoji} <b>{label}</b>\n\nلا توجد خدمات تحت هذه المنصة.",
            reply_markup=_kinds_kb(platform_key, []),
        )
        return
    await callback.message.edit_text(
        f"{emoji} <b>{label}</b>\n\nاختر نوع الخدمة:",
        reply_markup=_kinds_kb(platform_key, rows),
    )


@router.callback_query(F.data.startswith("ps:kd:"))
async def pulled_kind(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    parts = callback.data.split(":")
    # ps:kd:{platform}:{kind}:{page}
    if len(parts) < 5:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    platform_key, kind_key, page_raw = parts[2], parts[3], parts[4]
    try:
        page = int(page_raw)
    except ValueError:
        page = 0
    services, total = await PulledServicesService.list_services(
        session, platform_key, kind_key, page=page
    )
    p_emoji, p_label = platform_meta(platform_key)
    k_emoji, k_label = kind_meta(kind_key)
    await callback.answer()
    last_page = max(0, (total - 1) // SERVICES_PER_PAGE)
    await callback.message.edit_text(
        f"{p_emoji} {p_label} → {k_emoji} {k_label}\n"
        f"📊 {total} خدمة · مرتبة من الأرخص للأغلى\n"
        f"📄 صفحة {page + 1}/{last_page + 1}\n\n"
        "السعر الظاهر هو تكلفة المزود لكل 1000.",
        reply_markup=_services_kb(platform_key, kind_key, services, page, total),
    )


@router.callback_query(F.data.startswith("ps:sv:"))
async def pulled_service_view(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    try:
        service_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    service = await session.get(ProviderService, service_id)
    if service is None:
        await callback.answer("الخدمة غير موجودة", show_alert=True)
        return
    platform_key, kind_key = PulledServicesService.classify(service)
    provider = await session.get(ApiProvider, service.api_provider_id)
    provider_name = provider.name if provider is not None else f"#{service.api_provider_id}"
    p_emoji, p_label = platform_meta(platform_key)
    k_emoji, k_label = kind_meta(kind_key)

    from services.service_localization_service import display_service_name, is_arabic

    display_name = display_service_name(
        service.name, service.category, service.service_type
    )
    name_lines = f"الاسم: {display_name}\n"
    if not is_arabic(service.name or "") and display_name != (service.name or ""):
        name_lines += f"<i>أصلي: {service.name}</i>\n"

    await callback.answer()
    await callback.message.edit_text(
        "📦 <b>خدمة مسحوبة</b>\n\n"
        f"{name_lines}"
        f"المنصة: {p_emoji} {p_label}\n"
        f"النوع: {k_emoji} {k_label}\n"
        f"🔌 المزود: {provider_name}\n"
        f"🆔 آيدي الخدمة: <code>{service.external_service_id}</code>\n"
        f"💰 تكلفة المزود: <b>{service.rate_usd}$</b> / 1000\n"
        f"📊 الكمية: {service.min_quantity} — {service.max_quantity}\n"
        f"📂 التصنيف: {service.category or '—'}\n\n"
        "لن تظهر في البوت حتى تنشرها داخل قسم (والأفضل داخل قسم داخلي "
        "لتطبيقها) وتضع سعر البيع (لكل 1000).",
        reply_markup=_service_detail_kb(service.id, platform_key, kind_key, 0),
    )


@router.callback_query(F.data.startswith("ps:pb:"))
async def pulled_publish_start(callback: CallbackQuery, session, state: FSMContext):
    try:
        service_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    service = await session.get(ProviderService, service_id)
    if service is None:
        await callback.answer("الخدمة غير موجودة", show_alert=True)
        return
    platform_key, kind_key = PulledServicesService.classify(service)

    from services.service_localization_service import display_service_name

    svc_display = display_service_name(
        service.name, service.category, service.service_type
    )
    await state.clear()
    await callback.answer()

    # خدمة SMM لمنصة معروفة؟ → نرشد الأدمن إلى الأقسام الداخلية للتطبيق
    # (متابعون/لايكات/...) مباشرة بدل البحث في كل الأقسام.
    from services.feature_service import FeatureService

    if await FeatureService.enabled("smm_inner_sections", default=True):
        app_sub = await PulledServicesService.platform_app_subcategory(session, platform_key)
        if app_sub is not None:
            sections = await PulledServicesService.app_section_destinations(session, app_sub)
            p_emoji, p_label = platform_meta(platform_key)
            k_emoji, k_label = kind_meta(kind_key)
            if sections:
                await callback.message.edit_text(
                    f"{p_emoji} <b>{p_label}</b> ← {k_emoji} {k_label}\n\n"
                    "📂 <b>اختر القسم الداخلي الذي سيظهر فيه المنتج</b>\n"
                    "(الرقم بين قوسين = عدد المنتجات الظاهرة حالياً)\n\n"
                    f"الخدمة: {svc_display}",
                    reply_markup=_sections_dest_kb(service_id, sections, platform_key, kind_key, 0),
                )
                return
            await callback.message.edit_text(
                f"{p_emoji} <b>{p_label}</b> ← {k_emoji} {k_label}\n\n"
                "⚠️ <b>لا توجد أقسام داخلية بعد</b> في هذا التطبيق.\n"
                "ننصح بإنشائها تلقائياً: سيُنشئ البوت قسماً لهذا النوع "
                f"(«{k_label}») وينشر أرخص 5 خدمات فيه، ثم تعود وتنشر هذه الخدمة "
                "بسعرك الخاص داخل القسم.\n\n"
                f"الخدمة: {service.name}",
                reply_markup=_no_sections_dest_kb(service_id),
            )
            return
    await _show_subs(callback, session, state, service_id, 0)


@router.callback_query(F.data.startswith("ps:subs:"))
async def pulled_subs_page(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    service_id = int(parts[2])
    page = int(parts[3])
    await _show_subs(callback, session, state, service_id, page)


@router.callback_query(F.data.startswith("ps:ss:"))
async def pulled_subs_legacy_page(callback: CallbackQuery, session, state: FSMContext):
    """توافق مع الأزرار القديمة التي تستخدم ps:ss للترقيم."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    service_id = int(parts[2])
    page = int(parts[3])
    await _show_subs(callback, session, state, service_id, page)


async def _show_subs(callback, session, state, service_id: int, page: int) -> None:
    service = await session.get(ProviderService, service_id)
    if service is None:
        await callback.answer("الخدمة غير موجودة", show_alert=True)
        return
    subs = await PulledServicesService.destination_subcategories(session)
    await state.clear()
    await callback.answer()
    if not subs:
        await callback.message.edit_text(
            "⚠️ لا توجد أقسام فرعية.\nأنشئ قسماً من «إدارة الأقسام» أولاً ثم عد إلى هنا.",
            reply_markup=_service_detail_kb(service_id, *PulledServicesService.classify(service), 0),
        )
        return
    await callback.message.edit_text(
        "📂 <b>اختر القسم الذي سيظهر فيه المنتج</b>\n\n"
        f"الخدمة: {service.name}\n"
        "بعد الاختيار سيُطلب منك سعر البيع لكل 1000.",
        reply_markup=_subs_kb(service_id, subs, page),
    )


@router.callback_query(F.data.startswith("ps:sc:"))
async def pulled_sub_picked(callback: CallbackQuery, session, state: FSMContext):
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    service_id = int(parts[2])
    sub_id = int(parts[3])
    service = await session.get(ProviderService, service_id)
    if service is None:
        await callback.answer("الخدمة غير موجودة", show_alert=True)
        return
    await state.update_data(ps_service_id=service_id, ps_sub_id=sub_id)
    await state.set_state(AdminPulledServicesStates.waiting_sell_price)
    await callback.answer()
    from services.service_localization_service import display_service_name

    svc_display = display_service_name(
        service.name, service.category, service.service_type
    )
    await callback.message.edit_text(
        "💰 <b>سعر البيع لكل 1000</b>\n\n"
        f"الخدمة: {svc_display}\n"
        f"تكلفة المزود: {service.rate_usd}$ / 1000\n\n"
        "أرسل سعر البيع بالدولار <b>لكل 1000</b> (مثال: 1.50).\n"
        "هذا السعر هو الذي يراه المستخدم، وليس لكل 100."
    )


@router.message(AdminPulledServicesStates.waiting_sell_price)
async def pulled_price_received(message: Message, session, state: FSMContext):
    data = await state.get_data()
    service_id = data.get("ps_service_id")
    sub_id = data.get("ps_sub_id")
    try:
        sell_price = PulledServicesService.parse_sell_price(message.text or "")
    except ValueError as exc:
        await message.answer(str(exc))
        return
    service = await session.get(ProviderService, service_id) if service_id else None
    if service is None:
        await state.clear()
        await message.answer("⚠️ الخدمة لم تعد موجودة.")
        return
    try:
        product = await PulledServicesService.publish(
            session, service, int(sub_id), sell_price
        )
    except Exception:
        logger.exception("Failed to publish pulled service %s", service_id)
        await message.answer("❌ تعذر إنشاء المنتج. راجع السجل.")
        return
    await state.clear()
    await message.answer(
        "✅ <b>تم نشر المنتج في البوت</b>\n\n"
        f"📦 {product.name_ar}\n"
        f"💰 سعر البيع: {product.price_usd}$ / 1000\n"
        f"💵 التكلفة: {product.cost_price_usd}$ / 1000\n"
        f"🆔 المنتج: <code>{product.id}</code>\n\n"
        "يمكنك تعديل الاسم أو السعر لاحقاً من إدارة المنتجات."
    )
