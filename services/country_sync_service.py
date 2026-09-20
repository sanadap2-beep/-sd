"""
سحب الدول تلقائياً من مزودي 5sim و GrizzlySMS — بنفس نمط سحب HeroSMS.

الفكرة:
1) 5sim: طلب واحد لكل منتج (``guest/prices?product=whatsapp``) يكشف
   مخزون كل الدول دفعة واحدة — طلبان فقط بدل 300 (يتجنب حد 5sim
   والحظر المؤقت). Grizzly: فحص getPrices بإيقاع هادئ.
2) إنشاء/دمج سجلات الدول مع تعريب الاسم والعلم (نفس خريطة HeroSMS).
3) تفعيل الدول التي فيها مخزون تلقائياً.

الدمج الذكي (حتى لا تتكرر الدول):
- أولاً: بحث بكود المزود (fivesim_code / grizzly_code).
- ثانياً: بحث بالاسم العربي (صف مسحوب من HeroSMS سابقاً يُدمج معه الكود الجديد).
- ثالثاً: بحث بالمعرف الداخلي slug مع كود مزود فارغ.
- أخيراً: إنشاء دولة جديدة.

العرض للمستخدم لا يتغير: build_board يرتب من الأرخص للأغلى مع العلم
والاسم العربي والسعر بعد هامش الربح، وcountries_price_kb تقسم 25 دولة
بكل قائمة — كل ذلك يعمل تلقائياً على الدول المسحوبة هنا.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select

from config import settings
from database.models import Country, NumberService
from services.herosms_sync_service import (
    HEROSMS_FALLBACK_COUNTRIES,
    _is_arabic,
    _label_for,
    _slugify,
)

logger = logging.getLogger(__name__)

# خدمة البوت الداخلية → كود المنتج لدى كل مزود
FIVESIM_SERVICE_CODES: dict[str, str] = {
    "whatsapp": "whatsapp",
    "telegram": "telegram",
}

GRIZZLY_SERVICE_CODES: dict[str, str] = {
    "whatsapp": "wa",
    "telegram": "tg",
}


@dataclass
class CountrySyncReport:
    """تقرير عملية سحب واحدة."""

    provider_label: str = ""
    service_codes: list[str] = field(default_factory=list)
    catalog_source: str = ""
    fetched_countries: int = 0
    added: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    activated: int = 0
    skipped_no_stock: int = 0
    failed: int = 0

    def summary(self) -> str:
        lines = [
            f"✅ <b>اكتمل سحب الدول من {self.provider_label}</b>",
            "",
            f"🔌 مصدر الكتالوج: <code>{self.catalog_source}</code>",
            f"🌐 عدد الدول المفحوصة: <b>{self.fetched_countries}</b>",
            f"🆕 دول جديدة أُضيفت: <b>{len(self.added)}</b>",
            f"🔗 تم دمجها مع دول قائمة: <b>{len(self.merged)}</b>",
            f"♻️ تم تحديث بياناتها: <b>{len(self.updated)}</b>",
            f"🟢 دول تم تفعيلها لوجود مخزون: <b>{self.activated}</b>",
            f"⚪ دول بدون مخزون (معطلة): <b>{self.skipped_no_stock}</b>",
        ]
        if self.added:
            lines.append("")
            lines.append("<b>أبرز الدول المضافة:</b>")
            lines.extend(f"• {name}" for name in self.added[:30])
            if len(self.added) > 30:
                lines.append(f"• ... وغيرها ({len(self.added)} دولة)")
        return "\n".join(lines)


async def _ensure_number_services(
    session,
    codes: dict[str, str],
    code_field: str,
) -> dict[str, str]:
    """يضمن وجود خدمتي واتساب/تيليجرام مع كود المزود (يملأ الفارغ فقط).

    يرجع {خدمة البوت: كود المزود}.
    """
    resolved: dict[str, str] = {}
    for svc_code, provider_code in codes.items():
        result = await session.execute(
            select(NumberService).where(NumberService.code == svc_code)
        )
        svc = result.scalar_one_or_none()
        if svc is None:
            emoji = "💬" if svc_code == "whatsapp" else "✈️"
            name = "واتساب" if svc_code == "whatsapp" else "تيليجرام"
            svc = NumberService(
                code=svc_code,
                name_ar=name,
                emoji=emoji,
                is_active=True,
                sort_order=1 if svc_code == "whatsapp" else 2,
            )
            setattr(svc, code_field, provider_code)
            session.add(svc)
            await session.flush()
        else:
            if not getattr(svc, code_field, None):
                setattr(svc, code_field, provider_code)
            svc.is_active = True
        resolved[svc_code] = getattr(svc, code_field)
    await session.commit()
    return resolved


async def _fetch_fivesim_catalog(
    provider,
    products: list[str],
) -> tuple[dict[str, dict], str, dict[str, str]]:
    """مخزون 5sim بطلب واحد لكل منتج: ``guest/prices?product=whatsapp``.

    طلبان فقط يكشفان كل الدول (واتساب + تيليجرام) بدل 300 طلب —
    هذا يتجنب حد 5sim (100 طلب/ثانية) والحظر المؤقت الذي كان يُفشل السحب.
    يرجع (stock, source, slug_to_eng) حيث:
    ``stock = {slug: {product: operators_node}}``.
    """
    stock: dict[str, dict] = {}
    try:
        data = await provider._request("GET", "guest/countries")
        slug_to_eng = {
            str(slug): str((info or {}).get("text_en") or slug)
            for slug, info in (data or {}).items()
        } if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning(f"فشل كتالوج أسماء 5sim ({exc})")
        slug_to_eng = {}

    ok = 0
    for product in products:
        try:
            data = await provider._request("GET", f"guest/prices?product={product}")
        except Exception as exc:
            logger.warning(f"فشل أسعار 5sim لمنتج {product} ({exc})")
            continue
        node = data.get(str(product)) if isinstance(data, dict) else None
        if not isinstance(node, dict):
            continue
        ok += 1
        for slug, ops in node.items():
            if isinstance(ops, dict):
                stock.setdefault(str(slug), {})[product] = ops
    source = (
        f"5sim guest/prices?product (طلبان: {ok}/{len(products)} ناجح)"
        if stock
        else "5sim guest/prices?product"
    )
    return stock, source, slug_to_eng


def _fivesim_node_has_stock(node: dict, product: str) -> bool:
    """هل يوجد مخزون بسعر حقيقي لمنتج 5sim داخل عقدة دولة؟"""
    service_node = node.get(str(product))
    if not isinstance(service_node, dict):
        return False
    for info in service_node.values():
        if not isinstance(info, dict):
            continue
        try:
            count = int(info.get("count", 0) or 0)
            cost = Decimal(str(info.get("cost", 0)))
        except Exception:
            continue
        if count > 0 and cost > 0:
            return True
    return False


async def _fetch_grizzly_catalog(provider) -> tuple[list[dict], str]:
    """كتالوج Grizzly عبر getCountries — الـ API متوافق مع sms-activate."""
    try:
        raw = await provider._request({"action": "getCountries"})
        data = json.loads(raw)
        items = data.values() if isinstance(data, dict) else data
        out = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            cid = item.get("id")
            if cid is None:
                continue
            if int(item.get("visible", 1) or 1) == 0:
                continue
            eng = item.get("eng") or item.get("rus") or str(cid)
            out.append({"id": str(cid), "eng": str(eng)})
        if out:
            return out, "Grizzly getCountries API"
    except Exception as exc:
        logger.warning(f"فشل getCountries من Grizzly ({exc}) — كتالوج احتياطي")
    return (
        [{"id": cid, "eng": name} for cid, name in HEROSMS_FALLBACK_COUNTRIES.items()],
        "الكتالوج المدمج الشامل (Fallback — متوافق sms-activate)",
    )


async def _find_same_provider_row(
    session,
    code_field: str,
    cid: str,
) -> Country | None:
    """يبحث فقط عن صف يملك كود هذا المزود حصراً.

    ممنوع الدمج العابر للمزودين: كل مزود له صفوفه الخاصة حتى لو
    تكررت الدولة — هذا يضمن أن حذف الكل وإعادة السحب لا يخلط الأكواد،
    وأن سيرفر كل مزود يعرض دوله فقط.
    """
    res = await session.execute(
        select(Country).where(getattr(Country, code_field) == cid)
    )
    return res.scalar_one_or_none()


async def _adopt_numeric_fivesim_row(session, name_ar: str, cid: str) -> Country | None:
    """إصلاح لمرة واحدة: صفوف أخذت كود 5sim رقمياً من سحب سابق خاطئ.

    الكود الرسمي هو الاسم (slug) — نتبنى الصف ونصحح كوده بدل التكرار.
    يعمل فقط على صفوف كودها رقمي، ولا يمس أي مزود آخر.
    """
    res = await session.execute(select(Country).where(Country.name_ar == name_ar))
    for row in res.scalars().all():
        current = getattr(row, "fivesim_code", None)
        if current and str(current).isdigit() and str(current) != str(cid):
            row.fivesim_code = cid
            return row
    return None


async def _store_countries(
    session,
    report: CountrySyncReport,
    code_field: str,
    english_names: dict[str, str],
    availability: dict[str, bool],
    activate: bool,
) -> None:
    """حفظ/دمج الدول في قاعدة البيانات."""
    for cid, eng in english_names.items():
        try:
            has_stock = availability.get(cid, False)
            name_ar, flag = _label_for(eng)
            slug = _slugify(eng)

            existing = await _find_same_provider_row(session, code_field, cid)
            if existing is None and code_field == "fivesim_code":
                existing = await _adopt_numeric_fivesim_row(session, name_ar, cid)
                if existing is not None:
                    report.merged.append(f"{existing.flag} {existing.name_ar}")
                    if activate and has_stock and not existing.is_active:
                        existing.is_active = True
                        report.activated += 1
                    continue
            if existing is not None:
                changed = False
                just_merged = False
                current = getattr(existing, code_field, None)
                if not current:
                    setattr(existing, code_field, cid)
                    changed = True
                    just_merged = True
                    report.merged.append(f"{existing.flag} {existing.name_ar}")
                if (
                    not existing.name_ar
                    or existing.name_ar.startswith("HeroSMS")
                    or not _is_arabic(existing.name_ar)
                ):
                    existing.name_ar = name_ar
                    changed = True
                if existing.flag in (None, "", "🌍") and flag != "🌍":
                    existing.flag = flag
                    changed = True
                if activate and has_stock and not existing.is_active:
                    existing.is_active = True
                    report.activated += 1
                    changed = True
                if changed and not just_merged:
                    report.updated.append(f"{existing.flag} {existing.name_ar}")
                continue

            # دولة جديدة تماماً — معرف فريد
            final_slug = slug
            res = await session.execute(select(Country).where(Country.code == final_slug))
            if res.scalar_one_or_none() is not None:
                final_slug = f"{slug}_{code_field}_{cid}".replace(" ", "_")
            is_active = bool(activate and has_stock)
            new_country = Country(
                code=final_slug,
                name_ar=name_ar,
                flag=flag,
                is_active=is_active,
            )
            setattr(new_country, code_field, cid)
            session.add(new_country)
            report.added.append(f"{flag} {name_ar}")
            if is_active:
                report.activated += 1
            else:
                report.skipped_no_stock += 1
        except Exception as e:
            report.failed += 1
            logger.error(f"خطأ سحب دولة #{cid} ({code_field}): {e}")
    await session.commit()


async def sync_fivesim_countries(
    session,
    wanted_services: list[str] | None = None,
    activate: bool = True,
    provider=None,
) -> CountrySyncReport:
    """سحب دول 5sim: طلب لكل منتج (واتساب/تيليجرام) + حفظ/دمج + تفعيل.

    أكواد الدول أسماء (slugs) حسب التوثيق الرسمي — صالحة للأسعار والشراء.
    """
    from providers.fivesim import FiveSimProvider

    wanted_services = wanted_services or list(FIVESIM_SERVICE_CODES)
    service_codes = await _ensure_number_services(session, FIVESIM_SERVICE_CODES, "fivesim_code")
    products = [service_codes[s] for s in wanted_services if service_codes.get(s)]

    report = CountrySyncReport(provider_label="5sim", service_codes=wanted_services)
    provider = provider or FiveSimProvider()

    stock, source, slug_to_eng = await _fetch_fivesim_catalog(provider, products)
    report.catalog_source = source
    if not stock:
        return report
    report.fetched_countries = len(stock)

    availability: dict[str, bool] = {}
    english_names: dict[str, str] = {}
    for slug, node in stock.items():
        english_names[slug] = slug_to_eng.get(slug, slug.replace("_", " ").title())
        availability[slug] = any(
            _fivesim_node_has_stock(node, p) for p in products
        )

    await _store_countries(session, report, "fivesim_code", english_names, availability, activate)
    return report


async def sync_grizzly_countries(
    session,
    wanted_services: list[str] | None = None,
    activate: bool = True,
    provider=None,
    concurrency: int = 5,
) -> CountrySyncReport:
    """سحب دول GrizzlySMS: الكتالوج + فحص مخزون واتساب/تيليجرام + حفظ."""
    from providers.grizzly import GrizzlyProvider

    wanted_services = wanted_services or list(GRIZZLY_SERVICE_CODES)
    service_codes = await _ensure_number_services(session, GRIZZLY_SERVICE_CODES, "grizzly_code")

    report = CountrySyncReport(provider_label="GrizzlySMS", service_codes=wanted_services)
    provider = provider or GrizzlyProvider()

    catalog, source = await _fetch_grizzly_catalog(provider)
    report.catalog_source = source
    if not catalog:
        return report
    report.fetched_countries = len(catalog)

    semaphore = asyncio.Semaphore(concurrency)
    availability: dict[str, bool] = {}
    english_names: dict[str, str] = {}

    async def _probe(entry: dict):
        cid = str(entry.get("id", "")).strip()
        eng = str(entry.get("eng") or f"Grizzly {cid}").strip()
        if not cid:
            return
        english_names[cid] = eng
        async with semaphore:
            await asyncio.sleep(0.2)  # إيقاع هادئ: فحص مئات الدول دفعة واحدة
            try:
                has_any = False
                for s_code in wanted_services:
                    g_service = service_codes.get(s_code)
                    if not g_service:
                        continue
                    price: Decimal | None = await provider.get_price(cid, g_service)
                    if price is None or price <= 0:
                        continue
                    # تحقق ثانٍ بالمخزون الحي: getPrices قد يعرض مخزوناً
                    # وهمياً (سعر + count>0) بينما getNumbersStatus يكشف
                    # النفاد الحقيقي. عند تعذر الفحص (None) نقبل السعر
                    # حتى لا نحجب دولاً سليمة خطأً.
                    stock_fn = getattr(provider, "get_stock_count", None)
                    if callable(stock_fn):
                        try:
                            live = await stock_fn(cid, g_service)
                        except Exception:
                            live = None
                        if live is not None and live <= 0:
                            continue
                    has_any = True
                    break
                availability[cid] = has_any
            except Exception:
                availability[cid] = False

    await asyncio.gather(*(_probe(c) for c in catalog))
    await _store_countries(session, report, "grizzly_code", english_names, availability, activate)
    return report
