"""Bot information and full Arabic guide."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

router = Router(name="bot_info")


def _info_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 شرح الأزرار والخدمات", callback_data="info:guide")],
        [InlineKeyboardButton(text="💳 كيف تشحن حسابك", callback_data="info:deposit")],
        [InlineKeyboardButton(text="💸 كيف تسحب رصيدك", callback_data="info:withdraw")],
        [InlineKeyboardButton(text="🏪 شرح سوق المستخدمين", callback_data="info:market")],
        [InlineKeyboardButton(text="🛒 كيف تشتري من السوق", callback_data="info:market_buy")],
        [InlineKeyboardButton(text="💰 كيف تبيع في السوق", callback_data="info:market_sell")],
        [InlineKeyboardButton(text="📱 شرح الأرقام", callback_data="info:numbers")],
        [InlineKeyboardButton(text="🔥 العروض الخاصة", callback_data="info:special")],
        [InlineKeyboardButton(text="📢 الإعلانات المدفوعة", callback_data="info:ads")],
        [InlineKeyboardButton(text="🔔 الإشعارات", callback_data="info:notifications")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="back_to_main")],
    ])


@router.callback_query(F.data == "info:home")
async def bot_info_home(callback: CallbackQuery):
    await callback.message.edit_text(
        "ℹ️ <b>معلومات البوت</b>\n\n"
        "هذا بوت متجر رقمي متكامل داخل تيليجرام: أقسام، منتجات، مزودين، "
        "أرقام، عروض، سوق مستخدمين، إعلانات، شحن وسحب رصيد، ونظام إشعارات.\n\n"
        "جميع الأسعار الداخلية بالدولار، ويمكن عرض ما يعادلها بالليرة حسب سعر الصرف اليومي الذي يحدده الأدمن.\n\n"
        "👨‍💻 حقوق البرمجة والتطوير: <b>اب سند الفلسطيني</b>\n"
        "📩 التواصل: @hefawe",
        reply_markup=_info_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:guide")
async def guide(callback: CallbackQuery):
    await callback.message.edit_text(
        "📖 <b>شرح الأزرار الرئيسية</b>\n\n"
        "📈 قسم الرشق: خدمات سوشيال مثل متابعين ولايكات ومشاهدات.\n"
        "🎮 شحن الألعاب: منتجات ألعاب تحتاج غالباً ID اللاعب.\n"
        "📱 شحن التطبيقات: شحن أو خدمات تطبيقات حسب المتاح.\n"
        "💳 الأرصدة والبطاقات: أرصدة، بطاقات، فيز، وقسائم.\n"
        "🔐 الاشتراكات الرقمية: اشتراكات، تراخيص، أدوات، VPN وغيرها.\n"
        "✅ توثيق الحسابات: خدمات يدوية أو تلقائية حسب العرض.\n"
        "🎟 الأكواد الرقمية: كود أو حساب يسلم فوراً إذا كان بالمخزون المشفر.\n\n"
        "💳 شحن الرصيد: إضافة رصيد عبر الطرق المتاحة.\n"
        "💸 سحب الرصيد: للتجار والمستخدمين لسحب أرباحهم.\n"
        "🏪 سوق المستخدمين: بيع وشراء بين المستخدمين بضمان البوت.\n"
        "🔥 العروض الخاصة 24: عروض مؤقتة يدوي/تلقائي لمدة 24 ساعة.\n"
        "📢 إعلاناتي: نشر إعلان مدفوع على البوت والقناة.\n"
        "🔔 الإشعارات: صندوق إشعاراتك وسجل العمليات المهمة.\n"
        "🛠 الدعم الفني: فتح تذكرة عند وجود مشكلة.",
        reply_markup=_info_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:market")
async def market_info(callback: CallbackQuery):
    await callback.message.edit_text(
        "🏪 <b>شرح سوق المستخدمين</b>\n\n"
        "السوق يسمح للمستخدمين ببيع أكواد، حسابات، خدمات أو عروض.\n"
        "البائع ينشئ حساب سوق باسم مستعار وكلمة سر.\n"
        "إذا وضع كود/حساب، يحفظه البوت مشفراً ولا يظهر إلا بعد شراء المشتري.\n\n"
        "بعد الشراء تظهر للمشتري أزرار:\n"
        "✅ المنتج يعمل وصحيح: ينتقل المبلغ للبائع فوراً وتُحتسب عمولة المنصة.\n"
        "⚠️ المعلومات خاطئة: يبقى المبلغ محجوزاً ويفتح نزاع للأدمن.\n\n"
        "إذا كسب المشتري النزاع يعود المال له. وإذا كانت العملية صحيحة يفرج الأدمن للبائع.",
        reply_markup=_info_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:numbers")
async def numbers_info(callback: CallbackQuery):
    await callback.message.edit_text(
        "📱 <b>شرح شراء الأرقام</b>\n\n"
        "تختار الخدمة ثم الدولة، ويعرض البوت السعر قبل التأكيد.\n"
        "بعد الشراء ينتظر البوت وصول الكود تلقائياً.\n\n"
        "✅ إذا وصل الكود يصلك فوراً.\n"
        "↩️ إذا لم يصل الكود أو انتهت المهلة، يتم استرجاع رصيدك تلقائياً.\n"
        "📦 الشراء بالجملة متاح إذا فعله الأدمن، ومعه استرجاع تلقائي لأي رقم يفشل.",
        reply_markup=_info_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:deposit")
async def deposit_info(callback: CallbackQuery):
    await callback.message.edit_text(
        "💳 <b>كيف تشحن حسابك؟</b>\n\n"
        "1) اضغط زر شحن الرصيد.\n"
        "2) اختر طريقة الدفع المتاحة: شام كاش، USDT، نجوم تيليجرام أو طرق أخرى.\n"
        "3) أدخل المبلغ أو اختر مبلغ جاهز.\n"
        "4) في الشحن اليدوي أرسل صورة الإثبات ورقم العملية.\n"
        "5) في الشحن التلقائي اتبع تعليمات الفاتورة ثم تحقق.\n\n"
        "بعد القبول أو الدفع الناجح يصل إشعار لك، ويضاف الرصيد بالدولار داخل البوت.",
        reply_markup=_info_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:withdraw")
async def withdraw_info(callback: CallbackQuery):
    await callback.message.edit_text(
        "💸 <b>كيف تسحب رصيدك؟</b>\n\n"
        "1) اضغط سحب الرصيد.\n"
        "2) اختر شام كاش أو USDT.\n"
        "3) اختر العملة أو الشبكة.\n"
        "4) أدخل المبلغ بالدولار ثم عنوان حسابك/محفظتك.\n"
        "5) يُحجز المبلغ ويرسل طلب للأدمن.\n"
        "6) عند الدفع يصلك إشعار، وإذا رُفض الطلب يرجع رصيدك تلقائياً.",
        reply_markup=_info_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:market_buy")
async def market_buy_info(callback: CallbackQuery):
    await callback.message.edit_text(
        "🛒 <b>كيف تشتري من سوق المستخدمين؟</b>\n\n"
        "ادخل السوق، اختر إعلاناً، اقرأ وصفه وملف البائع، ثم اضغط شراء.\n"
        "المبلغ لا يذهب للبائع مباشرة، بل يبقى محجوزاً عند البوت.\n"
        "إذا كان المنتج كوداً/حساباً مشفراً يظهر لك بعد الدفع.\n"
        "اضغط ✅ المنتج صحيح ليصل المال للبائع، أو ⚠️ المعلومات خاطئة لفتح نزاع.",
        reply_markup=_info_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:market_sell")
async def market_sell_info(callback: CallbackQuery):
    await callback.message.edit_text(
        "💰 <b>كيف تبيع في سوق المستخدمين؟</b>\n\n"
        "أول دخول للسوق يتطلب حساب سوق باسم مستعار وكلمة سر.\n"
        "بعدها اختر اعرض شيئاً للبيع، وحدد النوع والسعر والوصف والصور.\n"
        "إذا كان كوداً أو حساباً، ضع بيانات التسليم وسيحفظها البوت مشفرة.\n"
        "بعد البيع لا تستلم المال إلا بعد تأكيد المشتري أو قرار الأدمن.\n"
        "كل نجاح يرفع تقييمك، وكل معلومات خاطئة تضر حسابك في السوق.",
        reply_markup=_info_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:special")
async def special_info(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔥 <b>العروض الخاصة 24</b>\n\n"
        "هي عروض مؤقتة لمدة 24 ساعة. قد تكون تلقائية عبر مزود أو يدوية عبر الإدارة.\n"
        "ادخل العرض، اقرأ المطلوب، أرسل الرابط/ID/الرقم حسب التعليمات، ثم أكد الشراء.\n"
        "إذا فشل العرض التلقائي أو تأخر أكثر من ساعة يرجع رصيدك مع إشعار اعتذار.\n"
        "بعد نجاح الطلب يمكنك التصويت لتمديد العرض إذا كان ممتازاً.",
        reply_markup=_info_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:ads")
async def ads_info(callback: CallbackQuery):
    await callback.message.edit_text(
        "📢 <b>الإعلانات المدفوعة</b>\n\n"
        "من زر إعلاناتي يمكنك نشر إعلان على البوت وقناة الإشعارات.\n"
        "سعر الإعلان 2$ لمدة 24 ساعة بعد موافقة الأدمن.\n"
        "إذا رُفض الإعلان يعود المبلغ لرصيدك.\n"
        "بعد القبول يعاد نشر الإعلان بالقناة كل نصف ساعة، ويمكن تجديده بـ1$ لمدة 15 ساعة إضافية.",
        reply_markup=_info_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "info:notifications")
async def notifications_info(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔔 <b>الإشعارات</b>\n\n"
        "مركز الإشعارات يحفظ أهم رسائل الطلبات، السوق، الشحن، السحب، العروض والدعم.\n"
        "يمكنك تعليم الإشعارات كمقروءة وتعديل تفضيلاتك.\n"
        "الإشعارات المالية والأمنية لا يمكن تعطيلها لحماية حسابك.",
        reply_markup=_info_kb(),
    )
    await callback.answer()
