"""
خدمة سحب الدول المتاحة من مزود HeroSMS تلقائياً.

المهام:
1) جلب كتالوج الدول عبر action=getCountries (مع خريطة احتياطية كاملة).
2) فحص الأسعار والمخزون لكل دولة بخاصية التوازي.
3) إنشاء وتحديث سجلات الدول وتعريبها مع الأعلام الدقيقة.
4) تفعيل الدول المتوفرة تلقائياً وضمان عدم فقدان أي دولة قائمة.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select

from database.models import Country, NumberService
from providers.herosms import HeroSMSProvider

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# ══════════════ خرائط الترجمة والأعلام الشاملة ══════════════
# ══════════════════════════════════════════════════════════════

# الاسم الإنجليزي (بأحرف صغيرة وبدون زوائد) → (الاسم العربي، العلم)
COUNTRY_LABELS: dict[str, tuple[str, str]] = {
    "russia": ("روسيا", "🇷🇺"),
    "ukraine": ("أوكرانيا", "🇺🇦"),
    "kazakhstan": ("كازاخستان", "🇰🇿"),
    "china": ("الصين", "🇨🇳"),
    "philippines": ("الفلبين", "🇵🇭"),
    "myanmar": ("ميانمار", "🇲🇲"),
    "indonesia": ("إندونيسيا", "🇮🇩"),
    "malaysia": ("ماليزيا", "🇲🇾"),
    "kenya": ("كينيا", "🇰🇪"),
    "tanzania": ("تنزانيا", "🇹🇿"),
    "vietnam": ("فيتنام", "🇻🇳"),
    "kyrgyzstan": ("قرغيزستان", "🇰🇬"),
    "usa": ("أمريكا", "🇺🇸"),
    "us": ("أمريكا", "🇺🇸"),
    "united states": ("أمريكا", "🇺🇸"),
    "unitedstates": ("أمريكا", "🇺🇸"),
    "united states of america": ("أمريكا", "🇺🇸"),
    "unitedstatesofamerica": ("أمريكا", "🇺🇸"),
    "usa virtual": ("أمريكا (افتراضي)", "🇺🇸"),
    "usavirtual": ("أمريكا (افتراضي)", "🇺🇸"),
    "usa physical": ("أمريكا (حقيقي)", "🇺🇸"),
    "israel": ("إسرائيل", "🇮🇱"),
    "hongkong": ("هونغ كونغ", "🇭🇰"),
    "hong kong": ("هونغ كونغ", "🇭🇰"),
    "poland": ("بولندا", "🇵🇱"),
    "england": ("بريطانيا", "🇬🇧"),
    "united kingdom": ("بريطانيا", "🇬🇧"),
    "great britain": ("بريطانيا", "🇬🇧"),
    "britain": ("بريطانيا", "🇬🇧"),
    "uk": ("بريطانيا", "🇬🇧"),
    "madagascar": ("مدغشقر", "🇲🇬"),
    "congo": ("الكونغو", "🇨🇩"),
    "dcongo": ("الكونغو الديمقراطية", "🇨🇩"),
    "dr congo": ("الكونغو الديمقراطية", "🇨🇩"),
    "democratic republic of the congo": ("الكونغو الديمقراطية", "🇨🇩"),
    "congo republic": ("الكونغو", "🇨🇬"),
    "congo brazzaville": ("الكونغو", "🇨🇬"),
    "republic of the congo": ("الكونغو", "🇨🇬"),
    "nigeria": ("نيجيريا", "🇳🇬"),
    "macao": ("ماكاو", "🇲🇴"),
    "macau": ("ماكاو", "🇲🇴"),
    "egypt": ("مصر", "🇪🇬"),
    "india": ("الهند", "🇮🇳"),
    "ireland": ("أيرلندا", "🇮🇪"),
    "cambodia": ("كمبوديا", "🇰🇭"),
    "laos": ("لاوس", "🇱🇦"),
    "haiti": ("هايتي", "🇭🇹"),
    "ivory coast": ("ساحل العاج", "🇨🇮"),
    "ivory": ("ساحل العاج", "🇨🇮"),
    "cote d ivoire": ("ساحل العاج", "🇨🇮"),
    "cote divoire": ("ساحل العاج", "🇨🇮"),
    "gambia": ("غامبيا", "🇬🇲"),
    "serbia": ("صربيا", "🇷🇸"),
    "yemen": ("اليمن", "🇾🇪"),
    "south africa": ("جنوب أفريقيا", "🇿🇦"),
    "southafrica": ("جنوب أفريقيا", "🇿🇦"),
    "romania": ("رومانيا", "🇷🇴"),
    "colombia": ("كولومبيا", "🇨🇴"),
    "estonia": ("إستونيا", "🇪🇪"),
    "azerbaijan": ("أذربيجان", "🇦🇿"),
    "canada": ("كندا", "🇨🇦"),
    "morocco": ("المغرب", "🇲🇦"),
    "ghana": ("غانا", "🇬🇭"),
    "argentina": ("الأرجنتين", "🇦🇷"),
    "uzbekistan": ("أوزبكستان", "🇺🇿"),
    "cameroon": ("الكاميرون", "🇨🇲"),
    "chad": ("تشاد", "🇹🇩"),
    "germany": ("ألمانيا", "🇩🇪"),
    "lithuania": ("ليتوانيا", "🇱🇹"),
    "croatia": ("كرواتيا", "🇭🇷"),
    "sweden": ("السويد", "🇸🇪"),
    "iraq": ("العراق", "🇮🇶"),
    "netherlands": ("هولندا", "🇳🇱"),
    "latvia": ("لاتفيا", "🇱🇻"),
    "austria": ("النمسا", "🇦🇹"),
    "belarus": ("بيلاروسيا", "🇧🇾"),
    "thailand": ("تايلاند", "🇹🇭"),
    "saudi arabia": ("السعودية", "🇸🇦"),
    "saudiarabia": ("السعودية", "🇸🇦"),
    "saudi": ("السعودية", "🇸🇦"),
    "mexico": ("المكسيك", "🇲🇽"),
    "taiwan": ("تايوان", "🇹🇼"),
    "spain": ("إسبانيا", "🇪🇸"),
    "iran": ("إيران", "🇮🇷"),
    "algeria": ("الجزائر", "🇩🇿"),
    "slovenia": ("سلوفينيا", "🇸🇮"),
    "slovenia republic": ("سلوفينيا", "🇸🇮"),
    "bangladesh": ("بنغلاديش", "🇧🇩"),
    "senegal": ("السنغال", "🇸🇳"),
    "turkey": ("تركيا", "🇹🇷"),
    "turkiye": ("تركيا", "🇹🇷"),
    "türkiye": ("تركيا", "🇹🇷"),
    "turkey republic": ("تركيا", "🇹🇷"),
    "sri lanka": ("سريلانكا", "🇱🇰"),
    "srilanka": ("سريلانكا", "🇱🇰"),
    "peru": ("بيرو", "🇵🇪"),
    "pakistan": ("باكستان", "🇵🇰"),
    "new zealand": ("نيوزيلندا", "🇳🇿"),
    "newzealand": ("نيوزيلندا", "🇳🇿"),
    "guinea": ("غينيا", "🇬🇳"),
    "mali": ("مالي", "🇲🇱"),
    "venezuela": ("فنزويلا", "🇻🇪"),
    "ethiopia": ("إثيوبيا", "🇪🇹"),
    "mongolia": ("منغوليا", "🇲🇳"),
    "brazil": ("البرازيل", "🇧🇷"),
    "afghanistan": ("أفغانستان", "🇦🇫"),
    "uganda": ("أوغندا", "🇺🇬"),
    "angola": ("أنغولا", "🇦🇴"),
    "cyprus": ("قبرص", "🇨🇾"),
    "france": ("فرنسا", "🇫🇷"),
    "papua new guinea": ("بابوا غينيا الجديدة", "🇵🇬"),
    "papua": ("بابوا غينيا الجديدة", "🇵🇬"),
    "papuanewguinea": ("بابوا غينيا الجديدة", "🇵🇬"),
    "mozambique": ("موزمبيق", "🇲🇿"),
    "nepal": ("نيبال", "🇳🇵"),
    "belgium": ("بلجيكا", "🇧🇪"),
    "bulgaria": ("بلغاريا", "🇧🇬"),
    "hungary": ("المجر", "🇭🇺"),
    "moldova": ("مولدوفا", "🇲🇩"),
    "italy": ("إيطاليا", "🇮🇹"),
    "paraguay": ("باراغواي", "🇵🇾"),
    "honduras": ("هندوراس", "🇭🇳"),
    "tunisia": ("تونس", "🇹🇳"),
    "nicaragua": ("نيكاراغوا", "🇳🇮"),
    "timor-leste": ("تيمور الشرقية", "🇹🇱"),
    "timorleste": ("تيمور الشرقية", "🇹🇱"),
    "east timor": ("تيمور الشرقية", "🇹🇱"),
    "bolivia": ("بوليفيا", "🇧🇴"),
    "costa rica": ("كوستاريكا", "🇨🇷"),
    "costarica": ("كوستاريكا", "🇨🇷"),
    "guatemala": ("غواتيمالا", "🇬🇹"),
    "uae": ("الإمارات", "🇦🇪"),
    "united arab emirates": ("الإمارات", "🇦🇪"),
    "uae dubai": ("الإمارات", "🇦🇪"),
    "emirates": ("الإمارات", "🇦🇪"),
    "zimbabwe": ("زيمبابوي", "🇿🇼"),
    "puerto rico": ("بورتوريكو", "🇵🇷"),
    "puertorico": ("بورتوريكو", "🇵🇷"),
    "sudan": ("السودان", "🇸🇩"),
    "togo": ("توغو", "🇹🇬"),
    "kuwait": ("الكويت", "🇰🇼"),
    "el salvador": ("السلفادور", "🇸🇻"),
    "salvador": ("السلفادور", "🇸🇻"),
    "libya": ("ليبيا", "🇱🇾"),
    "libyan": ("ليبيا", "🇱🇾"),
    "jamaica": ("جامايكا", "🇯🇲"),
    "trinidad": ("ترينيداد", "🇹🇹"),
    "trinidad and tobago": ("ترينيداد وتوباغو", "🇹🇹"),
    "ecuador": ("الإكوادور", "🇪🇨"),
    "swaziland": ("إسواتيني", "🇸🇿"),
    "eswatini": ("إسواتيني", "🇸🇿"),
    "oman": ("عمان", "🇴🇲"),
    "bosnia": ("البوسنة", "🇧🇦"),
    "bosnia and herzegovina": ("البوسنة والهرسك", "🇧🇦"),
    "bosniaandherzegovina": ("البوسنة والهرسك", "🇧🇦"),
    "dominican": ("الدومينيكان", "🇩🇴"),
    "dominican republic": ("الدومينيكان", "🇩🇴"),
    "syria": ("سوريا", "🇸🇾"),
    "syrian": ("سوريا", "🇸🇾"),
    "qatar": ("قطر", "🇶🇦"),
    "panama": ("بنما", "🇵🇦"),
    "georgia": ("جورجيا", "🇬🇪"),
    "greece": ("اليونان", "🇬🇷"),
    "guineabissau": ("غينيا بيساو", "🇬🇼"),
    "guinea-bissau": ("غينيا بيساو", "🇬🇼"),
    "guyana": ("غيانا", "🇬🇾"),
    "iceland": ("آيسلندا", "🇮🇸"),
    "comoros": ("جزر القمر", "🇰🇲"),
    "saintkittsandnevis": ("سانت كيتس ونيفيس", "🇰🇳"),
    "liberia": ("ليبيريا", "🇱🇷"),
    "lesotho": ("ليسوتو", "🇱🇸"),
    "malawi": ("مالاوي", "🇲🇼"),
    "namibia": ("ناميبيا", "🇳🇦"),
    "niger": ("النيجر", "🇳🇪"),
    "rwanda": ("رواندا", "🇷🇼"),
    "japan": ("اليابان", "🇯🇵"),
    "north macedonia": ("شمال مقدونيا", "🇲🇰"),
    "northmacedonia": ("شمال مقدونيا", "🇲🇰"),
    "macedonia": ("مقدونيا", "🇲🇰"),
    "seychelles": ("سيشل", "🇸🇨"),
    "new caledonia": ("كاليدونيا الجديدة", "🇳🇨"),
    "newcaledonia": ("كاليدونيا الجديدة", "🇳🇨"),
    "cape verde": ("الرأس الأخضر", "🇨🇻"),
    "capeverde": ("الرأس الأخضر", "🇨🇻"),
    "djibouti": ("جيبوتي", "🇩🇯"),
    "montenegro": ("الجبل الأسود", "🇲🇪"),
    "switzerland": ("سويسرا", "🇨🇭"),
    "norway": ("النرويج", "🇳🇴"),
    "australia": ("أستراليا", "🇦🇺"),
    "south sudan": ("جنوب السودان", "🇸🇸"),
    "southsudan": ("جنوب السودان", "🇸🇸"),
    "cuba": ("كوبا", "🇨🇺"),
    "finland": ("فنلندا", "🇫🇮"),
    "denmark": ("الدنمارك", "🇩🇰"),
    "czech": ("التشيك", "🇨🇿"),
    "czechia": ("التشيك", "🇨🇿"),
    "czech republic": ("التشيك", "🇨🇿"),
    "slovakia": ("سلوفاكيا", "🇸🇰"),
    "albania": ("ألبانيا", "🇦🇱"),
    "armenia": ("أرمينيا", "🇦🇲"),
    "jordan": ("الأردن", "🇯🇴"),
    "lebanon": ("لبنان", "🇱🇧"),
    "turkmenistan": ("تركمانستان", "🇹🇲"),
    "tajikistan": ("طاجيكستان", "🇹🇯"),
    "somalia": ("الصومال", "🇸🇴"),
    "burundi": ("بوروندي", "🇧🇮"),
    "benin": ("بنين", "🇧🇯"),
    "gabon": ("الغابون", "🇬🇦"),
    "zambia": ("زامبيا", "🇿🇲"),
    "botswana": ("بوتسوانا", "🇧🇼"),
    "korea": ("كوريا الجنوبية", "🇰🇷"),
    "south korea": ("كوريا الجنوبية", "🇰🇷"),
    "korea south": ("كوريا الجنوبية", "🇰🇷"),
    "korea republic": ("كوريا الجنوبية", "🇰🇷"),
    "korea rep": ("كوريا الجنوبية", "🇰🇷"),
    "north korea": ("كوريا الشمالية", "🇰🇵"),
    "portugal": ("البرتغال", "🇵🇹"),
    "luxembourg": ("لوكسمبورغ", "🇱🇺"),
    "malta": ("مالطا", "🇲🇹"),
    "singapore": ("سنغافورة", "🇸🇬"),
    "russian federation": ("روسيا", "🇷🇺"),
    "russianfederation": ("روسيا", "🇷🇺"),
    "viet nam": ("فيتنام", "🇻🇳"),
    "kyrgyz republic": ("قرغيزستان", "🇰🇬"),
    "kyrgyzrepublic": ("قرغيزستان", "🇰🇬"),
    # دول كتالوج 5sim/Grizzly التي كانت تسقط للإنجليزية
    "antigua and barbuda": ("أنتيغوا وباربودا", "🇦🇬"),
    "antiguaandbarbuda": ("أنتيغوا وباربودا", "🇦🇬"),
    "aruba": ("أروبا", "🇦🇼"),
    "bahrain": ("البحرين", "🇧🇭"),
    "barbados": ("باربادوس", "🇧🇧"),
    "bhutan": ("بوتان", "🇧🇹"),
    "bhutane": ("بوتان", "🇧🇹"),
    "burkina faso": ("بوركينا فاسو", "🇧🇫"),
    "burkinafaso": ("بوركينا فاسو", "🇧🇫"),
    "french guiana": ("غويانا الفرنسية", "🇬🇫"),
    "frenchguiana": ("غويانا الفرنسية", "🇬🇫"),
    "reunion": ("ريونيون", "🇷🇪"),
    "maldives": ("المالديف", "🇲🇻"),
    "saint lucia": ("سانت لوسيا", "🇱🇨"),
    "saintlucia": ("سانت لوسيا", "🇱🇨"),
    "mauritania": ("موريتانيا", "🇲🇷"),
    "mauritius": ("موريشيوس", "🇲🇺"),
    "samoa": ("ساموا", "🇼🇸"),
    "tonga": ("تونغا", "🇹🇴"),
    "vanuatu": ("فانواتو", "🇻🇺"),
    "trinidad and tobago": ("ترينيداد وتوباغو", "🇹🇹"),
    "trinidadandtobago": ("ترينيداد وتوباغو", "🇹🇹"),
    "tit": ("ترينيداد وتوباغو", "🇹🇹"),
    "dominicana": ("الدومينيكان", "🇩🇴"),
    "solomon islands": ("جزر سليمان", "🇸🇧"),
    "solomonislands": ("جزر سليمان", "🇸🇧"),
    "saint vincent and the grenadines": ("سانت فنسنت والغرينادين", "🇻🇨"),
    "saintvincentandgrenadines": ("سانت فنسنت والغرينادين", "🇻🇨"),
    "sierra leone": ("سيراليون", "🇸🇱"),
    "sierraleone": ("سيراليون", "🇸🇱"),
    "east timor": ("تيمور الشرقية", "🇹🇱"),
    "easttimor": ("تيمور الشرقية", "🇹🇱"),
    "bih": ("البوسنة والهرسك", "🇧🇦"),
}

# الخريطة الاحتياطية القياسية لكامل أرقام HeroSMS / SMS-Activate (0 - 100)
HEROSMS_FALLBACK_COUNTRIES: dict[str, str] = {
    "0": "Russia",
    "1": "Ukraine",
    "2": "Kazakhstan",
    "3": "China",
    "4": "Philippines",
    "5": "Myanmar",
    "6": "Indonesia",
    "7": "Malaysia",
    "8": "Kenya",
    "9": "Tanzania",
    "10": "Vietnam",
    "11": "Kyrgyzstan",
    "12": "USA virtual",
    "13": "Israel",
    "14": "HongKong",
    "15": "Poland",
    "16": "England",
    "17": "Madagascar",
    "18": "DCongo",
    "19": "Nigeria",
    "20": "Macao",
    "21": "Egypt",
    "22": "India",
    "23": "Ireland",
    "24": "Cambodia",
    "25": "Laos",
    "26": "Haiti",
    "27": "Ivory",
    "28": "Gambia",
    "29": "Serbia",
    "30": "Yemen",
    "31": "SouthAfrica",
    "32": "Romania",
    "33": "Colombia",
    "34": "Estonia",
    "35": "Azerbaijan",
    "36": "Canada",
    "37": "Morocco",
    "38": "Ghana",
    "39": "Argentina",
    "40": "Uzbekistan",
    "41": "Cameroon",
    "42": "Chad",
    "43": "Germany",
    "44": "Lithuania",
    "45": "Croatia",
    "46": "Sweden",
    "47": "Iraq",
    "48": "Netherlands",
    "49": "Latvia",
    "50": "Austria",
    "51": "Belarus",
    "52": "Thailand",
    "53": "SaudiArabia",
    "54": "Mexico",
    "55": "Taiwan",
    "56": "Spain",
    "57": "Iran",
    "58": "Algeria",
    "60": "Bangladesh",
    "62": "Senegal",
    "64": "SriLanka",
    "69": "Mali",
    "70": "Venezuela",
    "71": "Ethiopia",
    "72": "Mongolia",
    "73": "Brazil",
    "74": "Afghanistan",
    "75": "Uganda",
    "76": "Angola",
    "77": "Cyprus",
    "78": "France",
    "79": "Papua",
    "80": "Mozambique",
    "81": "Nepal",
    "82": "Belgium",
    "83": "Bulgaria",
    "84": "Hungary",
    "85": "Moldova",
    "86": "Italy",
    "87": "Paraguay",
    "88": "Honduras",
    "89": "Tunisia",
    "90": "Nicaragua",
    "91": "TimorLeste",
    "92": "Bolivia",
    "93": "CostaRica",
    "94": "Guatemala",
    "95": "UAE",
    "96": "Zimbabwe",
    "97": "PuertoRico",
    "98": "Sudan",
    "99": "Togo",
    "100": "Kuwait",
}

DEFAULT_SERVICE_CODES: dict[str, str] = {
    "whatsapp": "wa",
    "telegram": "tg",
}


def _slugify(name: str) -> str:
    """تحويل الاسم إلى معرّف آمن بدون مسافات."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "country"


def _deaccent(text: str) -> str:
    """تنظيف الحروف اللاتينية من علامات النطق."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _is_arabic(text: str | None) -> bool:
    if not text:
        return False
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


def _label_for(english_name: str) -> tuple[str, str]:
    """مطابقة الاسم الإنجليزي بالاسم العربي الدقيق والعلم."""
    key = _deaccent(english_name.strip().lower()).replace("-", " ").replace(".", "").replace("'", " ")
    key = re.sub(r"\s+", " ", key).strip()
    
    # 1. مطابقة مباشرة
    direct = COUNTRY_LABELS.get(key)
    if direct:
        return direct
        
    # 2. مطابقة بدون مسافات
    compact = COUNTRY_LABELS.get(key.replace(" ", ""))
    if compact:
        return compact
        
    # 3. مطابقة بحذف الأقواس (مثال: USA (virtual) -> usa)
    without_parens = re.sub(r"\s*\(.*?\)\s*", " ", key).strip()
    without_parens = re.sub(r"\s+", " ", without_parens)
    stripped = COUNTRY_LABELS.get(without_parens) or COUNTRY_LABELS.get(without_parens.replace(" ", ""))
    if stripped:
        return stripped
        
    # 4. مطابقة جزئية
    for k, v in COUNTRY_LABELS.items():
        if k in key or key in k:
            return v

    return (english_name, "🌍")


@dataclass
class SyncReport:
    """تقرير إحصائيات عملية السحب."""

    service_codes: list[str]
    catalog_source: str = "getCountries"
    fetched_countries: int = 0
    added: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    activated: int = 0
    skipped_no_stock: int = 0
    failed: int = 0

    def summary(self) -> str:
        lines = [
            "✅ <b>اكتمل سحب الدول من HeroSMS</b>",
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


async def ensure_number_services(session) -> dict[str, NumberService]:
    """يضمن وجود خدمات واتساب وتيليجرام وربط كود herosms الصحيح."""
    services = {}
    for code, herosms_code in DEFAULT_SERVICE_CODES.items():
        result = await session.execute(
            select(NumberService).where(NumberService.code == code)
        )
        svc = result.scalar_one_or_none()
        if svc is None:
            emoji = "💬" if code == "whatsapp" else "✈️"
            name = "واتساب" if code == "whatsapp" else "تيليجرام"
            svc = NumberService(
                code=code,
                name_ar=name,
                emoji=emoji,
                herosms_code=herosms_code,
                is_active=True,
                sort_order=1 if code == "whatsapp" else 2,
            )
            session.add(svc)
            await session.flush()
        else:
            svc.herosms_code = herosms_code
            svc.is_active = True
        services[code] = svc
    await session.commit()
    return services


async def _fetch_catalog(provider: HeroSMSProvider) -> tuple[list[dict], str]:
    """جلب كتالوج الدول مع استخدام الخريطة الاحتياطية الكاملة إذا لزم الأمر."""
    try:
        countries = await provider.get_countries()
        if countries:
            return countries, "HeroSMS getCountries API"
    except Exception as exc:
        logger.warning(f"فشل getCountries من HeroSMS ({exc}) — جاري استخدام الكتالوج الاحتياطي الكامل")

    return (
        [{"id": cid, "eng": name} for cid, name in HEROSMS_FALLBACK_COUNTRIES.items()],
        "الكتالوج المدمج الشامل (Fallback)",
    )


def _service_has_stock(prices_data: dict, herosms_service_code: str) -> bool:
    """التحقق الدقيق من وجود سعر ومخزون لخدمة معينة بالدولار."""
    if not prices_data:
        return False
    
    # دعم القاموس أو المصفوفة
    node = prices_data
    if isinstance(prices_data, list) and len(prices_data) > 0:
        node = prices_data[0]
    
    if not isinstance(node, dict):
        return False

    service_node = node.get(herosms_service_code)
    if not service_node:
        return False

    if isinstance(service_node, (int, float)):
        return service_node > 0

    if isinstance(service_node, dict):
        if "cost" in service_node:
            count = int(service_node.get("count", 1) or 1)
            return count > 0
        # فحص المشغلين المتعددين
        for op in service_node.values():
            if isinstance(op, dict):
                count = int(op.get("count", 0) or 0)
                if count > 0:
                    return True
            elif isinstance(op, (int, float)) and op > 0:
                return True

    return False


async def sync_herosms_countries(
    session,
    wanted_services: list[str] | None = None,
    activate: bool = True,
    provider=None,
    concurrency: int = 5,
) -> SyncReport:
    """السحب والمزامنة الشاملة لجميع الدول مع HeroSMS."""
    wanted_services = wanted_services or list(DEFAULT_SERVICE_CODES)
    services = await ensure_number_services(session)

    report = SyncReport(service_codes=wanted_services)
    provider = provider or HeroSMSProvider()

    # 1. جلب الكتالوج (من API أو الكتالوج الاحتياطي)
    catalog, source = await _fetch_catalog(provider)
    report.catalog_source = source
    if not catalog:
        return report

    report.fetched_countries = len(catalog)

    # 2. فحص توفر المخزون بالتوازي
    semaphore = asyncio.Semaphore(concurrency)
    availability: dict[str, bool] = {}
    english_names: dict[str, str] = {}

    async def _probe_country(entry: dict):
        cid = str(entry.get("id", "")).strip()
        eng = str(entry.get("eng") or f"HeroSMS {cid}").strip()
        if not cid:
            return
        english_names[cid] = eng
        async with semaphore:
            try:
                prices_raw = await provider.get_country_prices(cid)
                has_any_stock = False
                country_data = prices_raw.get(cid, prices_raw) if isinstance(prices_raw, dict) else {}
                for s_code in wanted_services:
                    h_code = services[s_code].herosms_code
                    if not _service_has_stock(country_data, h_code):
                        continue
                    # تحقق ثانٍ بالمخزون الحي: getPrices قد يكذب
                    # (مخزون وهمي) بينما getNumbersStatus يكشف النفاد.
                    stock_fn = getattr(provider, "get_stock_count", None)
                    if callable(stock_fn):
                        try:
                            live = await stock_fn(cid, h_code)
                        except Exception:
                            live = None
                        if live is not None and live <= 0:
                            continue
                    has_any_stock = True
                    break
                availability[cid] = has_any_stock
            except Exception:
                availability[cid] = False

    await asyncio.gather(*(_probe_country(c) for c in catalog))

    # 3. حفظ وتحديث الدول في قاعدة البيانات
    for cid, eng in english_names.items():
        try:
            has_stock = availability.get(cid, False)
            name_ar, flag = _label_for(eng)
            slug = _slugify(eng)

            # البحث عن دولة موجودة بنفس كود HeroSMS حصراً.
            # ممنوع الدمج العابر للمزودين: كل مزود له صفوفه الخاصة حتى لو
            # تكررت الدولة — حذف الكل وإعادة السحب لا يخلط الأكواد أبداً.
            res = await session.execute(
                select(Country).where(Country.herosms_code == cid)
            )
            country = res.scalar_one_or_none()

            if country is not None:
                changed = False
                if not country.name_ar or country.name_ar.startswith("HeroSMS") or not _is_arabic(country.name_ar):
                    country.name_ar = name_ar
                    changed = True
                if country.flag in (None, "", "🌍") and flag != "🌍":
                    country.flag = flag
                    changed = True
                if activate and has_stock and not country.is_active:
                    country.is_active = True
                    report.activated += 1
                    changed = True
                if changed:
                    report.updated.append(f"{country.flag} {country.name_ar}")
                continue

            # دولة جديدة: معرف فريد باسم المزود لمنع أي التباس
            final_slug = f"herosms_{slug}_{cid}"
            is_active = bool(activate and has_stock)
            new_country = Country(
                code=final_slug,
                name_ar=name_ar,
                flag=flag,
                herosms_code=cid,
                is_active=is_active,
            )
            session.add(new_country)
            report.added.append(f"{flag} {name_ar}")
            if is_active:
                report.activated += 1
            else:
                report.skipped_no_stock += 1

        except Exception as e:
            report.failed += 1
            logger.error(f"خطأ سحب دولة #{cid}: {e}")

    await session.commit()
    return report