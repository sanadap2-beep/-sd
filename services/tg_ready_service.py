"""مخزون الجلسات الجاهزة (أرقام تلجرام الجاهزة) — فرز تلقائي من ملف.

الأدمن يرفع ملف واحد (txt / csv / zip بأسماء ملفات تحوي أرقاماً)، والبوت:
1) يستخرج كل الأرقام، 2) يتعرف على الدولة من مقدمة الرقم (الاسم + العلم)،
3) يحسب سعر البيع = التكلفة + نسبة الربح، 4) يجمّع الدول تلقائياً كمخزون جاهز.
عند كل عملية شراء ينقص المخزون تلقائياً (عدّ العناصر المتاحة live).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_UP
from pathlib import Path

from sqlalchemy import func, select, update

PHONE_RE = re.compile(r"\+?(\d{7,15})")

# مقدمة الاتصال -> (الاسم العربي، العلم)
# مرتبة للبحث عن أطول مطابقة أولاً.
DIAL_MAP: dict[str, tuple[str, str]] = {
    "1": ("أمريكا/كندا", "🇺🇸"),
    "7": ("روسيا/كازاخستان", "🇷🇺"),
    "20": ("مصر", "🇪🇬"),
    "27": ("جنوب أفريقيا", "🇿🇦"),
    "30": ("اليونان", "🇬🇷"),
    "31": ("هولندا", "🇳🇱"),
    "32": ("بلجيكا", "🇧🇪"),
    "33": ("فرنسا", "🇫🇷"),
    "34": ("إسبانيا", "🇪🇸"),
    "39": ("إيطاليا", "🇮🇹"),
    "40": ("رومانيا", "🇷🇴"),
    "41": ("سويسرا", "🇨🇭"),
    "43": ("النمسا", "🇦🇹"),
    "44": ("بريطانيا", "🇬🇧"),
    "45": ("الدنمارك", "🇩🇰"),
    "46": ("السويد", "🇸🇪"),
    "47": ("النرويج", "🇳🇴"),
    "48": ("بولندا", "🇵🇱"),
    "49": ("ألمانيا", "🇩🇪"),
    "51": ("بيرو", "🇵🇪"),
    "52": ("المكسيك", "🇲🇽"),
    "53": ("كوبا", "🇨🇺"),
    "54": ("الأرجنتين", "🇦🇷"),
    "55": ("البرازيل", "🇧🇷"),
    "56": ("تشيلي", "🇨🇱"),
    "57": ("كولومبيا", "🇨🇴"),
    "58": ("فنزويلا", "🇻🇪"),
    "60": ("ماليزيا", "🇲🇾"),
    "61": ("أستراليا", "🇦🇺"),
    "62": ("إندونيسيا", "🇮🇩"),
    "63": ("الفلبين", "🇵🇭"),
    "64": ("نيوزيلندا", "🇳🇿"),
    "65": ("سنغافورة", "🇸🇬"),
    "66": ("تايلاند", "🇹🇭"),
    "81": ("اليابان", "🇯🇵"),
    "82": ("كوريا الجنوبية", "🇰🇷"),
    "84": ("فيتنام", "🇻🇳"),
    "86": ("الصين", "🇨🇳"),
    "90": ("تركيا", "🇹🇷"),
    "91": ("الهند", "🇮🇳"),
    "92": ("باكستان", "🇵🇰"),
    "93": ("أفغانستان", "🇦🇫"),
    "94": ("سريلانكا", "🇱🇰"),
    "95": ("ميانمار", "🇲🇲"),
    "98": ("إيران", "🇮🇷"),
    "211": ("جنوب السودان", "🇸🇸"),
    "212": ("المغرب", "🇲🇦"),
    "213": ("الجزائر", "🇩🇿"),
    "216": ("تونس", "🇹🇳"),
    "218": ("ليبيا", "🇱🇾"),
    "220": ("غامبيا", "🇬🇲"),
    "221": ("السنغال", "🇸🇳"),
    "222": ("موريتانيا", "🇲🇷"),
    "223": ("مالي", "🇲🇱"),
    "224": ("غينيا", "🇬🇳"),
    "225": ("ساحل العاج", "🇨🇮"),
    "226": ("بوركينا فاسو", "🇧🇫"),
    "227": ("النيجر", "🇳🇪"),
    "228": ("توغو", "🇹🇬"),
    "229": ("بنين", "🇧🇯"),
    "230": ("موريشيوس", "🇲🇺"),
    "231": ("ليبيريا", "🇱🇷"),
    "232": ("سيراليون", "🇸🇱"),
    "233": ("غانا", "🇬🇭"),
    "234": ("نيجيريا", "🇳🇬"),
    "235": ("تشاد", "🇹🇩"),
    "236": ("أفريقيا الوسطى", "🇨🇫"),
    "237": ("الكاميرون", "🇨🇲"),
    "238": ("الرأس الأخضر", "🇨🇻"),
    "239": ("ساو تومي", "🇸🇹"),
    "240": ("غينيا الاستوائية", "🇬🇶"),
    "241": ("الغابون", "🇬🇦"),
    "242": ("الكونغو", "🇨🇬"),
    "243": ("الكونغو الديمقراطية", "🇨🇩"),
    "244": ("أنغولا", "🇦🇴"),
    "245": ("غينيا بيساو", "🇬🇼"),
    "246": ("دييغو غارسيا", "🇮🇴"),
    "248": ("سيشيل", "🇸🇨"),
    "249": ("السودان", "🇸🇩"),
    "250": ("رواندا", "🇷🇼"),
    "251": ("إثيوبيا", "🇪🇹"),
    "252": ("الصومال", "🇸🇴"),
    "253": ("جيبوتي", "🇩🇯"),
    "254": ("كينيا", "🇰🇪"),
    "255": ("تنزانيا", "🇹🇿"),
    "256": ("أوغندا", "🇺🇬"),
    "257": ("بوروندي", "🇧🇮"),
    "258": ("موزمبيق", "🇲🇿"),
    "260": ("زامبيا", "🇿🇲"),
    "261": ("مدغشقر", "🇲🇬"),
    "262": ("ريونيون", "🇷🇪"),
    "263": ("زيمبابوي", "🇿🇼"),
    "264": ("ناميبيا", "🇳🇦"),
    "265": ("مالاوي", "🇲🇼"),
    "266": ("ليسوتو", "🇱🇸"),
    "267": ("بوتسوانا", "🇧🇼"),
    "268": ("إسواتيني", "🇸🇿"),
    "269": ("جزر القمر", "🇰🇲"),
    "290": ("سانت هيلانة", "🇸🇭"),
    "291": ("إريتريا", "🇪🇷"),
    "297": ("أروبا", "🇦🇼"),
    "298": ("جزر فارو", "🇫🇴"),
    "299": ("غرينلاند", "🇬🇱"),
    "350": ("جبل طارق", "🇬🇮"),
    "351": ("البرتغال", "🇵🇹"),
    "352": ("لوكسمبورغ", "🇱🇺"),
    "353": ("إيرلندا", "🇮🇪"),
    "354": ("آيسلندا", "🇮🇸"),
    "355": ("ألبانيا", "🇦🇱"),
    "356": ("مالطا", "🇲🇹"),
    "357": ("قبرص", "🇨🇾"),
    "358": ("فنلندا", "🇫🇮"),
    "359": ("بلغاريا", "🇧🇬"),
    "370": ("ليتوانيا", "🇱🇹"),
    "371": ("لاتفيا", "🇱🇻"),
    "372": ("إستونيا", "🇪🇪"),
    "373": ("مولدوفا", "🇲🇩"),
    "374": ("أرمينيا", "🇦🇲"),
    "375": ("بيلاروسيا", "🇧🇾"),
    "376": ("أندورا", "🇦🇩"),
    "377": ("موناكو", "🇲🇨"),
    "378": ("سان مارينو", "🇸🇲"),
    "380": ("أوكرانيا", "🇺🇦"),
    "381": ("صربيا", "🇷🇸"),
    "382": ("الجبل الأسود", "🇲🇪"),
    "383": ("كوسوفو", "🇽🇰"),
    "384": ("كوسوفو", "🇽🇰"),
    "385": ("كرواتيا", "🇭🇷"),
    "386": ("سلوفينيا", "🇸🇮"),
    "387": ("البوسنة", "🇧🇦"),
    "389": ("مقدونيا", "🇲🇰"),
    "420": ("التشيك", "🇨🇿"),
    "421": ("سلوفاكيا", "🇸🇰"),
    "423": ("ليختنشتاين", "🇱🇮"),
    "502": ("غواتيمالا", "🇬🇹"),
    "503": ("السلفادور", "🇸🇻"),
    "504": ("هندوراس", "🇭🇳"),
    "505": ("نيكاراغوا", "🇳🇮"),
    "506": ("كوستاريكا", "🇨🇷"),
    "507": ("بنما", "🇵🇦"),
    "509": ("هايتي", "🇭🇹"),
    "590": ("غوادلوب", "🇬🇵"),
    "591": ("بوليفيا", "🇧🇴"),
    "592": ("غيانا", "🇬🇾"),
    "593": ("الإكوادور", "🇪🇨"),
    "594": ("غويانا الفرنسية", "🇬🇫"),
    "595": ("باراغواي", "🇵🇾"),
    "596": ("مارتينيك", "🇲🇶"),
    "597": ("سورينام", "🇸🇷"),
    "598": ("أوروغواي", "🇺🇾"),
    "670": ("تيمور الشرقية", "🇹🇱"),
    "672": ("القارة القطبية", "🇦🇶"),
    "673": ("بروناي", "🇧🇳"),
    "674": ("ناورو", "🇳🇷"),
    "675": ("بابوا غينيا", "🇵🇬"),
    "676": ("تونغا", "🇹🇴"),
    "677": ("جزر سليمان", "🇸🇧"),
    "678": ("فانواتو", "🇻🇺"),
    "679": ("فيجي", "🇫🇯"),
    "680": ("بالاو", "🇵🇼"),
    "681": ("واليس وفوتونا", "🇼🇫"),
    "682": ("جزر كوك", "🇨🇰"),
    "683": ("نييوي", "🇳🇺"),
    "685": ("ساموا", "🇼🇸"),
    "686": ("كيريباتي", "🇰🇮"),
    "687": ("كاليدونيا الجديدة", "🇳🇨"),
    "688": ("توفالو", "🇹🇻"),
    "689": ("بولينيزيا الفرنسية", "🇵🇫"),
    "690": ("توكيلاو", "🇹🇰"),
    "691": ("ميكرونيزيا", "🇫🇲"),
    "692": ("جزر مارشال", "🇲🇭"),
    "850": ("كوريا الشمالية", "🇰🇵"),
    "852": ("هونغ كونغ", "🇭🇰"),
    "853": ("ماكاو", "🇲🇴"),
    "855": ("كمبوديا", "🇰🇭"),
    "856": ("لاوس", "🇱🇦"),
    "880": ("بنغلادش", "🇧🇩"),
    "886": ("تايوان", "🇹🇼"),
    "960": ("المالديف", "🇲🇻"),
    "961": ("لبنان", "🇱🇧"),
    "962": ("الأردن", "🇯🇴"),
    "963": ("سوريا", "🇸🇾"),
    "964": ("العراق", "🇮🇶"),
    "965": ("الكويت", "🇰🇼"),
    "966": ("السعودية", "🇸🇦"),
    "967": ("اليمن", "🇾🇪"),
    "968": ("عمان", "🇴🇲"),
    "970": ("فلسطين", "🇵🇸"),
    "971": ("الإمارات", "🇦🇪"),
    "972": ("إسرائيل", "🇮🇱"),
    "973": ("البحرين", "🇧🇭"),
    "974": ("قطر", "🇶🇦"),
    "975": ("بوتان", "🇧🇹"),
    "976": ("منغوليا", "🇲🇳"),
    "977": ("نيبال", "🇳🇵"),
    "992": ("طاجيكستان", "🇹🇯"),
    "993": ("تركمانستان", "🇹🇲"),
    "994": ("أذربيجان", "🇦🇿"),
    "995": ("جورجيا", "🇬🇪"),
    "996": ("قيرغيزستان", "🇰🇬"),
    "998": ("أوزبكستان", "🇺🇿"),
}

_SORTED_PREFIXES = sorted(DIAL_MAP.keys(), key=len, reverse=True)


def detect_country(digits: str) -> tuple[str, str, str]:
    """يتعرف على الدولة من الرقم. يرجع (المفتاح، الاسم، العلم)."""
    d = re.sub(r"\D", "", digits or "").lstrip("0")
    # الأرقام المحلية بدون مقدمة دولية (مثل 09xxxxxxxx بسوريا) لا يمكن تمييزها
    for prefix in _SORTED_PREFIXES:
        if d.startswith(prefix) and len(d) > len(prefix) + 4:
            name, flag = DIAL_MAP[prefix]
            return prefix, name, flag
    return "unknown", "غير معروف", "🌍"


def calc_sell_price(cost: Decimal, margin_percent: Decimal) -> Decimal:
    cost = Decimal(str(cost or 0))
    margin = Decimal(str(margin_percent or 0))
    if cost <= 0:
        return Decimal("0")
    return ((cost * (Decimal("100") + margin)) / Decimal("100")).quantize(
        Decimal("0.0001"), rounding=ROUND_UP
    )


@dataclass
class ParsedEntry:
    phone: str
    payload: str  # السطر الأصلي كاملاً (رقم|سيشن|2FA...)


def _norm_phone(raw: str) -> str | None:
    if not raw:
        return None
    m = PHONE_RE.search(raw.replace(" ", ""))
    if not m:
        return None
    digits = m.group(1)
    if not (7 <= len(digits) <= 15):
        return None
    return "+" + digits


def parse_text_entries(text: str) -> list[ParsedEntry]:
    """يفكك ملف txt/csv: كل سطر فيه رقم (ومعه اختيارياً بيانات الجلسة بعد | أو ; أو ,)."""
    entries: list[ParsedEntry] = []
    seen: set[str] = set()
    # جرّب CSV أولاً إن بدا كجدول
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    if not lines:
        return entries
    # إن كان أول سطر فيه header فيه كلمة phone/number/session نعتبره CSV ونتخطاه
    start = 0
    head = lines[0].lower()
    if any(w in head for w in ("phone", "number", "session", "رقم", "هاتف")) and not PHONE_RE.search(lines[0]):
        start = 1
    for ln in lines[start:]:
        phone = _norm_phone(ln)
        if not phone or phone in seen:
            continue
        seen.add(phone)
        entries.append(ParsedEntry(phone=phone, payload=ln.strip()[:2000]))
    return entries


def parse_csv_entries(raw: bytes) -> list[ParsedEntry]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="ignore")
    # CSV حقيقي بأعمدة؟ جرّب DictReader
    try:
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames and any(
            (fn or "").strip().lower() in ("phone", "number", "phone_number", "tel", "mobile")
            for fn in reader.fieldnames
        ):
            entries: list[ParsedEntry] = []
            seen: set[str] = set()
            for row in reader:
                blob = "|".join((v or "") for v in row.values())
                phone = _norm_phone(blob)
                if not phone or phone in seen:
                    continue
                seen.add(phone)
                entries.append(ParsedEntry(phone=phone, payload=blob.strip()[:2000]))
            if entries:
                return entries
    except Exception:
        pass
    return parse_text_entries(text)


def parse_zip_entries(raw: bytes) -> list[ParsedEntry]:
    """ملف ZIP لشغل الجلسات: أسماء الملفات/المجلدات تحوي الأرقام (tdata/session)."""
    entries: list[ParsedEntry] = []
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # اسم الملف أو المجلد الأب قد يحوي الرقم
                phone = _norm_phone(info.filename)
                if not phone:
                    # جرّب قراءة أول سطرين من الملفات النصية الصغيرة (json/txt)
                    if info.file_size < 20000 and info.filename.lower().endswith(
                        (".txt", ".json", ".session")
                    ):
                        try:
                            head = zf.read(info.filename)[:4000].decode("utf-8", errors="ignore")
                            phone = _norm_phone(head)
                        except Exception:
                            phone = None
                if not phone or phone in seen:
                    continue
                seen.add(phone)
                entries.append(
                    ParsedEntry(phone=phone, payload=f"{phone} | ملف: {info.filename}"[:500])
                )
    except zipfile.BadZipFile:
        return []
    return entries


def parse_uploaded_file(filename: str, raw: bytes) -> list[ParsedEntry]:
    name = (filename or "").lower()
    if name.endswith(".zip"):
        items = parse_zip_entries(raw)
        if items:
            return items
    if name.endswith(".csv"):
        return parse_csv_entries(raw)
    try:
        return parse_text_entries(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return parse_text_entries(raw.decode("utf-8", errors="ignore"))


class TgReadyService:
    MARGIN_KEY = "tg_ready_margin_percent"

    @staticmethod
    async def get_margin(session, default: Decimal = Decimal("50")) -> Decimal:
        from services.settings_service import SettingsService

        try:
            return await SettingsService.get_decimal(TgReadyService.MARGIN_KEY, default)
        except Exception:
            return default

    @staticmethod
    async def set_margin(session, margin: Decimal) -> None:
        from services.settings_service import SettingsService

        await SettingsService.set(session, TgReadyService.MARGIN_KEY, str(margin))

    @staticmethod
    async def stock_overview(session) -> list[dict]:
        """الدول المجمعة تلقائياً + المخزون الحي لكل دولة (متاح فقط)."""
        from database.models import TgReadyCountry, TgReadyItem, TgReadyItemStatus

        rows = (
            await session.execute(
                select(
                    TgReadyItem.country_key,
                    func.count(TgReadyItem.id),
                    func.min(TgReadyItem.price_usd),
                )
                .where(TgReadyItem.status == TgReadyItemStatus.AVAILABLE)
                .group_by(TgReadyItem.country_key)
                .order_by(func.count(TgReadyItem.id).desc())
            )
        ).all()
        countries = {
            c.country_key: c
            for c in (await session.execute(select(TgReadyCountry))).scalars().all()
        }
        out: list[dict] = []
        for key, count, min_price in rows:
            c = countries.get(key)
            if c is not None and not c.is_active:
                continue
            if c is not None:
                name, flag, price = c.name_ar, c.flag, c.price_usd
            else:
                # دولة ظهرت من ملف لكن سجلها غير موجود — اشتق الاسم من أول عنصر
                sample = (
                    await session.execute(
                        select(TgReadyItem)
                        .where(
                            TgReadyItem.country_key == key,
                            TgReadyItem.status == TgReadyItemStatus.AVAILABLE,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                name = sample.country_name_ar if sample else "غير معروف"
                flag = sample.flag if sample else "🌍"
                price = min_price
            out.append(
                {
                    "key": key,
                    "name": name,
                    "flag": flag,
                    "stock": int(count),
                    "price": price,
                }
            )
        return out

    @staticmethod
    async def total_available(session) -> int:
        from database.models import TgReadyItem, TgReadyItemStatus

        return (
            await session.execute(
                select(func.count(TgReadyItem.id)).where(
                    TgReadyItem.status == TgReadyItemStatus.AVAILABLE
                )
            )
        ).scalar_one()

    @staticmethod
    async def import_entries(
        session,
        entries: list[ParsedEntry],
        cost_usd: Decimal,
        margin_percent: Decimal,
        file_name: str = "",
        created_by: int | None = None,
    ) -> dict:
        """يفرز الملف تلقائياً: دولة + علم + سعر لكل مجموعة، ويسجّل المخزون."""
        from database.models import TgReadyBatch, TgReadyCountry, TgReadyItem, TgReadyItemStatus
        from services.encryption_service import EncryptionService

        sell = calc_sell_price(cost_usd, margin_percent)
        batch = TgReadyBatch(
            file_name=file_name or "upload",
            total_count=len(entries),
            cost_usd=cost_usd,
            margin_percent=margin_percent,
            created_by=created_by,
        )
        session.add(batch)
        await session.flush()

        existing_phones = set(
            (
                await session.execute(
                    select(TgReadyItem.phone_number).where(
                        TgReadyItem.phone_number.in_([e.phone for e in entries])
                    )
                )
            )
            .scalars()
            .all()
        )
        added = 0
        dupes = 0
        per_country: dict[str, dict] = {}
        for e in entries:
            if e.phone in existing_phones:
                dupes += 1
                continue
            key, name, flag = detect_country(e.phone)
            try:
                enc = EncryptionService.encrypt(e.payload)
            except Exception:
                enc = None
            session.add(
                TgReadyItem(
                    phone_number=e.phone,
                    country_key=key,
                    country_name_ar=name,
                    flag=flag,
                    cost_usd=cost_usd,
                    price_usd=sell,
                    payload_encrypted=enc,
                    batch_id=batch.id,
                    status=TgReadyItemStatus.AVAILABLE,
                )
            )
            existing_phones.add(e.phone)
            added += 1
            slot = per_country.setdefault(key, {"name": name, "flag": flag, "count": 0})
            slot["count"] += 1

        # إنشاء/تحديث سجل كل دولة تلقائياً بالاسم والعلم والسعر الجديد
        for key, info in per_country.items():
            country = await session.get(TgReadyCountry, key)
            if country is None:
                session.add(
                    TgReadyCountry(
                        country_key=key,
                        name_ar=info["name"],
                        flag=info["flag"],
                        price_usd=sell,
                        last_cost_usd=cost_usd,
                        margin_percent=margin_percent,
                    )
                )
            else:
                country.name_ar = info["name"]
                country.flag = info["flag"]
                country.price_usd = sell
                country.last_cost_usd = cost_usd
                country.margin_percent = margin_percent
                country.is_active = True

        batch.added_count = added
        batch.skipped_dupes = dupes
        await session.commit()
        return {"added": added, "dupes": dupes, "countries": per_country, "sell": sell}

    @staticmethod
    async def buy_one(session, user_id: int, country_key: str):
        """يحجز أقدم عنصر متاح لهذه الدولة (ينقص المخزون تلقائياً) ويرجعه."""
        from database.models import TgReadyCountry, TgReadyItem, TgReadyItemStatus

        country = await session.get(TgReadyCountry, country_key)
        price = country.price_usd if country else None
        item = (
            await session.execute(
                select(TgReadyItem)
                .where(
                    TgReadyItem.country_key == country_key,
                    TgReadyItem.status == TgReadyItemStatus.AVAILABLE,
                )
                .order_by(TgReadyItem.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if item is None:
            return None, None
        if price is None or price <= 0:
            price = item.price_usd
        # حجز ذري: لا يُباع نفس الرقم لزبونين
        res = await session.execute(
            update(TgReadyItem)
            .where(
                TgReadyItem.id == item.id,
                TgReadyItem.status == TgReadyItemStatus.AVAILABLE,
            )
            .values(
                status=TgReadyItemStatus.SOLD,
                buyer_user_id=user_id,
                sold_at=datetime.utcnow(),
            )
        )
        if res.rowcount != 1:
            return None, None
        await session.commit()
        await session.refresh(item)
        return item, price
