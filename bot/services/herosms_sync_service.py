"""
خدمة سحب الدول المتاحة من مزود HeroSMS تلقائياً.

الفكرة:
- بدلاً من إضافة كل دولة يدوياً (7 خطوات لكل دولة)، تسحب هذه الخدمة
  كتالوج الدول من HeroSMS مباشرة، تتحقق من توفر الخدمات المطلوبة
  (واتساب/تيليجرام) عبر الأسعار والمخزون، ثم تنشئ الدول في قاعدة
  البيانات بأكواد HeroSMS الصحيحة.

الاستخدام من لوحة الأدمن:
   زر «🔄 سحب دول من HeroSMS» داخل إدارة الدول.

آلية العمل:
1) جلب كتالوج الدول عبر action=getCountries (يدعمه نمط SMS-Activate).
2) لكل دولة: طلب أسعار واحد action=getPrices&country={id} يرجع كل
   الخدمات المتاحة بتكلفتها ومخزونها.
3) إنشاء/تحديث سجل Country مع herosms_code فقط دون المساس بأكواد
   المزودين الآخرين (5sim/SMS-Activate/SMSHub).
4) تفعيل الدول التي فيها مخزون للخدمة المطلوبة (دون تعطيل أي دولة قائمة).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select

from database.models import Country, NumberService
from providers.herosms import HeroSMSProvider

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# ══════════════ خرائط الترجمة والأعلام ══════════════
# ══════════════════════════════════════════════════════════════

# الاسم الإنجليزي (بأحرف صغيرة) → (الاسم العربي، العلم)
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
    "usa virtual": ("أمريكا (افتراضي)", "🇺🇸"),
    "usavirtual": ("أمريكا (افتراضي)", "🇺🇸"),
    "usa physical": ("أمريكا (حقيقي)", "🇺🇸"),
    "israel": ("إسرائيل", "🇮🇱"),
    "hongkong": ("هونغ كونغ", "🇭🇰"),
    "hong kong": ("هونغ كونغ", "🇭🇰"),
    "poland": ("بولندا", "🇵🇱"),
    "england": ("بريطانيا", "🇬🇧"),
    "united kingdom": ("بريطانيا", "🇬🇧"),
    "uk": ("بريطانيا", "🇬🇧"),
    "madagascar": ("مدغشقر", "🇲🇬"),
    "congo": ("الكونغو", "🇨🇩"),
    "dcongo": ("الكونغو الديمقراطية", "🇨🇩"),
    "dr congo": ("الكونغو الديمقراطية", "🇨🇩"),
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
    "bangladesh": ("بنغلاديش", "🇧🇩"),
    "senegal": ("السنغال", "🇸🇳"),
    "turkey": ("تركيا", "🇹🇷"),
    "turkiye": ("تركيا", "🇹🇷"),
    "sri lanka": ("سريلانكا", "🇱🇰"),
    "srilanka": ("سريلانكا", "🇱🇰"),
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
    "mozambique": ("موزمبيق", "🇲🇿"),
    "nepal": ("نيبال", "🇳🇵"),
    "belgium": ("بلجيكا", "🇧🇪"),
    "bulgaria": ("بلغاريا", "🇧🇬"),
    "hungary": ("هنغاريا", "🇭🇺"),
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
    "slovenia republic": ("سلوفينيا", "🇸🇮"),
    "albania": ("ألبانيا", "🇦🇱"),
    "armenia": ("أرمينيا", "🇦🇲"),
    "jordan": ("الأردن", "🇯🇴"),
    "lebanon": ("لبنان", "🇱🇧"),
    "pakistan": ("باكستان", "🇵🇰"),
    "turkmenistan": ("تركمانستان", "🇹🇲"),
    "tajikistan": ("طاجيكستان", "🇹🇯"),
    "somalia": ("الصومال", "🇸🇴"),
    "burundi": ("بوروندي", "🇧🇮"),
    "benin": ("بنين", "🇧🇯"),
    "gabon": ("الغابون", "🇬🇦"),
    "guinea": ("غينيا", "🇬🇳"),
    "zambia": ("زامبيا", "🇿🇲"),
    "botswana": ("بوتسوانا", "🇧🇼"),
    "korea": ("كوريا الجنوبية", "🇰🇷"),
    "south korea": ("كوريا الجنوبية", "🇰🇷"),
    "north korea": ("كوريا الشمالية", "🇰🇵"),
    "portugal": ("البرتغال", "🇵🇹"),
    "luxembourg": ("لوكسمبورغ", "🇱🇺"),
    "malta": ("مالطا", "🇲🇹"),
    "singapore": ("سنغافورة", "🇸🇬"),
    "papua new guinea": ("بابوا غينيا الجديدة", "🇵🇬"),
    "papua": ("بابوا غينيا الجديدة", "🇵🇬"),
    "papuanewguinea": ("بابوا غينيا الجديدة", "🇵🇬"),
    "congo republic": ("الكونغو", "🇨🇬"),
    "congo brazzaville": ("الكونغو", "🇨🇬"),
    "republic of the congo": ("الكونغو", "🇨🇬"),
    "democratic republic of the congo": ("الكونغو الديمقراطية", "🇨🇩"),
    "dr congo": ("الكونغو الديمقراطية", "🇨🇩"),
    "ivory coast": ("ساحل العاج", "🇨🇮"),
    "cote d ivoire": ("ساحل العاج", "🇨🇮"),
    "cote divoire": ("ساحل العاج", "🇨🇮"),
    "united states of america": ("أمريكا", "🇺🇸"),
    "unitedstatesofamerica": ("أمريكا", "🇺🇸"),
    "great britain": ("بريطانيا", "🇬🇧"),
    "britain": ("بريطانيا", "🇬🇧"),
    "türkiye": ("تركيا", "🇹🇷"),
    "turkey republic": ("تركيا", "🇹🇷"),
    "russian federation": ("روسيا", "🇷🇺"),
    "russianfederation": ("روسيا", "🇷🇺"),
    "korea republic": ("كوريا الجنوبية", "🇰🇷"),
    "korea rep": ("كوريا الجنوبية", "🇰🇷"),
    "uae dubai": ("الإمارات", "🇦🇪"),
    "emirates": ("الإمارات", "🇦🇪"),
    "viet nam": ("فيتنام", "🇻🇳"),
    "vietnam": ("فيتنام", "🇻🇳"),
    "czechia": ("التشيك", "🇨🇿"),
    "kyrgyz republic": ("قرغيزستان", "🇰🇬"),
    "kyrgyzrepublic": ("قرغيزستان", "🇰🇬"),
    "portugal": ("البرتغال", "🇵🇹"),
    "luxembourg": ("لوكسمبورغ", "🇱🇺"),
    "malta": ("مالطا", "🇲🇹"),
    "singapore": ("سنغافورة", "🇸🇬"),
    "korea south": ("كوريا الجنوبية", "🇰🇷"),
}

# خريطة احتياطية بأرقام الدول القياسية في منظومة SMS-Activate.
# تُستخدم فقط إذا فشل getCountries من المزود. أسماء الدول تظل قابلة
# للتعديل من لوحة الأدمن لاحقاً، والشراء يعتمد على الرقم لا الاسم.
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

# الخدمات المدعومة افتراضياً بأكواد HeroSMS
DEFAULT_SERVICE_CODES: dict[str, str] = {
    "whatsapp": "wa",
    "telegram": "tg",
}


def _slugify(name: str) -> str:
    """يحوّل الاسم الإنجليزي إلى معرّف داخلي آمن."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "country"


def _deaccent(text: str) -> str:
    """يحوّل الحروف اللاتينية الممدودة إلى أساسية (Côte d'Ivoire → Cote d'Ivoire)."""
    import unicodedata

    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _is_arabic(text: str | None) -> bool:
    """هل النص يحوي حروفاً عربية؟"""
    if not text:
        return False
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


def _label_for(english_name: str) -> tuple[str, str]:
    """يرجع (الاسم العربي، العلم) لاسم إنجليزي، مع بديل آمن."""
    key = _deaccent(english_name.strip().lower()).replace("-", " ").replace(".", "").replace("'", " ")
    key = re.sub(r"\s+", " ", key).strip()
    direct = COUNTRY_LABELS.get(key)
    if direct:
        return direct
    compact = COUNTRY_LABELS.get(key.replace(" ", ""))
    if compact:
        return compact
    without_parens = re.sub(r"\s*\(.*?\)\s*", " ", key).strip()
    without_parens = re.sub(r"\s+", " ", without_parens)
    stripped = COUNTRY_LABELS.get(without_parens) or COUNTRY_LABELS.get(
        without_parens.replace(" ", "")
    )
    if stripped:
        return stripped
    return (english_name, "🌍")


@dataclass
class SyncReport:
    """تقرير عملية السحب."""

    service_codes: list[str]
    catalog_source: str = "getCountries"
    fetched_countries: int = 0
    added: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)      # دولة قائمة أُضيف كودها
    updated: list[str] = field(default_factory=list)     # كانت موجودة بنفس الكود
    activated: int = 0
    skipped_no_stock: int = 0
    failed: int = 0

    def summary(self) -> str:
        lines = [
            "✅ <b>اكتمل سحب الدول من HeroSMS</b>",
            "",
            f"🔌 مصدر الكتالوج: <code>{self.catalog_source}</code>",
            f"🌐 عدد الدول المفحوصة: <b>{self.fetched_countries}</b>",
            f"🆕 دول جديدة: <b>{len(self.added)}</b>",
            f"🔗 دمج مع دول قائمة: <b>{len(self.merged)}</b>",
            f"♻️ محدّثة (بنفس الكود): <b>{len(self.updated)}</b>",
            f"🟢 فُعّلت تلقائياً: <b>{self.activated}</b>",
            f"⚪ بدون مخزون (لم تُفعّل): <b>{self.skipped_no_stock}</b>",
            f"❌ فشل فحصها: <b>{self.failed}</b>",
        ]
        if self.added:
            shown = self.added[:60]
            lines.append("")
            lines.append("<b>الدول الجديدة:</b>")
            lines.extend(f"• {name}" for name in shown)
            if len(self.added) > len(shown):
                lines.append(f"• ... و {len(self.added) - len(shown)} دولة أخرى")
        lines.append("")
        lines.append("💡 راجع القائمة من «إدارة الدول» ويمكنك تعديل أي اسم أو علم.")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# ══════════════ ضمان وجود الخدمات ══════════════
# ══════════════════════════════════════════════════════════════


async def ensure_number_services(session) -> dict[str, NumberService]:
    """يضمن وجود خدمات واتساب/تيليجرام مع أكواد HeroSMS.

    البذور الافتراضية تنشئها، لكن قواعد بيانات قديمة قد تخلو من
    أكواد herosms — هنا نملأها دون إنشاء تكرارات.
    """
    services: dict[str, NumberService] = {}
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
        elif not svc.herosms_code:
            svc.herosms_code = herosms_code
        services[code] = svc
    await session.commit()
    return services


# ══════════════════════════════════════════════════════════════
# ══════════════ السحب الرئيسي ══════════════
# ══════════════════════════════════════════════════════════════


async def _fetch_catalog(provider) -> tuple[list[dict], str]:
    """يجلب كتالوج الدول من المزود مع بديل الخريطة الاحتياطية.

    كل عنصر: {"id": str, "eng": str}
    يرجع (الكتالوج، مصدره: getCountries أو fallback).
    """
    try:
        countries = await provider.get_countries()
        if countries:
            return countries, "getCountries"
    except Exception as exc:  # noqa: BLE001 - نتحول للمسار الاحتياطي
        logger.warning("فشل getCountries من HeroSMS (%s) — استخدام الخريطة الاحتياطية", exc)

    return (
        [{"id": cid, "eng": name} for cid, name in HEROSMS_FALLBACK_COUNTRIES.items()],
        "الخريطة الاحتياطية",
    )


def _normalize_prices(raw: dict, country_id: str) -> dict[str, dict]:
    """يوحّد شكل استجابة getPrices إلى {خدمة: {cost: Decimal, count: int}}.

    المنصات المستنسخة من SMS-Activate تختلف قليلاً:
    - {country: {service: {cost, count}}}
    - {service: {cost, count}}
    - {service: {operator: {cost, count}}}
    """
    node = raw.get(country_id, raw) if isinstance(raw, dict) else {}
    if not isinstance(node, dict):
        return {}

    normalized: dict[str, dict] = {}
    for service, payload in node.items():
        if not isinstance(payload, dict):
            continue
        cost = payload.get("cost")
        count = payload.get("count", 0)
        if cost is None:
            # شكل المشغلين المتداخل: نأول أرخص مشغل
            operator_costs = [
                (op.get("cost"), op.get("count", 0))
                for op in payload.values()
                if isinstance(op, dict) and op.get("cost") is not None
            ]
            if not operator_costs:
                continue
            cheapest = min(
                operator_costs, key=lambda item: Decimal(str(item[0] or 0))
            )
            cost, count = cheapest
        try:
            normalized[str(service)] = {
                "cost": Decimal(str(cost)),
                "count": int(count or 0),
            }
        except Exception:  # noqa: BLE001 - قيمة تالفة لا تُسقط البقية
            continue
    return normalized


def _service_available(prices: dict[str, dict], herosms_service_code: str) -> bool:
    """الخدمة متاحة إذا كان لها سعر وعداد مخزون غير صفري (إن وُجد العداد)."""
    info = prices.get(herosms_service_code)
    if info is None or info.get("cost") is None:
        return False
    count = info.get("count", 0)
    return count > 0


async def sync_herosms_countries(
    session,
    wanted_services: list[str] | None = None,
    activate: bool = True,
    provider=None,
    concurrency: int = 5,
) -> SyncReport:
    """يسحب الدول المتاحة من HeroSMS وينشئها في قاعدة البيانات.

    Args:
        session: جلسة قاعدة البيانات.
        wanted_services: أكواد الخدمات الداخلية المطلوبة
            (مثل ["whatsapp", "telegram"]). الافتراضي كلاهما.
        activate: تفعيل الدول التي فيها مخزون تلقائياً (لا يعطّل شيئاً أبداً).
        provider: مزود بديل للاختبارات.
        concurrency: عدد الطلبات المتوازية نحو HeroSMS.

    Returns:
        SyncReport للعرض على الأدمن.
    """
    wanted_services = wanted_services or list(DEFAULT_SERVICE_CODES)
    services = await ensure_number_services(session)

    report = SyncReport(service_codes=wanted_services)
    provider = provider or HeroSMSProvider()

    # ── 1) كتالوج الدول ──
    catalog, catalog_source = await _fetch_catalog(provider)
    report.catalog_source = catalog_source
    if not catalog:
        report.catalog_source = "لا شيء"
        return report
    report.fetched_countries = len(catalog)

    # ── 2) فحص الأسعار والمخزون لكل دولة (بتوازٍ محدود) ──
    semaphore = asyncio.Semaphore(max(1, concurrency))
    availability: dict[str, dict[str, bool]] = {}
    english_names: dict[str, str] = {}

    async def _probe(entry: dict) -> None:
        cid = str(entry.get("id", "")).strip()
        eng = str(entry.get("eng") or "").strip() or f"HeroSMS {cid}"
        if not cid:
            return
        async with semaphore:
            try:
                raw = await provider.get_country_prices(cid)
                prices = _normalize_prices(raw if isinstance(raw, dict) else {}, cid)
            except Exception:  # noqa: BLE001 - دولة فاشلة لا توقف البقية
                prices = {}
        if not prices:
            return
        english_names[cid] = eng
        availability[cid] = {
            code: _service_available(prices, services[code].herosms_code or "")
            for code in wanted_services
            if code in services
        }

    await asyncio.gather(*(_probe(entry) for entry in catalog))

    # ── 3) إنشاء/تحديث الدول ──
    for cid, eng in english_names.items():
        try:
            stock_flags = availability.get(cid, {})
            has_stock = any(stock_flags.values())

            existing_by_code = await session.execute(
                select(Country).where(Country.herosms_code == cid)
            )
            country = existing_by_code.scalar_one_or_none()

            name_ar, flag = _label_for(eng)
            slug = _slugify(eng)

            if country is not None:
                # موجودة بنفس كود HeroSMS: نكمل الناقص فقط
                changed = False
                if not country.name_ar or country.name_ar.startswith("HeroSMS"):
                    country.name_ar = name_ar
                    changed = True
                elif not _is_arabic(country.name_ar) and _is_arabic(name_ar):
                    # اسم قائم بحروف لاتينية ولدينا ترجمة عربية → نعرّبه
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

            # دمج مع دولة قائمة أُضيفت سابقاً بمزود آخر (بدون كود herosms)
            existing_by_slug = await session.execute(
                select(Country).where(Country.code == slug)
            )
            country = existing_by_slug.scalar_one_or_none()
            if country is not None and not country.herosms_code:
                country.herosms_code = cid
                if activate and has_stock and not country.is_active:
                    country.is_active = True
                    report.activated += 1
                report.merged.append(f"{country.flag} {country.name_ar}")
                continue

            if country is not None and country.herosms_code:
                # نفس المعرّف الداخلي مستخدم لدولة HeroSMS أخرى → لاحقة مميزة
                slug = f"{slug}_hs{cid}"

            is_new_active = bool(activate and has_stock)
            new_country = Country(
                code=slug,
                name_ar=name_ar,
                flag=flag,
                herosms_code=cid,
                is_active=is_new_active,
            )
            session.add(new_country)
            await session.flush()
            report.added.append(
                f"{flag} {name_ar}"
                + ("" if is_new_active else " (⚪ بدون مخزون)")
            )
            if is_new_active:
                report.activated += 1
            if not has_stock:
                report.skipped_no_stock += 1
        except Exception:  # noqa: BLE001 - دولة واحدة لا توقف العملية
            report.failed += 1
            logger.exception("فشل تجهيز دولة HeroSMS #%s", cid)

    await session.commit()
    return report
