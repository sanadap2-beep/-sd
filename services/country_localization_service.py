"""
تعريب أسماء الدول والعلم في كل مكان يظهر فيه اسم الدولة.

القاعدة:
- أسماء الدول قد تصل من المزود أجنبية (أو يُدخلها الأدمن بالإنجليزي).
- نعيد تسميتها بالعربي + العلم الصحيح من نفس قاموس مزامنة HeroSMS
  (COUNTRY_LABELS) — دون المساس بأي كود ربط مع المزود
  (fivesim_code/herosms_code/sms_activate_code/smshub_code تبقى كما هي).
- التسمية تتم على مستوى العرض (فوري) + ترميم قاعدة البيانات عند
  كل إقلاع/مزامنة حتى لا يعود الاسم أجنبياً مرة أخرى.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from database.models import Country
from services.herosms_sync_service import _is_arabic, _label_for

logger = logging.getLogger(__name__)


def localize_name(name: str) -> tuple[str, str]:
    """يعيد (الاسم العربي، العلم) لاسم دولة أجنبي أو عربي.

    الاسم العربي المار يترك كما هو (الأدمن قد يعدله يدوياً).
    """
    if not name or _is_arabic(name):
        return (name or "", "🌍")
    arabic, flag = _label_for(name)
    return arabic, flag


def display_name(country: Country) -> str:
    """الاسم العربي المعروض للمستخدم (مع ترميم فوري للاسم الأجنبي)."""
    if _is_arabic(country.name_ar):
        return country.name_ar
    return localize_name(country.name_ar)[0] or country.name_ar


def display_flag(country: Country) -> str:
    """العلم الصحيح: الحالي إن كان مضبوطاً، وإلا من قاموس الترجمة."""
    if country.flag and country.flag not in ("", "🌍"):
        return country.flag
    return localize_name(country.name_ar)[1] or "🌍"


async def heal_countries(session) -> int:
    """يرمم أسماء/أعلام الدول الأجنبية المخزنة في قاعدة البيانات.

    يُستدعى عند الإقلاع وبعد أي مزامنة دول. لا يلمس أكواد المزودين أبداً.
    """
    result = await session.execute(select(Country))
    countries = list(result.scalars().all())
    fixed = 0
    for country in countries:
        if _is_arabic(country.name_ar):
            continue
        name_ar, flag = localize_name(country.name_ar)
        changed = False
        if name_ar and name_ar != country.name_ar:
            country.name_ar = name_ar
            changed = True
        if country.flag in (None, "", "🌍") and flag != "🌍":
            country.flag = flag
            changed = True
        if changed:
            fixed += 1
    if fixed:
        await session.commit()
        logger.info("🌍 عُرّبت أسماء %s دولة أجنبية في قاعدة البيانات.", fixed)
    return fixed
