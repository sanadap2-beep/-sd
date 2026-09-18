"""Admin catalog of pulled SMM services.

Pulled provider services stay hidden from the storefront. The admin browses
platform → type → cheapest-to-expensive, picks a destination section, sets a
sell price (per 1000), and only then is a product created.
"""

from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from database.models import (
    ApiProvider,
    Category,
    Product,
    ProductFulfillmentType,
    ProviderService,
    ProviderServiceStatus,
    SubCategory,
)
from filters.admin_filter import IsAdmin
from services.pulled_services_service import (
    SEARCH_PER_PAGE,
    SERVICES_PER_PAGE,
    PulledServicesService,
)
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
        text="🔌 إدارة كل مزود لحاله (مسح / إعادة سحب / بحث)",
        callback_data="ps:providers",
        style="primary",
    )
    b.button(
        text="🚀 إنشاء أقسام الرشق تلقائياً (أرخص 10 لكل نوع)",
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
    b.button(
        text="🔎 ابحث عن خدمة محددة بالاسم/الآيدي",
        callback_data="ps:search",
        style="primary",
    )
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    layout = [2] * (len(rows) // 2)
    if len(rows) % 2:
        layout.append(1)
    # منصات + إدارة حسب المزود + بناء تلقائي + مزامنة اشتراكات + مزامنة متجر + بحث + رجوع
    layout.extend([1, 1, 1, 1, 1, 1])
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
    from services.service_localization_service import service_name_ar

    b = InlineKeyboardBuilder()
    for service in services:
        rate = service.rate_usd or 0
        name = service_name_ar(service)[:36]
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
        text="🚀 أنشئ أقسام الرشق تلقائياً (أرخص 10 لكل نوع)",
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


def _search_results_kb(services, page: int, total: int, provider_id: int | None = None):
    """نتائج البحث: الخدمة تفتح تفاصيلها مباشرة (نشر/سعر).

    عند تمرير ``provider_id`` يبقى البحث داخل كتالوج ذلك المزود وتُضاف
    أزرار رجوع إلى شاشة المزود نفسه.
    """
    from services.service_localization_service import service_name_ar

    b = InlineKeyboardBuilder()
    for service in services:
        rate = service.rate_usd or 0
        name = service_name_ar(service)[:36]
        b.button(text=f"{rate}$ · {name}", callback_data=f"ps:sv:{service.id}")
    nav = []
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"ps:sr:{page - 1}")
        nav.append(1)
    last_page = max(0, (total - 1) // SEARCH_PER_PAGE)
    if page < last_page:
        b.button(text="التالي ▶️", callback_data=f"ps:sr:{page + 1}")
        nav.append(1)
    if provider_id:
        b.button(text="🔎 بحث جديد في المزود", callback_data=f"ps:psr:{provider_id}", style="primary")
        b.button(text="🔙 شاشة المزود", callback_data=f"ps:prov:{provider_id}")
    else:
        b.button(text="🔎 بحث جديد", callback_data="ps:search", style="primary")
        b.button(text="🔙 الخدمات المسحوبة", callback_data="admin:pulled_services")
    rows = [1] * len(services)
    if nav:
        rows.append(len(nav))
    rows.extend([1, 1])
    b.adjust(*rows)
    return b.as_markup()


def _search_intro_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:pulled_services")]
        ]
    )


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
        "🔌 <b>إدارة حسب المزود:</b> لكل مزود كتالوجه وحده — ابحث في "
        "خدماته، امسح كل منتجاته من المتجر، ثم أعد سحبها/نشرها بدون تشتّت.\n\n"
        "اختر المنصة ثم النوع (لايكات / مشاهدات / متابعون…).\n"
        "الترتيب داخل كل نوع: الأرخص ← الأغلى.\n\n"
        "🚀 <b>البناء التلقائي</b> ينشئ لكل تطبيق أقسامه الداخلية "
        "(متابعون/لايكات/مشاهدات...) وينشر أرخص 10 خدمات في كل نوع، "
        "وتستطيع بعده نشر أي خدمة يدوياً بسعرك الخاص داخل قسمها.\\n\\n"
        "🔎 <b>تبحث عن خدمة بعينها؟</b> استخدم زر البحث واكتب اسمها "
        "أو آيديها عند المزود بدل التصفّح.",
        reply_markup=_platforms_kb(rows),
    )


# ══════════════════════════════════════════════════════════════
# ═══ إدارة الخدمات / المنتجات حسب المزود (لكل مزود وحده) ═══
# ══════════════════════════════════════════════════════════════


def _providers_kb(rows: list[tuple[ApiProvider, int, int, bool]]) -> InlineKeyboardMarkup:
    """``(provider, services_count, products_count, active)``"""
    b = InlineKeyboardBuilder()
    for provider, svc_count, prod_count, active in rows:
        status = "🟢" if active else "🔴"
        b.button(
            text=f"{status} {provider.name} — {svc_count} خدمة · {prod_count} منتج",
            callback_data=f"ps:prov:{provider.id}",
        )
    b.button(text="🔙 الخدمات المسحوبة", callback_data="admin:pulled_services")
    b.adjust(1)
    return b.as_markup()


def _provider_detail_kb(provider_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🎮 خدمات الألعاب لحال", callback_data=f"ps:pg:{provider_id}:games")
    b.button(text="📦 باقي الخدمات لحال", callback_data=f"ps:pg:{provider_id}:other")
    b.button(text="🔎 بحث في خدمات هذا المزود", callback_data=f"ps:psr:{provider_id}")
    b.button(text="📃 كل خدمات المزود (الأرخص ← الأغلى)", callback_data=f"ps:plist:{provider_id}:0")
    b.button(
        text="🚀 نشر أرخص 10 في كل نوع (قسم الرشق)",
        callback_data=f"ps:pbuild:{provider_id}",
        style="primary",
    )
    b.button(
        text="🏬 مزامنة كاملة كقسم باسم المزود",
        callback_data=f"ps:psync:{provider_id}",
    )
    b.button(
        text="🗑 مسح كل منتجات المزود من المتجر",
        callback_data=f"ps:pdelc:{provider_id}",
        style="danger",
    )
    b.button(text="🔙 كل المزودين", callback_data="ps:providers")
    b.adjust(1)
    return b.as_markup()


def _provider_services_kb(
    services: list[ProviderService], provider_id: int, page: int, total: int
) -> InlineKeyboardMarkup:
    from services.service_localization_service import service_name_ar

    b = InlineKeyboardBuilder()
    for service in services:
        rate = service.rate_usd or 0
        name = service_name_ar(service)[:36]
        b.button(text=f"{rate}$ · {name}", callback_data=f"ps:sv:{service.id}")
    last_page = max(0, (total - 1) // SERVICES_PER_PAGE)
    nav = []
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"ps:plist:{provider_id}:{page - 1}")
        nav.append(1)
    if page < last_page:
        b.button(text="التالي ▶️", callback_data=f"ps:plist:{provider_id}:{page + 1}")
        nav.append(1)
    b.button(text="🧹 إعادة توجيه للبناء التلقائي", callback_data=f"ps:pbuild:{provider_id}")
    b.button(text="🔙 تفاصيل المزود", callback_data=f"ps:prov:{provider_id}")
    rows = [1] * len(services)
    if nav:
        rows.append(len(nav))
    rows.extend([1, 1])
    b.adjust(*rows)
    return b.as_markup()


def _provider_delete_confirm_kb(provider_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="❌ نعم، احذف كل منتجات هذا المزود",
        callback_data=f"ps:pdel:{provider_id}",
        style="danger",
    )
    b.button(text="🔙 إلغاء", callback_data=f"ps:prov:{provider_id}")
    b.adjust(1)
    return b.as_markup()


def _provider_report_kb(provider_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🚀 إعادة النشر التلقائي", callback_data=f"ps:pbuild:{provider_id}")
    b.button(text="🔙 خدمة المزود", callback_data=f"ps:prov:{provider_id}")
    b.adjust(1)
    return b.as_markup()


@router.callback_query(F.data == "ps:providers")
async def pulled_providers_home(callback: CallbackQuery, session, state: FSMContext):
    """قائمة المزودين مع عدد خدماتهم ومنتجاتهم — بوابة إدارة كل مزود لحاله."""
    await state.clear()
    await callback.answer()
    providers = list(
        (await session.execute(select(ApiProvider).order_by(ApiProvider.id))).scalars().all()
    )
    rows: list[tuple[ApiProvider, int, int, bool]] = []
    for provider in providers:
        svc_count = (
            await session.execute(
                select(func.count(ProviderService.id)).where(
                    ProviderService.api_provider_id == provider.id,
                    ProviderService.status == ProviderServiceStatus.ACTIVE,
                )
            )
        ).scalar_one()
        prod_count = (
            await session.execute(
                select(func.count(Product.id)).where(Product.api_provider_id == provider.id)
            )
        ).scalar_one()
        rows.append((provider, int(svc_count), int(prod_count), bool(provider.is_active)))

    if not rows:
        await callback.message.edit_text(
            "🔌 <b>إدارة حسب المزود</b>\n\n"
            "لا يوجد مزودون بعد.\nأضف مزوداً من «🔌 مزودو المتجر» أولاً ثم اسحب خدماته.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔙 رجوع", callback_data="admin:pulled_services")]]
            ),
        )
        return

    lines = ["🔌 <b>الخدمات المسحوبة حسب المزود</b>\n", "لكل مزود كتالوج وحده:"]
    for provider, svc_count, prod_count, _active in rows:
        lines.append(f"• {provider.name} — {svc_count} خدمة / {prod_count} منتج")
    lines.append(
        "\nمن شاشة المزود تستطيع: البحث في خدماته، رؤية كتالوجه كاملاً، "
        "نشر أرخص 10 بكل نوع، مزامنته كقسم، أو مسح كل منتجاته ثم إعادة سحبها."
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=_providers_kb(rows))


@router.callback_query(F.data.startswith("ps:prov:"))
async def pulled_provider_detail(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return
    svc_count = (
        await session.execute(
            select(func.count(ProviderService.id)).where(
                ProviderService.api_provider_id == provider.id,
                ProviderService.status == ProviderServiceStatus.ACTIVE,
            )
        )
    ).scalar_one()
    prod_count = (
        await session.execute(
            select(func.count(Product.id)).where(Product.api_provider_id == provider.id)
        )
    ).scalar_one()
    active = "🟢 مفعّل" if provider.is_active else "🔴 معطّل"
    await callback.answer()
    try:
        groups = await PulledServicesService.provider_group_counts(session, provider.id)
        group_line = f"🎮 الألعاب: <b>{groups['games']}</b> · 📦 الباقي: <b>{groups['other']}</b>\n"
    except Exception:
        group_line = ""
    await callback.message.edit_text(
        f"🔌 <b>{provider.name}</b>\n\n"
        f"الحالة: {active}\n"
        f"🆔 ID: <code>{provider.id}</code>\n"
        f"📥 الخدمات المسحوبة: <b>{svc_count}</b>\n"
        f"{group_line}"
        f"📦 المنتجات المنشورة: <b>{prod_count}</b>\n"
        f"🧩 البروتوكول: {provider.protocol_type.value if provider.protocol_type else '—'}\n"
        f"🏷 النوع: {provider.type.value if provider.type else '—'}\n\n"
        "اختر «🎮 خدمات الألعاب» أو «📦 باقي الخدمات»، تصفح تصنيفاتها، "
        "ثم انشر ما تريد في قسمك بهامش ربح — الأسماء بالعربية تلقائياً.",
        reply_markup=_provider_detail_kb(provider.id),
    )


@router.callback_query(F.data.startswith("ps:plist:"))
async def pulled_provider_services_list(callback: CallbackQuery, session, state: FSMContext):
    """كل خدمات مزود واحد مرتبة من الأرخص للأغلى، مع ترقيم الصفحات."""
    await state.clear()
    parts = callback.data.split(":")
    try:
        provider_id = int(parts[2])
        page = max(0, int(parts[3]))
    except (IndexError, ValueError):
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    provider = await session.get(ApiProvider, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return
    services, total = await PulledServicesService.list_active_by_provider(
        session, provider_id, page=page
    )
    last_page = max(0, (total - 1) // SERVICES_PER_PAGE)
    await callback.answer()
    await callback.message.edit_text(
        f"🔌 <b>{provider.name}</b> · الخدمات المسحوبة\n"
        f"📊 {total} خدمة · من الأرخص للأغلى\n"
        f"📄 صفحة {page + 1}/{last_page + 1}",
        reply_markup=_provider_services_kb(services, provider_id, page, total),
    )


# ─────────── فرز المزود: ألعاب لحال / باقي لحال + نشر جماعي ───────────
# أسماء التصنيفات عربية وطويلة ولا تتسع في callback_data (حد 64 بايت)،
# لذلك نخزن قائمة التصنيفات لكل أدمن ونمرر فهرسها فقط.
_GROUP_CAT_CACHE: dict[tuple[int, int, str], tuple[float, list[str]]] = {}
_GROUP_CAT_TTL = 1800.0


def _group_cat_set(user_id: int, provider_id: int, group: str, cats: list[str]) -> None:
    import time

    now = time.time()
    for key, (stamp, _cats) in list(_GROUP_CAT_CACHE.items()):
        if now - stamp > _GROUP_CAT_TTL:
            _GROUP_CAT_CACHE.pop(key, None)
    _GROUP_CAT_CACHE[(user_id, provider_id, group)] = (now, list(cats))


def _group_cat_get(user_id: int, provider_id: int, group: str) -> list[str] | None:
    import time

    cached = _GROUP_CAT_CACHE.get((user_id, provider_id, group))
    if cached is None:
        return None
    stamp, cats = cached
    if time.time() - stamp > _GROUP_CAT_TTL:
        _GROUP_CAT_CACHE.pop((user_id, provider_id, group), None)
        return None
    return cats


def _group_title(group: str) -> str:
    return "🎮 خدمات الألعاب" if group == "games" else "📦 باقي الخدمات"


def _group_cats_kb(
    provider_id: int, group: str, cats: list[tuple[str, int]], total: int
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text=f"⭐ كل {_group_title(group)} ({total})",
        callback_data=f"ps:pgc:{provider_id}:{group}:-1:0",
    )
    for idx, (label, count) in enumerate(cats[:30]):
        short = label[:32]
        b.button(
            text=f"{short} ({count})",
            callback_data=f"ps:pgc:{provider_id}:{group}:{idx}:0",
        )
    b.button(text="🔙 تفاصيل المزود", callback_data=f"ps:prov:{provider_id}")
    b.adjust(1)
    return b.as_markup()


def _group_services_kb(
    provider_id: int,
    group: str,
    cat_idx: int,
    services,
    page: int,
    total: int,
) -> InlineKeyboardMarkup:
    from services.service_localization_service import service_name_ar

    b = InlineKeyboardBuilder()
    for service in services:
        rate = service.rate_usd or 0
        name = service_name_ar(service)[:36]
        b.button(text=f"{rate}$ · {name}", callback_data=f"ps:sv:{service.id}")
    last_page = max(0, (total - 1) // SERVICES_PER_PAGE)
    nav = []
    if page > 0:
        b.button(
            text="◀️ السابق",
            callback_data=f"ps:pgc:{provider_id}:{group}:{cat_idx}:{page - 1}",
        )
        nav.append(1)
    if page < last_page:
        b.button(
            text="التالي ▶️",
            callback_data=f"ps:pgc:{provider_id}:{group}:{cat_idx}:{page + 1}",
        )
        nav.append(1)
    b.button(
        text="📤 نشر الكل في قسم بهامش ربح %",
        callback_data=f"ps:pbulk:{provider_id}:{group}:{cat_idx}",
    )
    b.button(text="🔙 التصنيفات", callback_data=f"ps:pg:{provider_id}:{group}")
    rows = [1] * len(services)
    if nav:
        rows.append(len(nav))
    rows.extend([1, 1])
    b.adjust(*rows)
    return b.as_markup()


@router.callback_query(F.data.startswith("ps:pg:"))
async def pulled_provider_group(callback: CallbackQuery, session, state: FSMContext):
    """شاشة الفرز: الألعاب لحال والباقي لحال مع تصنيفات وعدد."""
    await state.clear()
    parts = callback.data.split(":")
    try:
        provider_id = int(parts[2])
        group = parts[3]
    except (IndexError, ValueError):
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    if group not in ("games", "other"):
        await callback.answer("مجموعة غير معروفة", show_alert=True)
        return
    provider = await session.get(ApiProvider, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return
    counts = await PulledServicesService.provider_group_counts(session, provider_id)
    cats = await PulledServicesService.provider_group_categories(session, provider_id, group)
    _group_cat_set(callback.from_user.id, provider_id, group, [c for c, _n in cats])
    await callback.answer()
    await callback.message.edit_text(
        f"🔌 <b>{provider.name}</b> · {_group_title(group)}\n\n"
        f"🎮 الألعاب: <b>{counts['games']}</b> · 📦 الباقي: <b>{counts['other']}</b>\n"
        f"هذه المجموعة: <b>{counts.get(group, 0)}</b> خدمة مخفية عن المتجر.\n\n"
        "اختر تصنيفاً لتصفح خدماته من الأرخص للأغلى، أو اختر «كل المجموعة».\n"
        "النشر لا يتم هنا — تختار الخدمات ثم قسمك ثم هامش الربح، "
        "والأسماء تُنشر بالعربية تلقائياً.",
        reply_markup=_group_cats_kb(provider_id, group, cats, counts.get(group, 0)),
    )


@router.callback_query(F.data.startswith("ps:pgc:"))
async def pulled_provider_group_category(
    callback: CallbackQuery, session, state: FSMContext
):
    """خدمات مجموعة/تصنيف واحد مع زر النشر الجماعي."""
    await state.clear()
    parts = callback.data.split(":")
    try:
        provider_id = int(parts[2])
        group = parts[3]
        cat_idx = int(parts[4])
        page = max(0, int(parts[5]))
    except (IndexError, ValueError):
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    provider = await session.get(ApiProvider, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return
    cats = _group_cat_get(callback.from_user.id, provider_id, group)
    if cats is None:
        fresh = await PulledServicesService.provider_group_categories(
            session, provider_id, group
        )
        cats = [c for c, _n in fresh]
        _group_cat_set(callback.from_user.id, provider_id, group, cats)
    category = None if cat_idx < 0 else (cats[cat_idx] if 0 <= cat_idx < len(cats) else None)
    if cat_idx >= 0 and category is None:
        await callback.answer("التصنيف انتهت صلاحيته — أعد فتح المجموعة.", show_alert=True)
        return
    services, total = await PulledServicesService.list_group_services(
        session, provider_id, group, category, page=page
    )
    last_page = max(0, (total - 1) // SERVICES_PER_PAGE)
    await callback.answer()
    cat_label = "كل المجموعة" if category is None else category
    await callback.message.edit_text(
        f"🔌 <b>{provider.name}</b> · {_group_title(group)}\n"
        f"📂 التصنيف: <b>{cat_label}</b>\n"
        f"📊 {total} خدمة · من الأرخص للأغلى\n"
        f"📄 صفحة {page + 1}/{last_page + 1}\n\n"
        "اضغط أي خدمة لنشرها مفردة، أو استخدم زر النشر الجماعي بالأسفل.",
        reply_markup=_group_services_kb(
            provider_id, group, cat_idx, services, page, total
        ),
    )


@router.callback_query(F.data.startswith("ps:pbulk:"))
async def pulled_bulk_pick_sub(callback: CallbackQuery, session, state: FSMContext):
    """بدء النشر الجماعي: اختيار قسمك الذي ستُنشر فيه الخدمات."""
    parts = callback.data.split(":")
    try:
        provider_id = int(parts[2])
        group = parts[3]
        cat_idx = int(parts[4])
    except (IndexError, ValueError):
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    provider = await session.get(ApiProvider, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return
    cats = _group_cat_get(callback.from_user.id, provider_id, group)
    if cats is None:
        fresh = await PulledServicesService.provider_group_categories(
            session, provider_id, group
        )
        cats = [c for c, _n in fresh]
        _group_cat_set(callback.from_user.id, provider_id, group, cats)
    category = None if cat_idx < 0 else (cats[cat_idx] if 0 <= cat_idx < len(cats) else None)
    subs = await PulledServicesService.destination_subcategories(session)
    if not subs:
        await callback.answer("لا توجد أقسام — أنشئ قسماً أولاً.", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        bulk_provider_id=provider_id,
        bulk_group=group,
        bulk_category=category,
    )
    b = InlineKeyboardBuilder()
    for sub in subs[:30]:
        label = f"{sub.emoji or ''} {_sub_label(sub)}".strip()[:60]
        b.button(text=label, callback_data=f"ps:pbs:{sub.id}")
    b.button(text="🔙 رجوع", callback_data=f"ps:pg:{provider_id}:{group}")
    b.adjust(1)
    await callback.answer()
    cat_label = "كل المجموعة" if category is None else category
    await callback.message.edit_text(
        f"📂 <b>اختر قسمك</b> الذي ستُنشر فيه {_group_title(group)}\n"
        f"📂 التصنيف: <b>{cat_label}</b>\n\n"
        "المنتجات الجديدة ستظهر فيه فقط بعد النشر — الخدمات المسحوبة "
        "تبقى مخفية حتى تنشرها.",
        reply_markup=b.as_markup(),
    )


def _sub_label(sub) -> str:
    """تسمية هرمية: القسم الرئيسي / الأب / القسم (مثل: الألعاب / شحن ألعاب / ببجي)."""
    cat = getattr(sub, "category", None)
    parent = getattr(sub, "parent", None)
    parts = []
    cat_name = getattr(cat, "name_ar", None)
    if cat_name:
        parts.append(cat_name)
    parent_name = getattr(parent, "name_ar", None)
    if parent_name:
        parts.append(parent_name)
    parts.append(sub.name_ar or "")
    return " / ".join(p for p in parts if p)


@router.callback_query(F.data.startswith("ps:pbs:"))
async def pulled_bulk_pick_margin(callback: CallbackQuery, session, state: FSMContext):
    """بعد اختيار القسم: طلب هامش الربح %."""
    try:
        sub_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    data = await state.get_data()
    if not data.get("bulk_provider_id"):
        await callback.answer("انتهت الجلسة — أعد من جديد.", show_alert=True)
        return
    await state.update_data(bulk_sub_id=sub_id)
    await state.set_state(AdminPulledServicesStates.waiting_bulk_margin)
    await callback.answer()
    await callback.message.edit_text(
        "💰 <b>هامش الربح %</b>\n\n"
        "أرسل رقماً فقط (مثال: <code>30</code> يعني التكلفة + 30%).\n"
        "السعر = تكلفة المزود × (1 + الهامش/100).\n"
        "الأسماء ستُنشر بالعربية تلقائياً."
    )


@router.message(AdminPulledServicesStates.waiting_bulk_margin)
async def pulled_bulk_margin_received(message: Message, session, state: FSMContext):
    from decimal import Decimal, InvalidOperation

    from database.models import SubCategory

    data = await state.get_data()
    provider_id = data.get("bulk_provider_id")
    group = data.get("bulk_group")
    category = data.get("bulk_category")
    sub_id = data.get("bulk_sub_id")
    try:
        margin = Decimal((message.text or "").strip().replace("%", ""))
    except (InvalidOperation, ValueError, AttributeError):
        await message.answer("⚠️ أرسل رقماً صحيحاً (مثال: 30).")
        return
    if margin < -95 or margin > 1000:
        await message.answer("⚠️ الهامش يجب أن يكون بين -95 و 1000.")
        return
    provider = await session.get(ApiProvider, provider_id) if provider_id else None
    sub = await session.get(SubCategory, sub_id) if sub_id else None
    if provider is None or sub is None:
        await state.clear()
        await message.answer("⚠️ المزود أو القسم لم يعد موجوداً.")
        return
    progress = await message.answer("⏳ جارٍ النشر الجماعي... قد يستغرق دقيقة مع المئات.")
    try:
        report = await PulledServicesService.publish_group_to_subcategory(
            session, provider_id, group, sub_id, margin, category
        )
    except Exception:
        logger.exception("فشل النشر الجماعي لمزود %s", provider_id)
        await progress.edit_text("❌ فشل النشر الجماعي. راجع السجل.")
        await state.clear()
        return
    await state.clear()
    cat_label = "كل المجموعة" if not category else category
    await progress.edit_text(
        "✅ <b>تم النشر الجماعي</b>\n\n"
        f"🔌 المزود: <b>{provider.name}</b>\n"
        f"📂 التصنيف: <b>{cat_label}</b>\n"
        f"📂 قسمك: <b>{sub.name_ar}</b>\n"
        f"💰 الهامش: <b>{margin}%</b>\n\n"
        f"🆕 منتجات جديدة: <b>{report['created']}</b>\n"
        f"🔄 صُحح سعرها بالهامش الجديد: <b>{report.get('repriced', 0)}</b>\n"
        f"🚫 بلا سعر (تُجوهلت): <b>{report['skipped_unpriced']}</b>\n\n"
        "الأسماء منشورة بالعربية، ويمكنك تعديل أي سعر/هامش لاحقاً من إدارة المنتجات."
    )


@router.callback_query(F.data.startswith("ps:psr:"))
async def pulled_provider_search_start(callback: CallbackQuery, session, state: FSMContext):
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return
    own_count = (
        await session.execute(
            select(func.count(ProviderService.id)).where(
                ProviderService.api_provider_id == provider.id,
                ProviderService.status == ProviderServiceStatus.ACTIVE,
            )
        )
    ).scalar_one()
    await state.clear()
    await state.update_data(search_provider_id=provider_id)
    await state.set_state(AdminPulledServicesStates.waiting_search)
    await callback.answer()
    await callback.message.edit_text(
        f"🔎 <b>ابحث في خدمات «{provider.name}»</b>\n\n"
        f"لديك <b>{int(own_count)}</b> خدمة مسحوبة من هذا المزود فقط.\n\n"
        "اكتب الاسم أو التصنيف أو آيدي الخدمة عند المزود:"
        "\n• <code>pubg</code>\n• <code>متتبع</code>\n• <code>9002</code>"
        "\n\nالنتائج ضمن كتالوج هذا المزود فقط.",
        reply_markup=_search_intro_kb(),
    )


@router.callback_query(F.data.startswith("ps:pbuild:"))
async def pulled_provider_build(callback: CallbackQuery, session, state: FSMContext):
    """بناء/تحديث أقسام الرشق لكن من خدمات مزود واحد فقط."""
    await state.clear()
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return
    await callback.answer(f"🚀 جارٍ بناء كتالوج {provider.name}...")
    await SmmSectionsService.upgrade_legacy_limit()
    report = await SmmSectionsService.build(session, provider_id=provider_id)
    lines = [
        f"🚀 <b>تم بناء كتالوج «{provider.name}»</b>\n",
        f"📱 تطبيقات المعالجة: <b>{report['apps']}</b>",
        f"📂 أقسام داخلية جديدة: <b>{report['sections_created']}</b>",
        f"📦 منتجات جديدة منشورة: <b>{report['products_created']}</b>",
        f"🔄 أعيد ترتيبها: <b>{report['reordered']}</b>",
        f"♻️ أعيد تفعيلها: <b>{report['products_reactivated']}</b>",
        f"⏸ عُطّلت (خارج الأرخص): <b>{report['products_deactivated']}</b>",
        f"🚫 خدمات بلا سعر تُجوهلت: <b>{report['skipped_unpriced']}</b>",
    ]
    if report["errors"]:
        lines.append(f"\n⚠️ أخطاء جزئية: <b>{report['errors']}</b>")
    lines.append("\nلم يُمسّ كتالوج أي مزود آخر.")
    await callback.message.edit_text("\n".join(lines), reply_markup=_provider_report_kb(provider.id))


@router.callback_query(F.data.startswith("ps:psync:"))
async def pulled_provider_store_sync(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
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
            reply_markup=_provider_report_kb(provider.id),
        )
        return
    await callback.message.edit_text(
        f"🏬 <b>تمت مزامنة متجر «{provider.name}»</b>\n\n"
        f"🆕 منتجات جديدة: <b>{report['created']}</b>\n"
        f"♻️ أعيد تفعيلها: <b>{report['reactivated']}</b>\n"
        f"🔀 أعيد ترتيبها: <b>{report['reordered']}</b>\n"
        f"⏭ منتجات يدوية لم تُمس: <b>{report['skipped_manual']}</b>",
        reply_markup=_provider_report_kb(provider.id),
    )


@router.callback_query(F.data.startswith("ps:pdelc:"))
async def pulled_provider_delete_confirm(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return
    prod_count = (
        await session.execute(
            select(func.count(Product.id)).where(Product.api_provider_id == provider.id)
        )
    ).scalar_one()
    await callback.answer()
    await callback.message.edit_text(
        f"⚠️ <b>مسح منتجات «{provider.name}»</b>\n\n"
        f"سيُحذف <b>{prod_count}</b> منتجاً منشوراً من هذا المزود.\n"
        "لن تُحذف الخدمات المسحوبة نفسها — فقط منتجات المتجر.\n"
        "بعدها اضغط «🚀 إعادة النشر التلقائي» أو «🏬 مزامنة كاملة».",
        reply_markup=_provider_delete_confirm_kb(provider.id),
    )


@router.callback_query(F.data.startswith("ps:pdel:"))
async def pulled_provider_delete_products(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    provider_id = int(callback.data.split(":")[2])
    provider = await session.get(ApiProvider, provider_id)
    if provider is None:
        await callback.answer("المزود غير موجود.", show_alert=True)
        return
    products = list(
        (
            await session.execute(
                select(Product).where(
                    Product.api_provider_id == provider.id,
                    Product.fulfillment_type == ProductFulfillmentType.API,
                )
            )
        ).scalars().all()
    )
    deleted = 0
    errors = 0
    for product in products:
        try:
            await session.delete(product)
            deleted += 1
        except Exception:
            logger.exception("فشل حذف منتج %s من مزود %s", product.id, provider.name)
            errors += 1
    if deleted:
        try:
            await session.commit()
        except Exception:
            logger.exception("فشل حفظ حذف منتجات مزود %s", provider.name)
            await session.rollback()
            await callback.answer()
            await callback.message.edit_text(
                f"❌ <b>لم يُكمل حذف منتجات «{provider.name}»</b>\n"
                "راجع السجل — غالباً بسبب طلبات مرتبطة بالمنتج.",
                reply_markup=_provider_report_kb(provider.id),
            )
            return
    await callback.answer()
    await callback.message.edit_text(
        f"🗑 <b>تم مسح منتجات «{provider.name}»</b>\n\n"
        f"✅ حُذف: <b>{deleted}</b>\n"
        + (f"⚠️ فشل حذف: <b>{errors}</b> (راجع السجل)\n" if errors else "")
        + "\nالخدمات المسحوبة ما زالت موجودة — أعد سحبها متى شئت.",
        reply_markup=_provider_report_kb(provider.id),
    )


@router.callback_query(F.data == "admin:pulled_services")
async def pulled_home(callback: CallbackQuery, session, state: FSMContext):
    await state.clear()
    await callback.answer()
    await _show_platforms(callback, session)


# نتائج البحث تُحفظ مؤقتاً لكل أدمن حتى يستطيع التنقّل بين صفحاتها.
# (لا نضع الاستعلام داخل callback_data لأن الحد 64 بايت والنص عربي متعدد البايت.)
_SEARCH_CACHE: dict[int, tuple[float, str, list[int], int | None]] = {}
_SEARCH_CACHE_TTL = 1800.0
_SEARCH_CACHE_MAX = 200


def _store_search(
    user_id: int, query: str, service_ids: list[int], provider_id: int | None = None
) -> None:
    import time

    now = time.time()
    for key, (stamp, _q, _ids, _p) in list(_SEARCH_CACHE.items()):
        if now - stamp > _SEARCH_CACHE_TTL:
            _SEARCH_CACHE.pop(key, None)
    if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX:
        oldest = min(_SEARCH_CACHE, key=lambda key: _SEARCH_CACHE[key][0])
        _SEARCH_CACHE.pop(oldest, None)
    _SEARCH_CACHE[user_id] = (now, query, service_ids, provider_id)


def _get_search(user_id: int) -> tuple[str, list[int], int | None] | None:
    import time

    cached = _SEARCH_CACHE.get(user_id)
    if cached is None:
        return None
    stamp, query, service_ids, provider_id = cached
    if time.time() - stamp > _SEARCH_CACHE_TTL:
        _SEARCH_CACHE.pop(user_id, None)
        return None
    return query, service_ids, provider_id


@router.callback_query(F.data == "ps:search")
async def pulled_search_start(callback: CallbackQuery, session, state: FSMContext):
    """🔎 بحث مباشر عن خدمة محددة بالاسم/التصنيف/الآيدي."""
    await state.clear()
    total_count = (
        await session.execute(
            select(func.count(ProviderService.id)).where(
                ProviderService.status == ProviderServiceStatus.ACTIVE
            )
        )
    ).scalar_one()
    await state.set_state(AdminPulledServicesStates.waiting_search)
    await callback.message.edit_text(
        "🔎 <b>ابحث عن خدمة محددة</b>\\n\\n"
        f"لديك <b>{int(total_count)}</b> خدمة مسحوبة مخفية عن المتجر.\\n\\n"
        "اكتب ما تبحث عنه — الاسم أو التصنيف أو آيدي الخدمة عند المزود:\\n"
        "• <code>pubg</code>\\n"
        "• <code>تيك توك متابعين</code> (أكثر من كلمة: كلها يجب أن توجد)\\n"
        "• <code>9002</code> (آيدي الخدمة عند المزود)\\n\\n"
        "النتائج مرتبة من الأرخص للأغلى، وبالضغط على أي نتيجة تفتح صفحتها "
        "لتحدد القسم وسعر البيع.",
        reply_markup=_search_intro_kb(),
    )
    await callback.answer()


@router.message(AdminPulledServicesStates.waiting_search)
async def pulled_search_received(message: Message, session, state: FSMContext):
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("⚠️ اكتب كلمة أو رقمين على الأقل للبحث.")
        return

    state_data = await state.get_data()
    provider_id = state_data.get("search_provider_id")
    services, total = await PulledServicesService.search_services(
        session, query, provider_id=provider_id
    )
    await state.clear()

    search_new_cb = f"ps:psr:{provider_id}" if provider_id else "ps:search"
    no_search_back = (
        [f"🔙 مزود #{provider_id}", f"ps:prov:{provider_id}"]
        if provider_id
        else ["📥 الخدمات المسحوبة", "admin:pulled_services"]
    )

    if not services:
        await message.answer(
            f"🔎 <b>لا نتائج لـ «{escape(query)}»</b>\\n\\n"
            "جرّب كلمة أقصر، أو ابحث بآيدي الخدمة عند المزود، "
            "أو تصفّح المنصات من «📥 الخدمات المسحوبة».",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔎 بحث جديد", callback_data=search_new_cb, style="primary"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=no_search_back[0], callback_data=no_search_back[1]
                        )
                    ],
                ]
            ),
        )
        return

    _store_search(
        message.from_user.id, query, [service.id for service in services], provider_id
    )
    await message.answer(
        f"🔎 <b>نتائج «{escape(query)}»</b>\\n\\n"
        f"وُجدت <b>{total}</b> خدمة · من الأرخص للأغلى\\n"
        f"📄 صفحة 1/{max(1, (total - 1) // SEARCH_PER_PAGE + 1)}\\n\\n"
        "اضغط الخدمة لتنشرها في القسم الذي تريده.",
        reply_markup=_search_results_kb(services, 0, total, provider_id),
    )


@router.callback_query(F.data.startswith("ps:sr:"))
async def pulled_search_page(callback: CallbackQuery, session, state: FSMContext):
    """تنقّل بين صفحات نتائج البحث."""
    await state.clear()
    parts = callback.data.split(":")
    try:
        page = max(0, int(parts[2]))
    except (IndexError, ValueError):
        page = 0

    cached = _get_search(callback.from_user.id)
    if cached is None:
        await callback.answer("انتهت صلاحية البحث — أعد كتابته.", show_alert=True)
        total_count = (
            await session.execute(
                select(func.count(ProviderService.id)).where(
                    ProviderService.status == ProviderServiceStatus.ACTIVE
                )
            )
        ).scalar_one()
        await state.set_state(AdminPulledServicesStates.waiting_search)
        await callback.message.edit_text(
            "🔎 <b>ابحث عن خدمة محددة</b>\\n\\n"
            f"لديك <b>{int(total_count)}</b> خدمة مسحوبة مخفية عن المتجر.\\n\\n"
            "اكتب الاسم أو التصنيف أو آيدي الخدمة عند المزود.",
            reply_markup=_search_intro_kb(),
        )
        return

    query, service_ids, provider_id = cached
    if not service_ids:
        await callback.answer("لا توجد نتائج محفوظة.", show_alert=True)
        return

    result = await session.execute(
        select(ProviderService).where(
            ProviderService.id.in_(service_ids),
            ProviderService.status == ProviderServiceStatus.ACTIVE,
        )
    )
    found = {service.id: service for service in result.scalars().all()}
    services = [found[service_id] for service_id in service_ids if service_id in found]
    total = len(services)
    last_page = max(0, (total - 1) // SEARCH_PER_PAGE)
    page = min(page, last_page)
    start = page * SEARCH_PER_PAGE
    await callback.message.edit_text(
        f"🔎 <b>نتائج «{escape(query)}»</b>\\n\\n"
        f"وُجدت <b>{total}</b> خدمة · من الأرخص للأغلى\\n"
        f"📄 صفحة {page + 1}/{last_page + 1}\\n\\n"
        "اضغط الخدمة لتنشرها في القسم الذي تريده.",
        reply_markup=_search_results_kb(
            services[start : start + SEARCH_PER_PAGE], page, total, provider_id
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "ps:build")
async def pulled_build_sections(callback: CallbackQuery, session, state: FSMContext):
    """🚀 البناء التلقائي: أقسام داخلية لكل تطبيق + أرخص 10 خدمات بكل نوع."""
    await state.clear()
    await callback.answer("🚀 جارٍ الإنشاء والتحديث...")
    await SmmSectionsService.upgrade_legacy_limit()
    limit = await SmmSectionsService.limit()
    report = await SmmSectionsService.build(session)
    lines = [
        "🚀 <b>تم تنفيذ البناء التلقائي لأقسام الرشق</b>\n",
        f"📱 التطبيقات المعالجة: <b>{report['apps']}</b>",
        f"📂 أقسام داخلية جديدة: <b>{report['sections_created']}</b>",
        f"📦 منتجات جديدة منشورة: <b>{report['products_created']}</b>",
        f"🔄 منتجات أُعيد ترتيبها: <b>{report['reordered']}</b>",
        f"♻️ منتجات أُعيد تفعيلها: <b>{report['products_reactivated']}</b>",
        f"⏸ منتجات تلقائية خارجة عن أرخص {limit} عُطّلت: "
        f"<b>{report['products_deactivated']}</b>",
        f"⏭ خدمات منشورة مسبقاً (لم تتكرر): <b>{report['skipped_existing']}</b>",
        f"🚫 خدمات بلا سعر تُجوهلت (سيرفر/عناوين): "
        f"<b>{report['skipped_unpriced']}</b>",
    ]
    if report["errors"]:
        lines.append(f"\n⚠️ أخطاء جزئية: <b>{report['errors']}</b> (راجع السجل)")
    lines.append(
        f"\nيُنشر أرخص <b>{limit}</b> خدمة في كل قسم داخلي، والسعر = تكلفة "
        "المزود + هامش الربح المحدد، والترتيب من الأرخص للأغلى.\n"
        "الخدمات بلا سعر (0$) لا تُنشر إطلاقاً.\n"
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

    from services.service_localization_service import (
        display_category_name,
        is_arabic,
        service_name_ar,
    )

    display_name = service_name_ar(service)
    name_lines = f"الاسم: {display_name}\n"
    if not is_arabic(service.name or "") and display_name != (service.name or ""):
        name_lines += f"<i>أصلي: {service.name}</i>\n"
    category_ar = display_category_name(service.category) or "—"

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
        f"📂 التصنيف: {category_ar}\n\n"
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

    from services.service_localization_service import service_name_ar

    svc_display = service_name_ar(service)
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
                f"(«{k_label}») وينشر أرخص 10 خدمات فيه، ثم تعود وتنشر هذه الخدمة "
                "بسعرك الخاص داخل القسم.\n\n"
                f"الخدمة: {svc_display}",
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
    from services.service_localization_service import service_name_ar

    await callback.message.edit_text(
        "📂 <b>اختر القسم الذي سيظهر فيه المنتج</b>\n\n"
        f"الخدمة: {service_name_ar(service)}\n"
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
    from services.service_localization_service import service_name_ar

    svc_display = service_name_ar(service)
    await callback.message.edit_text(
        "💰 <b>سعر البيع لكل 1000</b>\n\n"
        f"الخدمة: {svc_display}\n"
        f"تكلفة المزود: {service.rate_usd}$ / 1000\n\n"
        "أرسل سعر البيع بالدولار <b>لكل 1000</b> (مثال: 1.50).\n"
        "يُحفظ هذا السعر <b>كهامش ربح للمنتج</b> (غير يدوي)، فيتبع أولوية "
        "الهوامش: قسم فرعي ← قسم ← الهامش العام. فإن كان هامش قسم/عام "
        "مضبوطاً يُعاد حساب السعر منه.\n"
        "لقفل سعرك بالضبط: افتح المنتج من «📦 إدارة المنتجات» واجعل هامشه "
        "<b>يدوياً</b>."
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
