"""Map runtime errors to a human diagnosis and a concrete fix for admins."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorDiagnosis:
    title: str
    severity: str
    cause: str
    solution: str

    def as_html(self) -> str:
        severity_icon = {
            "critical": "🛑",
            "high": "🚨",
            "medium": "⚠️",
            "low": "ℹ️",
        }.get(self.severity, "⚠️")
        return (
            f"{severity_icon} <b>{self.title}</b>\n"
            f"درجة الخطورة: <b>{self.severity}</b>\n\n"
            f"📌 <b>السبب:</b>\n{self.cause}\n\n"
            f"🛠 <b>الحل المقترح:</b>\n{self.solution}"
        )


# كل قاعدة: (مفاتيح البحث، التشخيص، هل يُقارَن برسالة الخطأ وحدها).
#
# الثالث مهم جداً: التتبّع يحوي كلمات عامة بالصدفة — ``request_timeout`` داخل
# كود أيوغرام، رقم سطر مثل 403، كلمة ``json`` في مسار ملف — فتُصنَّف أخطاء
# التنسيق والتحميل خطأ «مزود» وتُغَيِّر تشخيص الأدمن بالكامل. لذا الكلمات
# الغامضة (timeout/json/401/403...) تُقابَل على نص الاستثناء فقط، أما
# العلامات المفتقدة (MissingGreenlet…) فيُسمح بالبحث في التتبّع أيضاً.
_RULES: tuple[tuple[tuple[str, ...], ErrorDiagnosis, bool], ...] = (
    (
        (
            "can't parse entities",
            "unexpected end tag",
            "unexpected start tag",
            "can't find end of tag",
            "expected closing tag",
            "unbalanced",
        ),
        ErrorDiagnosis(
            title="تنسيق HTML مكسور في رسالة البوت",
            severity="medium",
            cause=(
                "تيليجرام رفض نص الرسالة لأنه يحوي وسم HTML غير متوازن "
                "(</b> زائد أو < بلا إغلاق). الأسباب المعتادة: اسم قسم/منتج "
                "أو شرح مسحوب من المزود فيه الحرف < أو &، أو مقطع ترجمة "
                "يحمل </b> بينما الكود يغلق الوسم أيضاً. لا علاقة للمزود "
                "بالعطل والطلب لم يُرسل أصلاً — المشكلة في العرض."
            ),
            solution=(
                "1) حدّث الكود لنسخة تستخدم services/html_guard "
                "(esc للقيم + HtmlGuardedBot يصلح الوسوم قبل الإرسال).\n"
                "2) إن استمر: من التتبّع أدناه افتح الملف والسطر، وأزل الوسم "
                "الزائد من نص الرسالة أو من شرح القسم في لوحة الأدمن.\n"
                "3) لا تشحن المزود ولا تعطّل الخدمة — العطل في التنسيق فقط."
            ),
        ),
        True,
    ),
    (
        ("not enough fund", "not_enough_funds", "insufficient", "low balance"),
        ErrorDiagnosis(
            title="رصيد المزود غير كافٍ",
            severity="high",
            cause="المزود رفض الطلب لأن رصيد حسابك عنده انتهى أو لا يكفي للكمية المطلوبة.",
            solution=(
                "1) ادخل لوحة المزود واشحن رصيده فوراً.\n"
                "2) عطّل المنتج مؤقتاً من إدارة المنتجات حتى لا تُخصم مبالغ من المستخدمين.\n"
                "3) انقل الخدمة إلى مزود بديل لديه رصيد عبر مسار احتياطي."
            ),
        ),
        True,
    ),
    (
        ("catching classes that do not inherit from baseexception",),
        ErrorDiagnosis(
            title="خطأ معالجة استثناء غير صالح",
            severity="critical",
            cause="الكود يحاول التقاط كائن ليس فئة استثناء (مثل aiohttp.ClientTimeout).",
            solution="تأكد أن كل جملة except تستخدم فئة Exception حقيقية، وأعد تشغيل البوت بعد التحديث.",
        ),
        False,
    ),
    (
        ("missinggreenlet", "greenlet_spawn"),
        ErrorDiagnosis(
            title="تحميل كسول على جلسة غير متزامنة",
            severity="high",
            cause="تم الوصول لعلاقة SQLAlchemy دون selectinload داخل AsyncSession.",
            solution="حمّل العلاقة مسبقاً بـ selectinload قبل بناء الرسالة أو الكيبورد.",
        ),
        False,
    ),
    (
        ("message is not modified", "query is too old", "query_expired"),
        ErrorDiagnosis(
            title="خطأ تيليجرام حميد",
            severity="low",
            cause="المستخدم ضغط نفس الزر مرتين أو انتهت صلاحية الضغطة.",
            solution="لا إجراء مطلوب. يُتجاهل بهدوء ولا يُزعج الأدمن.",
        ),
        True,
    ),
    (
        ("timeout", "timed out", "انتهت مهلة"),
        ErrorDiagnosis(
            title="انتهت مهلة الاتصال بالمزود",
            severity="high",
            cause="المزود لم يرد خلال المهلة المحددة أو الشبكة بطيئة.",
            solution="تحقق من حالة المزود ورابط API. إن تكرر العطل عطّله أو خفّض الكمية/التردد.",
        ),
        True,
    ),
    (
        ("cannot connect", "clientconnector", "name or service not known", "network is unreachable"),
        ErrorDiagnosis(
            title="تعذر الاتصال بالمزود",
            severity="high",
            cause="رابط API غير صحيح أو المزود متوقف أو جدار ناري يحجب الخادم.",
            solution="افتح رابط API من الخادم، صحّح الـ URL، وتأكد أن المزود أونلاين.",
        ),
        False,
    ),
    (
        ("invalid api", "unauthorized", "forbidden", "مفتاح api غير صالح", "401", "403"),
        ErrorDiagnosis(
            title="مفتاح API مرفوض",
            severity="high",
            cause="مفتاح المزود خاطئ أو منتهٍ أو بلا صلاحيات.",
            solution="حدّث API Key من لوحة المزود داخل البوت ثم اختبر الاتصال.",
        ),
        True,
    ),
    (
        ("json", "استجابة غير صالحة"),
        ErrorDiagnosis(
            title="رد المزود ليس JSON صالحاً",
            severity="medium",
            cause="المزود أرجع HTML/نصاً بدل JSON، غالباً بسبب رابط خاطئ أو صيانة.",
            solution="تحقق من مسار /api/v2 وتأكد أن المفتاح يُرسل في الجسم وليس الهيدر فقط.",
        ),
        True,
    ),
    (
        ("integrityerror", "unique constraint", "foreign key"),
        ErrorDiagnosis(
            title="تعارض في قاعدة البيانات",
            severity="medium",
            cause="محاولة إدخال صف مكرر أو ربط بمعرّف غير موجود.",
            solution="لا تكرر العملية. راجع السجل المعني واحذف التكرار إن وُجد.",
        ),
        False,
    ),
    (
        ("database is locked", "operationalerror"),
        ErrorDiagnosis(
            title="قاعدة البيانات مشغولة أو تالفة",
            severity="critical",
            cause="SQLite مقفلة أو الاتصال بقاعدة البيانات انقطع.",
            solution="أوقف العمليات الثقيلة، تأكد من مساحة القرص، وفي الإنتاج استخدم PostgreSQL.",
        ),
        False,
    ),
    (
        ("chat not found", "bot was blocked", "forbidden: bot"),
        ErrorDiagnosis(
            title="تيليجرام رفض الإرسال",
            severity="medium",
            cause="المستخدم حظر البوت أو معرّف القناة خاطئ أو البوت ليس مشرفاً.",
            solution="تحقق من ADMIN_NOTIFY_CHAT_ID وصلاحيات البوت في القناة.",
        ),
        True,
    ),
    (
        ("message to edit not found", "message can't be edited"),
        ErrorDiagnosis(
            title="الرسالة القديمة لم تعد قابلة للتعديل",
            severity="low",
            cause="البوت يحاول تعديل رسالة حُذفت أو مُرّرت من محادثة قديمة.",
            solution="أرسل رسالة جديدة بدل التعديل، أو تجاهل الخطأ كخطأ حميد.",
        ),
        True,
    ),
)


def diagnose(exc: BaseException, traceback_text: str = "", context: str = "") -> ErrorDiagnosis:
    """تشخيص الاستثناء: رسالة الخطأ أولاً، ثم التتبّع للعلامات المفتقدة فقط."""
    primary = f"{type(exc).__name__}: {exc}".lower()
    fallback = f"{traceback_text}\n{context}".lower()
    for needles, diagnosis, message_only in _RULES:
        if any(needle in primary for needle in needles):
            return diagnosis
        if not message_only and any(needle in fallback for needle in needles):
            return diagnosis
    return ErrorDiagnosis(
        title=f"خطأ غير متوقع: {type(exc).__name__}",
        severity="high",
        cause=str(exc)[:500] or "استثناء بلا رسالة.",
        solution=(
            "1) راجع التتبع أدناه لتحديد الملف والسطر.\n"
            "2) أعد محاولة العملية بعد دقيقة.\n"
            "3) إن تكرر الخطأ عطّل الميزة مؤقتاً من مركز الإضافات وأرسل التتبع للمطور."
        ),
    )
