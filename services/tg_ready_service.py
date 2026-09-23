"""مخزون الجلسات الجاهزة (أرقام تلجرام الجاهزة) — فرز تلقائي من ملف.

الأدمن يرفع ملف واحد (txt / csv / zip بأسماء ملفات تحوي أرقاماً)، والبوت:
1) يستخرج كل الأرقام، 2) يتعرف على الدولة من مقدمة الرقم (الاسم + العلم),
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
from decimal import Decimal, ROUND_UP
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
    "353": ("أيرلندا", "🇮🇪"),
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
    "387": ("البوسنة والهرسك", "🇧🇦"),
    "389": ("مقدونيا الشمالية", "🇲🇰"),
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
    "675": ("بابوا غينيا الجديدة", "🇵🇬"),
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
    "880": ("بنغلاديش", "🇧🇩"),
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
    "975": ("بيلاروسيا", "🇧🇾"),
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


def protect_payload(payload: str) -> str | None:
    """حفظ حمولة السطر: مشفّرة إن توفّر المفتاح، وإلا صريحة — لا نضيع الروابط أبداً.

    كان السلوك السابق: أي فشل تشفير (مفتاح ناقص) يسجّل payload_encrypted=None،
    فيفقد المشتري رابط ZIP ورابط الكود ويظهر «لا توجد جلسة محفوظة».
    """
    if not payload:
        return None
    try:
        from services.encryption_service import EncryptionService

        return EncryptionService.encrypt(payload)
    except Exception:
        return payload


def reveal_payload(stored: str | None, phone: str = "") -> str:
    """قراءة الحمولة سواء كانت مشفّرة أو صريحة (رفعات قديمة بلا مفتاح)."""
    if not stored:
        return phone or ""
    try:
        from services.encryption_service import EncryptionService

        return EncryptionService.decrypt(stored)
    except Exception:
        # ليست رمزاً مشفّراً صالحاً — إن بدت حمولة (روابط/مقسّم) استعملها كما هي
        if "http" in stored or "|" in stored or stored.lstrip().startswith("+"):
            return stored
        return phone or ""


def _norm_phone(raw: str) -> str | None:
    if not raw:
        return None
    compact = (raw or "").replace(" ", "")
    # الأفضل: رقم بصيغة دولية صريحة (+...) — كما في صيغة المورّد |+63...|
    m = re.search(r"\+(\d{7,15})", compact)
    if m:
        return "+" + m.group(1)
    # وإلا: كل المرشحات الرقمية، ونفضّل الأطول (رقم الهاتف أطول من بقايا hash)
    # مع تجاهل الأرقام الطويلة جداً داخل base64 (أكثر من 15 رقماً متتالياً ليست هاتفاً).
    cands = re.findall(r"(?<!\d)(\d{7,15})(?!\d)", compact)
    if not cands:
        return None
    # رتّب بالأطول أولاً ثم الأقرب لبداية الرقم الدولي المعروف
    cands = sorted(set(cands), key=len, reverse=True)
    return "+" + cands[0]


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


# ── ملفات الجلسة الفعلية (tdata/session) لكل رقم ──

#: جذر تخزين ملفات الجلسات (مجلد data/ مُتجاهَل من git — لا يُرفع للمستودع).
READY_FILES_ROOT = Path("data/tg_ready")

#: حد أقصى لحجم ملف واحد داخل ZIP (25MB) حتى لا يمتلئ القرص بملف مفخخ.
MAX_ONE_FILE_BYTES = 25 * 1024 * 1024


def _safe_phone_dir(phone: str) -> str:
    return re.sub(r"\D", "", phone or "unknown") or "unknown"


def extract_zip_files(raw: bytes) -> dict[str, list[tuple[str, bytes]]]:
    """يستخرج ملفات كل حساب من ZIP مرفوع: {phone: [(اسم الملف, المحتوى)]}.

    التجميع برقم الهاتف المكتشف من مسار الملف (مجلد الحساب عادة اسمه الرقم).
    الملفات النصية الصغيرة التي لا يُكتشف رقمها من اسمها تُنسب لأول رقم
    يُكتشف داخل محتواها، وما عداها يُتجاهل.
    """
    out: dict[str, list[tuple[str, bytes]]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.file_size > MAX_ONE_FILE_BYTES:
                    continue
                phone = _norm_phone(info.filename)
                try:
                    blob = zf.read(info.filename)
                except Exception:
                    continue
                if not phone and info.file_size < 20000:
                    phone = _norm_phone(blob[:4000].decode("utf-8", errors="ignore"))
                if not phone:
                    continue
                out.setdefault(phone, []).append((Path(info.filename).name or "file", blob))
    except zipfile.BadZipFile:
        return {}
    return out


def save_account_files(batch_id: int, phone: str, files: list[tuple[str, bytes]]) -> list[str]:
    """يحفظ ملفات حساب واحد على القرص ويرجع مساراتها النسبية."""
    phone_dir = READY_FILES_ROOT / str(batch_id) / _safe_phone_dir(phone)
    phone_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    seen_names: set[str] = set()
    for raw_name, blob in files:
        name = re.sub(r"[^\w.\-() ]", "_", raw_name or "file")[:120] or "file"
        if name in seen_names:
            stem, dot, ext = name.partition(".")
            name = f"{stem}_{len(seen_names)}{('.' + ext) if dot else ''}"
        seen_names.add(name)
        dest = phone_dir / name
        # حماية traversal: البقاء داخل مجلد الحساب حصراً
        if phone_dir not in dest.resolve().parents and dest.resolve() != phone_dir:
            dest = phone_dir / "file"
        dest.write_bytes(blob)
        saved.append(str(dest.relative_to(READY_FILES_ROOT)))
    return saved


def build_account_zip(phone: str, rel_paths: list[str]) -> tuple[str, bytes] | None:
    """يجمع ملفات الحساب المخزنة بأرشيف ZIP واحد جاهز للتسليم."""
    if not rel_paths:
        return None
    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in rel_paths:
            src = READY_FILES_ROOT / rel
            try:
                if not src.is_file() or src.stat().st_size > MAX_ONE_FILE_BYTES:
                    continue
                # داخل الأرشيف: مجلد باسم الرقم حتى لا تختلط الملفات عند الفك
                zf.writestr(f"{_safe_phone_dir(phone)}/{src.name}", src.read_bytes())
                added += 1
            except OSError:
                continue
    if not added:
        return None
    fname = f"telegram_session_{_safe_phone_dir(phone)}.zip"
    return fname, buf.getvalue()


def extract_login_link(payload: str) -> str | None:
    """يستخرج رابط كود الدخول (login-code link) من سطر البيانات إن وُجد."""
    # صيغة المورّد الشائعة: رابط_ملف_ZIP | الرقم | رابط_الكود (/c/...)
    # رابط الكود له أولوية على رابط الملف.
    code = extract_code_link(payload or "")
    if code:
        return code
    m = re.search(r"https?://[^\s'\"<>]+", payload or "")
    return m.group(0) if m else None


def extract_all_links(payload: str) -> list[str]:
    """كل الروابط بالسطر مرتبة كما وردت (بدون تكرار + بدون ترقيم زائد)."""
    if not payload:
        return []
    found: list[str] = []
    for m in re.finditer(r"https?://[^\s'\"<>|,;]+", payload):
        url = m.group(0).rstrip(".,)")
        if url and url not in found:
            found.append(url)
    return found


def _is_code_link(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in ("/c/", "/code", "gethtml", "/login", "code", "otp", "/tvr", "/v-ke"))


def _is_file_link(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in ("/files/", ".zip", "download", ".session", "tdata"))


def extract_code_link(payload: str) -> str | None:
    """رابط الكود (/c/...) من صيغة: ملف | رقم | كود."""
    links = extract_all_links(payload or "")
    if not links:
        return None
    for url in links:
        if _is_code_link(url):
            return url
    # لا يوجد رابط كود واضح: إن كان هناك رابطان فالثاني غالباً هو الكود
    if len(links) >= 2:
        # الأول ملف غالباً، الثاني كود
        if _is_file_link(links[0]):
            return links[1]
        return links[-1]
    return None


def extract_file_link(payload: str) -> str | None:
    """رابط ملف الجلسة ZIP من صيغة: ملف | رقم | كود."""
    links = extract_all_links(payload or "")
    if not links:
        return None
    for url in links:
        if _is_file_link(url):
            return url
    # لا يوجد رابط ملف واضح: إن كان هناك رابطان فالأول غالباً هو الملف
    if len(links) >= 2 and not _is_code_link(links[0]):
        return links[0]
    if len(links) == 1 and not _is_code_link(links[0]):
        return links[0]
    return None


def extract_twofa(payload: str) -> str | None:
    """يستخرج كلمة التحقق بخطوتين (2FA) من السطر إن وُجدت."""
    if not payload:
        return None
    # أنماط شائعة: 2fa:xxx | pass:xxx | password xxx | تحقق:xxx
    patterns = [
        r"(?:2\s*fa|twofa|password|pass|pwd|تحقق|كلمة\s*(?:المرور|السر))\s*[:=\s]+\s*([^\s|;,]+)",
        r"\b2FA\s+([^\s|;,]+)",
    ]
    for pat in patterns:
        m = re.search(pat, payload, re.IGNORECASE)
        if m:
            val = (m.group(1) or "").strip().strip("'\"")
            # تجاهل القيم التي هي روابط أو أرقام هواتف
            if val and not val.startswith("http") and not _norm_phone(val):
                return val
    return None


def parse_login_codes(text: str) -> list[str]:
    """يستخرج أكواد الدخول (5-6 أرقام) من صفحة الكود (للمورّدين العامّين)."""
    if not text:
        return []
    # أكواد تيليجرام عادة 5 أرقام
    codes = re.findall(r"\b(\d{5,6})\b", text)
    # رتّب الأكواد القريبة من كلمات (code/login/telegram/كود) أولاً
    scored: list[tuple[int, str]] = []
    low = text.lower()
    for c in dict.fromkeys(codes):  # إزالة التكرار مع الحفاظ على الترتيب
        idx = low.find(c)
        window = low[max(0, idx - 120): idx + 120]
        score = 0
        if any(k in window for k in ("code", "login", "telegram", "otp", "verify", "كود", "تحقق")):
            score = 1
        scored.append((score, c))
    scored.sort(key=lambda x: (-x[0], text.find(x[1])))
    return [c for _, c in scored]


def dl_cloude_base(code_url: str) -> tuple[str, str] | None:
    """يستخرج (base, code_id) من رابط كود dl-cloude بصيغة /c/{id}.

    مثال: https://dl-cloude.org/c/ABC123 -> (https://dl-cloude.org/c/ABC123, ABC123)
    يرجع None إن لم تكن الصيغة مطابقة.
    """
    if not code_url:
        return None
    m = re.search(r"(https?://[^/]+)/c/([A-Za-z0-9_\-]+)", (code_url or "").strip().rstrip("/"))
    if not m:
        return None
    host = m.group(1).rstrip("/")
    cid = m.group(2)
    return f"{host}/c/{cid}", cid


async def _fetch_json(url: str, timeout_s: int = 20, method: str = "GET") -> dict | None:
    """GET/POST خفيف يرجع JSON كـ dict أو None عند الفشل."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        import aiohttp

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_s),
            headers={"User-Agent": "Mozilla/5.0 (TelegramBot)"},
        ) as sess:
            ctx = sess.post(url, allow_redirects=True) if method.upper() == "POST" else sess.get(url, allow_redirects=True)
            async with ctx as resp:
                if resp.status != 200:
                    return None
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    return None
                return data if isinstance(data, dict) else None
    except Exception:
        return None


async def fetch_dl_cloude_code(code_url: str, timeout_s: int = 25) -> dict:
    """يجلب الكود من مورّد dl-cloude عبر API الحقيقي (وليس HTML).

    الصفحة تعمل بالJS: POST /c/{id}/code ثم GET /c/{id}/twofa.
    يرجع: {"status": delivered|already_used|rate_limited|timeout|pending|fetch_failed,
            "code": str|None, "twofa": str|None, "retry_after": int, "base": str}
    """
    info = dl_cloude_base(code_url or "")
    if not info:
        return {"status": "not_dl_cloude", "code": None, "twofa": None, "retry_after": 0, "base": None}
    base, _cid = info
    data = await _fetch_json(f"{base}/code", timeout_s=timeout_s, method="POST")
    if not data:
        return {"status": "fetch_failed", "code": None, "twofa": None, "retry_after": 0, "base": base}
    status = str(data.get("status") or "").lower() or "unknown"
    code = data.get("code")
    code_s = str(code).strip() if code is not None else None
    if code_s and not re.fullmatch(r"\d{4,8}", code_s):
        # المورّد يرسل الكود رقمياً فقط — غير ذلك تجاهله
        code_s = None
    retry_after = 0
    try:
        retry_after = int(data.get("retry_after") or 0)
    except (ValueError, TypeError):
        retry_after = 0
    twofa: str | None = None
    # كلمة 2FA متوفرة عبر endpoint مستقل — نجلبها عند نجاح الكود أو دائماً
    try:
        t = await _fetch_json(f"{base}/twofa", timeout_s=timeout_s, method="GET")
        if t and t.get("password"):
            twofa = str(t["password"]).strip() or None
    except Exception:
        twofa = None
    return {"status": status, "code": code_s, "twofa": twofa, "retry_after": retry_after, "base": base}


async def fetch_url_text(url: str, timeout_s: int = 20, max_chars: int = 200_000) -> str | None:
    """يجلب صفحة الكود كنص (لزر طلب الكود). يرجع None عند الفشل."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        import aiohttp

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_s),
            headers={"User-Agent": "Mozilla/5.0 (TelegramBot)"},
        ) as sess:
            async with sess.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return None
                data = await resp.content.read(max_chars + 1)
                if len(data) > max_chars:
                    data = data[:max_chars]
                for enc in ("utf-8", "utf-8-sig", "latin-1"):
                    try:
                        return data.decode(enc)
                    except UnicodeDecodeError:
                        continue
                return data.decode("utf-8", errors="ignore")
    except Exception:
        return None


async def fetch_code_for_payload(payload: str, timeout_s: int = 25) -> dict:
    """يجلب الكود الجاهز من رابط الكود داخل السطر.

    يدعم مورّد dl-cloude عبر API الحقيقي (POST /c/{id}/code + GET /twofa)،
    وأي مورّد آخر عبر قراءة HTML واستخراج 5-6 أرقام.

    يرجع: {"ok": bool, "codes": [...], "raw_excerpt": str, "code_url": str|None,
            "error": str, "twofa_remote": str|None, "retry_after": int}
    أخطاء error: no_code_link|fetch_failed|no_code_yet|already_used|rate_limited
    """
    code_url = extract_code_link(payload or "")
    if not code_url:
        return {"ok": False, "codes": [], "raw_excerpt": "", "code_url": None,
                "error": "no_code_link", "twofa_remote": None, "retry_after": 0}
    # 1) مورّد dl-cloude (/c/...) — API حقيقي عبر POST
    if dl_cloude_base(code_url):
        res = await fetch_dl_cloude_code(code_url, timeout_s=timeout_s)
        st = res.get("status") or ""
        if st == "delivered" and res.get("code"):
            return {"ok": True, "codes": [str(res["code"])], "raw_excerpt": "",
                    "code_url": code_url, "error": "",
                    "twofa_remote": res.get("twofa"), "retry_after": 0}
        if st == "already_used":
            return {"ok": False, "codes": [], "raw_excerpt": "", "code_url": code_url,
                    "error": "already_used", "twofa_remote": res.get("twofa"), "retry_after": 0}
        if st == "rate_limited":
            return {"ok": False, "codes": [], "raw_excerpt": "", "code_url": code_url,
                    "error": "rate_limited", "twofa_remote": res.get("twofa"),
                    "retry_after": int(res.get("retry_after") or 0)}
        if st in ("timeout", "pending"):
            return {"ok": False, "codes": [], "raw_excerpt": "", "code_url": code_url,
                    "error": "no_code_yet", "twofa_remote": res.get("twofa"), "retry_after": 0}
        if st == "fetch_failed":
            return {"ok": False, "codes": [], "raw_excerpt": "", "code_url": code_url,
                    "error": "fetch_failed", "twofa_remote": None, "retry_after": 0}
        # حالة غير معروفة — نسقط للقراءة العامة كاحتياط
    # 2) مورّدون عامّون — قراءة الصفحة واستخراج الكود
    page = await fetch_url_text(code_url, timeout_s=timeout_s)
    if not page:
        return {"ok": False, "codes": [], "raw_excerpt": "", "code_url": code_url,
                "error": "fetch_failed", "twofa_remote": None, "retry_after": 0}
    codes = parse_login_codes(page)
    excerpt = re.sub(r"\s+", " ", page)[:500]
    if codes:
        return {"ok": True, "codes": codes, "raw_excerpt": excerpt, "code_url": code_url,
                "error": "", "twofa_remote": None, "retry_after": 0}
    return {"ok": False, "codes": [], "raw_excerpt": excerpt, "code_url": code_url,
            "error": "no_code_yet", "twofa_remote": None, "retry_after": 0}


async def download_file_bytes(url: str, timeout_s: int = 30, max_bytes: int = 25 * 1024 * 1024) -> bytes | None:
    """يحمّل ملف ZIP الجلسة من رابط الملف. يرجع bytes أو None."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        import aiohttp

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_s),
            headers={"User-Agent": "Mozilla/5.0 (TelegramBot)"},
        ) as sess:
            async with sess.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    return None
                length = resp.headers.get("Content-Length")
                try:
                    if length and int(length) > max_bytes:
                        return None
                except (ValueError, TypeError):
                    pass
                buf = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        return None
                if not buf:
                    return None
                return bytes(buf)
    except Exception:
        return None


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
        files_map: dict[str, list[tuple[str, bytes]]] | None = None,
    ) -> dict:
        """يفرز الملف تلقائياً: دولة + علم + سعر لكل مجموعة، ويسجّل المخزون.

        files_map (اختياري، من extract_zip_files): ملفات الجلسة الفعلية لكل
        رقم — تُحفظ على القرص تحت data/tg_ready وتُسلَّم ZIP للمشتري، فيدخل
        الزبون بالملف مباشرة بلا انتظار أي كود.
        """
        import json as _json

        from database.models import TgReadyBatch, TgReadyCountry, TgReadyItem, TgReadyItemStatus

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
                # إصلاح الرفعات القديمة: حمولة ناقصة (بلا مفتاح تشفير) تُملأ الآن
                try:
                    from sqlalchemy import select as _select

                    old = (
                        await session.execute(
                            _select(TgReadyItem).where(TgReadyItem.phone_number == e.phone)
                        )
                    ).scalars().first()
                    if old is not None:
                        cur = (
                            reveal_payload(old.payload_encrypted, "")
                            if old.payload_encrypted
                            else ""
                        )
                        new_useful = ("http" in e.payload) or ("|" in e.payload)
                        if new_useful and "http" not in cur:
                            old.payload_encrypted = protect_payload(e.payload)
                except Exception:
                    pass
                dupes += 1
                continue
            key, name, flag = detect_country(e.phone)
            enc = protect_payload(e.payload)
            files_json = None
            if files_map and e.phone in files_map:
                try:
                    saved = save_account_files(batch.id, e.phone, files_map[e.phone])
                    if saved:
                        files_json = _json.dumps(saved, ensure_ascii=False)
                except OSError:
                    files_json = None
            session.add(
                TgReadyItem(
                    phone_number=e.phone,
                    country_key=key,
                    country_name_ar=name,
                    flag=flag,
                    cost_usd=cost_usd,
                    price_usd=sell,
                    payload_encrypted=enc,
                    files_json=files_json,
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
        with_files = (
            await session.execute(
                select(func.count(TgReadyItem.id)).where(
                    TgReadyItem.batch_id == batch.id,
                    TgReadyItem.files_json.is_not(None),
                )
            )
        ).scalar_one()
        return {
            "added": added,
            "dupes": dupes,
            "countries": per_country,
            "sell": sell,
            "with_files": int(with_files),
        }

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
