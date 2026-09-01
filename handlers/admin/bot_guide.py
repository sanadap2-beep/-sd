"""In-bot Arabic admin guide for every major button and service."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from filters.admin_filter import IsAdmin

router = Router(name="admin_bot_guide")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧭 شرح لوحة الأدمن", callback_data="admin:guide:panel")],
        [InlineKeyboardButton(text="📂 الأقسام والأزرار", callback_data="admin:guide:categories")],
        [InlineKeyboardButton(text="🔌 ربط المزودين", callback_data="admin:guide:providers")],
        [InlineKeyboardButton(text="📦 إضافة المنتجات", callback_data="admin:guide:products")],
        [InlineKeyboardButton(text="🏪 سوق المستخدمين", callback_data="admin:guide:market")],
        [InlineKeyboardButton(text="🔥 العروض الخاصة", callback_data="admin:guide:special")],
        [InlineKeyboardButton(text="📢 الإعلانات", callback_data="admin:guide:ads")],
        [InlineKeyboardButton(text="💳 الشحن والسحب", callback_data="admin:guide:finance")],
        [InlineKeyboardButton(text="🔔 الإشعارات والقنوات", callback_data="admin:guide:notifs")],
        [InlineKeyboardButton(text="🧪 الفحص قبل البيع", callback_data="admin:guide:qa")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")],
    ])


_GUIDES = {
    "panel": """🧭 <b>شرح لوحة الأدمن</b>\n\n📊 إحصائيات البوت: أرقام عامة عن المستخدمين والطلبات والرصيد.\n📦 إدارة الطلبات: طلبات المنتجات العامة API / Inventory / Manual.\n📞 طلبات الأرقام: متابعة أرقام SMS واسترجاعها.\n💳 طلبات الشحن: مراجعة الشحن اليدوي.\n💸 طلبات السحب: دفع سحوبات التجار أو رفضها وإرجاع الرصيد.\n📢 إدارة الإعلانات: قبول/رفض/حذف/تعديل الإعلانات المدفوعة.\n🔥 قسم العروض: إنشاء عروض 24 ساعة يدوية أو تلقائية.\n📂 إدارة الأقسام: إنشاء أقسام رئيسية وفرعية وتعديلها.\n📦 إدارة المنتجات: إضافة منتجات وربطها بمزود أو مخزون أو تنفيذ يدوي.\n🔌 مزودو المتجر: ربط أي مزود API وسحب خدماته.\n🌐 مزودو الأرقام: حالة مزودي أرقام SMS.\n👥 إدارة المستخدمين: بحث، عرض معلومات، إضافة/خصم رصيد، حظر/فك حظر.\n📌 الاشتراك الإجباري: إضافة عدد غير محدود من القنوات المطلوبة قبل استخدام البوت.\n📢 إذاعة جماعية: إرسال رسالة لكل المستخدمين غير المحظورين.\n🔔 إدارة الإشعارات: سجل الإرسال وقوالب الرسائل.""",
    "categories": """📂 <b>الأقسام والأزرار</b>\n\nالأقسام الأساسية تظهر للمستخدم كأزرار رئيسية: الرشق، الألعاب، التطبيقات، الأرصدة، البطاقات، الاشتراكات، التوثيق، الأكواد.\n\nلإنشاء قسم: إدارة الأقسام ← إضافة قسم رئيسي ← اختر النوع ← الاسم ← الإيموجي.\nلإنشاء قسم فرعي: افتح القسم ← إضافة قسم فرعي ← الاسم/الوصف/الصورة.\n\n🎛 أزرار الواجهة: تستطيع إنشاء زر رئيسي يفتح قسم، قسم فرعي، منتج، صفحة داخلية أو رابط خارجي. يوجد فحص للأزرار المكسورة وترتيب رفع/تنزيل.""",
    "providers": """🔌 <b>ربط المزودين بدون كود</b>\n\nمن مزودو المتجر ← إضافة مزود جديد.\nاختر البروتوكول:\n📈 SMM V2 للرشق القياسي.\n🎮 Games JSON للألعاب.\n🛠 Custom JSON لأي موقع لديه API.\n\nفي Custom استخدم 🧙 معالج ربط بدون JSON: أدخل مسار المنتجات، مكان القائمة، حقول id/name/price/category، مسار الطلب، ومسار الحالة.\nبعد الحفظ: اختبر الاتصال ← مزامنة الخدمات ← افتح خدمة ← إنشاء منتج منها.\n\nإذا الموقع لا يملك API، استخدم منتج يدوي أو مخزون رقمي.""",
    "products": """📦 <b>إضافة المنتجات</b>\n\nأنواع التنفيذ:\n🔌 API: يرسل الطلب للمزود تلقائياً. مناسب للرشق، الألعاب، الأرصدة، المتاجر الخارجية.\n🔐 Inventory: كود/حساب/بطاقة مشفرة وتسلم فوراً بعد الشراء.\n🧑‍💼 Manual: المستخدم يدفع، والطلب يذهب للإدارة للتنفيذ أو الاسترجاع.\n\nبعد إنشاء المنتج استخدم زر 🧪 فحص جاهزية المنتج للتأكد من: السعر، القسم، المزود، المخزون، المتطلبات.""",
    "market": """🏪 <b>سوق المستخدمين</b>\n\nالمستخدم ينشئ حساب سوق باسم مستعار وكلمة سر. يضيف عرض بيع: كود، حساب، رقم، خدمة أو شيء آخر.\nالأكواد والحسابات تحفظ مشفرة ولا تكشف إلا للمشتري بعد الدفع.\n\nالمشتري بعد الاستلام يضغط: ✅ المنتج يعمل وصحيح فينتقل المال للبائع وتُخصم عمولتك. أو ⚠️ المعلومات خاطئة فيفتح نزاعاً.\n\nالبائع الموثوق يمكن نشر إعلاناته تلقائياً حسب Feature Flag: trusted_seller_auto_approve.""",
    "special": """🔥 <b>العروض الخاصة 24</b>\n\nمن قسم العروض ← إضافة عرض جديد.\n⚡ تلقائي: اختر مزوداً، أدخل service id، ثم الاسم والوصف والسعر وما الذي يطلبه من المستخدم. الطلب يذهب للمزود.\n🧑‍💼 يدوي: لا يحتاج مزوداً، ويذهب طلب المستخدم إلى قسم طلبات العروض اليدوية.\n\nالعرض يستمر 24 ساعة، يرسل إشعاراً عند النشر، تذكير بعد 4 ساعات، وتذكير قبل الانتهاء بساعتين. إذا حصل 50 تصويت تمديد يتمدد مرة واحدة 24 ساعة.""",
    "ads": """📢 <b>الإعلانات المدفوعة</b>\n\nالمستخدم من إعلاناتي يضيف إعلاناً بسعر 2$، يرسل عنوان/نص/نوع/سعر/تواصل/صور.\nالإعلان يصل لقناة الأدمن الخاصة مع قبول أو رفض.\nالقبول ينشره داخل البوت وعلى قناة الإشعارات العامة، ويعاد نشره بالقناة كل نصف ساعة لمدة 24 ساعة.\nالرفض يرجع 2$ للمستخدم. التجديد بـ1$ يضيف 15 ساعة بدون مراجعة جديدة.""",
    "finance": """💳 <b>الشحن والسحب</b>\n\nالشحن: شام كاش يدوي/تلقائي، USDT يدوي/تلقائي، Telegram Stars وطرق أخرى. سعر الصرف اليومي يضبط من الإعدادات.\nالسحب: المستخدم يضغط 💸 سحب الرصيد، يختار شام كاش أو USDT، يرسل المبلغ والعنوان. المبلغ يُحجز ويصل طلب للأدمن.\nالأدمن يضغط تم الدفع أو رفض وإرجاع الرصيد. خيار سحب شام كاش بالليرة يمكن تعطيله من إعدادات طرق الدفع.""",
    "notifs": """🔔 <b>الإشعارات والقنوات</b>\n\nADMIN_NOTIFY_CHAT_ID: قناة خاصة للأدمن، تصلها إيصالات الشحن، السحب، موافقات السوق، النزاعات، أخطاء النظام.\nPUBLIC_CHANNEL_ID: قناة عامة للجميع، تصلها عمليات الشراء الناجحة، مبيعات السوق الناجحة، الإعلانات والعروض.\n\nمركز الإشعارات يحفظ سجل إرسال وصندوق وارد للمستخدم، مع تفضيلات وقوالب قابلة للتعديل ومنع تكرار.""",
    "qa": """🧪 <b>الفحص قبل البيع</b>\n\nنفّذ:\n<code>python -m compileall -q .</code>\n<code>pytest -q</code>\n<code>python scripts/security_audit.py</code>\n<code>python scripts/i18n_audit.py</code>\n\nواختبار تحميل API:\n<code>python scripts/load_smoke.py http://localhost:8080 --requests 200 --concurrency 25</code>""",
}


@router.callback_query(F.data == "admin:guide")
async def admin_guide_home(callback: CallbackQuery):
    await callback.message.edit_text(
        "📘 <b>شرح البوت للأدمن</b>\n\n"
        "هذا الدليل يشرح كل زر وخدمة مهمة داخل لوحة الأدمن وكيف تربط المزودين وتضيف المنتجات والعروض والإعلانات.",
        reply_markup=_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:guide:"))
async def admin_guide_section(callback: CallbackQuery):
    key = callback.data.rsplit(":", 1)[1]
    await callback.message.edit_text(_GUIDES.get(key, "القسم غير موجود."), reply_markup=_kb())
    await callback.answer()
