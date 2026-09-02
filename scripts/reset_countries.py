#!/usr/bin/env python3
"""تصفير جدول الدول لإعادة سحبها من HeroSMS بشكل نظيف.

متى تستخدمه؟
- إذا كانت الدول المسحوبة سابقاً فيها فوضى (أسماء لاتينية، أعلام ناقصة،
  تكرارات بلحقة _hs، أو أسعار قديمة مبنية على نظام العملة الخاطئ).

ماذا يفعل؟
1) يحذف الدول (افتراضياً: فقط الدول التي لديها herosms_code أي جاءت من
   السحب التلقائي، ويبقي الدول المضافة يدوياً). استخدم --all لحذف الكل.
2) يحذف قواعد التسعير الخاصة بالدول المحذوفة (ServicePricing) لأنها
   أصبحت تشير لدول غير موجودة.
3) يمسح كاش أسعار الأرقام (number-price:*) كي لا تظهر أسعار قديمة.
4) اختيارياً مع --sync يسحب الدول من جديد فوراً (واتساب + تيليجرام
   مع تفعيل المتوفر منها).

ملاحظات أمان:
- لا يوجد أي مفتاح خارجي يشير لجدول countries: الطلبات القديمة
  (NumberOrder وغيرها) تحفظ country_code كنص ولن تتأثر إطلاقاً.
- المستخدمون وأرصدتهم لا يُمسون.
- البوت يجب إعادة تشغيله بعد العملية (أو انتظر دقيقتين) لتفريغ كاش
  لوحة الأسعار من الذاكرة.

أمثلة:
  # معاينة فقط (لا يحذف شيئاً)
  python scripts/reset_countries.py --dry-run

  # حذف الدول المسحوبة تلقائياً فقط (يُبقي اليدوية) بعد تأكيد
  python scripts/reset_countries.py

  # حذف كل الدول بلا استثناء
  python scripts/reset_countries.py --all

  # حذف المسحوبة تلقائياً + سحب جديد فوري من HeroSMS
  python scripts/reset_countries.py --yes --sync
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# يسمح بتشغيل السكربت من أي مكان: نضيف جذر مشروع البوت للمسار.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select  # noqa: E402

from database.engine import async_session_maker  # noqa: E402
from database.models import Country, ServicePricing  # noqa: E402


async def _collect_targets(delete_all: bool) -> list[Country]:
    async with async_session_maker() as session:
        if delete_all:
            result = await session.execute(select(Country).order_by(Country.id))
        else:
            result = await session.execute(
                select(Country)
                .where(Country.herosms_code.is_not(None))
                .order_by(Country.id)
            )
        return list(result.scalars().all())


async def _delete_countries(countries: list[Country]) -> tuple[int, int]:
    """يحذف الدول وقواعد التسعير المرتبطة بها. يرجع (دول محذوفة، قواعد تسعير محذوفة)."""
    codes = [c.code for c in countries]
    async with async_session_maker() as session:
        pricing_deleted = 0
        if codes:
            pricing_result = await session.execute(
                delete(ServicePricing).where(ServicePricing.country_code.in_(codes))
            )
            pricing_deleted = pricing_result.rowcount or 0

        countries_result = await session.execute(
            delete(Country).where(Country.id.in_([c.id for c in countries]))
        )
        countries_deleted = countries_result.rowcount or 0
        await session.commit()
    return countries_deleted, pricing_deleted


async def _clear_price_cache() -> int:
    """يمسح كاش أسعار الأرقام كي لا تظهر أسعار الدول المحذوفة."""
    try:
        from services.price_cache_service import PriceCacheService

        return await PriceCacheService.invalidate("number-price:")
    except Exception as exc:  # noqa: BLE001 - فشل الكاش لا يوقف العملية
        print(f"⚠️ تعذّر مسح كاش الأسعار ({exc}) — أعد تشغيل البوت لتفريغه.")
        return 0


async def _resync_from_herosms() -> None:
    """سحب جديد للدول من HeroSMS بعد التصفير."""
    from services.herosms_sync_service import sync_herosms_countries

    async with async_session_maker() as session:
        report = await sync_herosms_countries(
            session,
            wanted_services=["whatsapp", "telegram"],
            activate=True,
        )
    print(report.summary())


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="تصفير جدول الدول لإعادة سحبها من HeroSMS بشكل نظيف."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="حذف كل الدول بلا استثناء (حتى المضافة يدوياً).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="تنفيذ الحذف دون سؤال تأكيد.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="عرض ما سيُحذف فقط دون حذف أي شيء.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="سحب الدول من HeroSMS فوراً بعد الحذف (واتساب + تيليجرام مع تفعيل المتوفر).",
    )
    args = parser.parse_args()

    targets = await _collect_targets(delete_all=args.all)

    async with async_session_maker() as session:
        total = (await session.execute(select(func.count(Country.id)))).scalar_one()

    mode = "كل الدول" if args.all else "الدول المسحوبة تلقائياً من HeroSMS فقط"
    print("=" * 50)
    print(f"🌍 إجمالي الدول في قاعدة البيانات: {total}")
    print(f"🎯 سيتم حذف ({mode}): {len(targets)}")
    kept = total - len(targets)
    if kept:
        print(f"⚪ ستبقى دون حذف: {kept}")
    print("=" * 50)

    if args.dry_run:
        for c in targets[:30]:
            print(f"  • {c.flag} {c.name_ar} (code={c.code}, herosms={c.herosms_code or '—'})")
        if len(targets) > 30:
            print(f"  • ... و {len(targets) - 30} دولة أخرى")
        print("🔎 وضع المعاينة: لم يُحذف أي شيء.")
        return

    if not targets:
        print("✅ لا توجد دول مطابقة للحذف. لا شيء يجب فعله.")
        return

    if not args.yes:
        answer = input("⚠️ متأكد من الحذف؟ اكتب نعم للتأكيد: ").strip()
        if answer not in ("نعم", "yes", "y"):
            print("❌ تم الإلغاء. لم يُحذف شيء.")
            return

    countries_deleted, pricing_deleted = await _delete_countries(targets)
    print(f"🗑 تم حذف {countries_deleted} دولة.")
    if pricing_deleted:
        print(f"💰 تم حذف {pricing_deleted} قاعدة تسعير خاصة بدول محذوفة.")

    cache_cleared = await _clear_price_cache()
    if cache_cleared:
        print(f"🧹 تم مسح {cache_cleared} مفتاح كاش أسعار.")

    if args.sync:
        print("🔄 بدء السحب الجديد من HeroSMS...")
        await _resync_from_herosms()
    else:
        print("✅ تم. شغّل السحب من لوحة الأدمن: إدارة الدول → 🔄 سحب دول من HeroSMS")
    print("🔁 لا تنسَ إعادة تشغيل البوت لتفريغ كاش لوحة الأسعار من الذاكرة.")


if __name__ == "__main__":
    asyncio.run(main())