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


_RULES: tuple[tuple[tuple[str, ...], ErrorDiagnosis], ...] = (
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
    ),
    (
        ("catching classes that do not inherit from baseexception",),
        ErrorDiagnosis(
            title="خطأ معالجة استثناء غير صالح",
            severity="critical",
            cause="الكود يحاول التقاط كائن ليس فئة استثناء (مثل aiohttp.ClientTimeout).",
            solution="تأكد أن كل جملة except تستخدم فئة Exception حقيقية، وأعد تشغيل البوت بعد التحديث.",
        ),
    ),
    (
        ("missinggreenlet", "greenlet_spawn"),
        ErrorDiagnosis(
            title="تحميل كسول على جلسة غير متزامنة",
            severity="high",
            cause="تم الوصول لعلاقة SQLAlchemy دون selectinload داخل AsyncSession.",
            solution="حمّل العلاقة مسبقاً بـ selectinload قبل بناء الرسالة أو الكيبورد.",
        ),
    ),
    (
        ("message is not modified", "query is too old", "query_expired"),
        ErrorDiagnosis(
            title="خطأ تيليجرام حميد",
            severity="low",
            cause="المستخدم ضغط نفس الزر مرتين أو انتهت صلاحية الضغطة.",
            solution="لا إجراء مطلوب. يُتجاهل بهدوء ولا يُزعج الأدمن.",
        ),
    ),
    (
        ("timeout", "timed out", "انتهت مهلة"),
        ErrorDiagnosis(
            title="انتهت مهلة الاتصال بالمزود",
            severity="high",
            cause="المزود لم يرد خلال المهلة المحددة أو الشبكة بطيئة.",
            solution="تحقق من حالة المزود ورابط API. إن تكرر العطل عطّله أو خفّض الكمية/التردد.",
        ),
    ),
    (
        ("cannot connect", "clientconnector", "name or service not known", "network is unreachable"),
        ErrorDiagnosis(
            title="تعذر الاتصال بالمزود",
            severity="high",
            cause="رابط API غير صحيح أو المزود متوقف أو جدار ناري يحجب الخادم.",
            solution="افتح رابط API من الخادم، صحّح الـ URL، وتأكد أن المزود أونلاين.",
        ),
    ),
    (
        ("invalid api", "unauthorized", "forbidden", "مفتاح api غير صالح", "401", "403"),
        ErrorDiagnosis(
            title="مفتاح API مرفوض",
            severity="high",
            cause="مفتاح المزود خاطئ أو منتهٍ أو بلا صلاحيات.",
            solution="حدّث API Key من لوحة المزود داخل البوت ثم اختبر الاتصال.",
        ),
    ),
    (
        ("json", "استجابة غير صالحة"),
        ErrorDiagnosis(
            title="رد المزود ليس JSON صالحاً",
            severity="medium",
            cause="المزود أرجع HTML/نصاً بدل JSON، غالباً بسبب رابط خاطئ أو صيانة.",
            solution="تحقق من مسار /api/v2 وتأكد أن المفتاح يُرسل في الجسم وليس الهيدر فقط.",
        ),
    ),
    (
        ("integrityerror", "unique constraint", "foreign key"),
        ErrorDiagnosis(
            title="تعارض في قاعدة البيانات",
            severity="medium",
            cause="محاولة إدخال صف مكرر أو ربط بمعرّف غير موجود.",
            solution="لا تكرر العملية. راجع السجل المعني واحذف التكرار إن وُجد.",
        ),
    ),
    (
        ("database is locked", "operationalerror"),
        ErrorDiagnosis(
            title="قاعدة البيانات مشغولة أو تالفة",
            severity="critical",
            cause="SQLite مقفلة أو الاتصال بقاعدة البيانات انقطع.",
            solution="أوقف العمليات الثقيلة، تأكد من مساحة القرص، وفي الإنتاج استخدم PostgreSQL.",
        ),
    ),
    (
        ("chat not found", "bot was blocked", "forbidden: bot"),
        ErrorDiagnosis(
            title="تيليجرام رفض الإرسال",
            severity="medium",
            cause="المستخدم حظر البوت أو معرّف القناة خاطئ أو البوت ليس مشرفاً.",
            solution="تحقق من ADMIN_NOTIFY_CHAT_ID وصلاحيات البوت في القناة.",
        ),
    ),
)


def diagnose(exc: BaseException, traceback_text: str = "", context: str = "") -> ErrorDiagnosis:
    blob = f"{type(exc).__name__}: {exc}\n{traceback_text}\n{context}".lower()
    for needles, diagnosis in _RULES:
        if any(needle in blob for needle in needles):
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
