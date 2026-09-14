"""
لوحة تحكم الأدمن الرئيسية.
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from keyboards.admin import ADMIN_TABS, admin_main_kb, admin_maintenance_kb, admin_tab_kb
from services.settings_service import SettingsService
from states.states import AdminMaintenanceStates
from filters.admin_filter import IsAdmin

router = Router(name="admin_panel")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.message(Command("admin"))
async def admin_entry(message: Message):
    await message.answer(
        "🛠 <b>لوحة تحكم الأدمن</b>\n\n"
        "اختر أحد التبويبات الرئيسية للوصول السريع بدون ازدحام.",
        reply_markup=admin_main_kb(),
    )


@router.callback_query(F.data == "admin:main")
async def admin_main_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "🛠 <b>لوحة تحكم الأدمن</b>\n\n"
        "اختر تبويباً رئيسياً لإدارة القسم المطلوب بدل قائمة طويلة مزدحمة.",
        reply_markup=admin_main_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:tab:"))
async def admin_tab_callback(callback: CallbackQuery):
    tab = (callback.data or "").rsplit(":", 1)[-1]
    if tab not in ADMIN_TABS:
        await callback.answer("تبويب غير معروف.", show_alert=True)
        return
    title, items = ADMIN_TABS[tab]
    bullets = "\n".join(f"• {label}" for label, _data in items)
    await callback.message.edit_text(
        f"{title}\n\n{bullets}\n\nاختر من الأزرار بالأسفل:",
        reply_markup=admin_tab_kb(tab),
    )
    await callback.answer()


# ══════════════ آخر التحديثات والإضافات ══════════════

# كل ما أُنجز في التحديث الكبير الحالي — يُعرض للأدمن من زر
# «🆕 آخر التحديثات والإضافات». أضف أي مرحلة جديدة هنا.
CHANGELOG_ENTRIES: list[tuple[str, str, str]] = [
    (
        "🤖",
        "قسمان رئيسيان: الذكاء الاصطناعي + واتساب",
        "زران جديدان في القائمة الرئيسية:\n"
        "• <b>🤖 الذكاء الاصطناعي</b>: أقسام تُدار بالكامل من اللوحة — "
        "جاهزان «👨‍💻 برمجة بدون قيود» (الرد يوصل كملف) و«💬 تحدث بدون "
        "قيود»، وأضف أي قسم ثالث بمعالج من 9 خطوات: شرح يدوي، موديل "
        "NanoGPT لكل قسم، وتسعير (تكلفة المزود الفعلية × مضاعف الربح "
        "افتراضياً ×3 أو سعر ثابت)، مع جلسات محفوظة يستطيع المستخدم "
        "والأدمن الرجوع إليها. يظهر الزر بعد ضبط مفتاح NanoGPT.\n"
        "• <b>📱 واتساب</b>: اشتراك يومي 1$ (قابل للتعديل)، ربط رقم واتساب "
        "بكود اقتران عبر سيرفر جسر بجانب البوت الثاني، وبعد الربط تظهر "
        "أزرار البوت الثاني داخل هذا البوت وكل أوامره تعمل من هنا. "
        "العقد والتنفيذ المرجعي في docs/whatsapp_bridge_v1_ar.md.",
    ),
    (
        "🚀",
        "منتجات قسم الرشق — شاشة تحكم واحدة",
        "زر جديد «🚀 منتجات قسم الرشق» في تبويب «المتجر والخدمات والرشق»: "
        "يعرض كل التطبيقات التي فيها منتجات مع عدد منتجات كل تطبيق، ثم "
        "أقسامه الفرعية (متابعون/لايكات/مشاهدات…)، ثم منتجات القسم. داخل "
        "القسم: ضغطة على المنتج تعطّله أو تفعّله، و🗑 تحذفه بتأكيد، و«💵 "
        "نسبة الربح» تُطبَّق فوراً على كل منتجات القسم (تُعاد الأسعار من "
        "التكلفة)، مع أزرار تفعيل/تعطيل كل المنتجات دفعة واحدة وحذفها كلها "
        "مع بقاء الأقسام. وكل منتج يعرض سعره عند المزود وسعره عندنا والربح "
        "واسم المزود وآيدي الخدمة عنده.",
    ),
    (
        "📥",
        "السحب التلقائي: أرخص 10 بدل 5 وبلا خدمات صفرية",
        "البناء التلقائي لأقسام الرشق صار ينشر أرخص <b>10</b> خدمات في كل "
        "قسم داخلي بدل 5، ولم يعد ينشر أي خدمة بلا سعر (أسطر «سيرفر 1» "
        "وعناوين الأقسام التي تصل من المزود بسعر 0$)، ويظهر عددها في تقرير "
        "البناء. وللمخلّفات القديمة: زر «🧼 حذف المنتجات بلا سعر» داخل "
        "شاشة منتجات الرشق.",
    ),
    (
        "💵",
        "هامش ربح لكل تطبيق/قسم فرعي في كل قسم",
        "صار هامش القسم الفرعي (وكل خدمة بداخله مثل لايكات انستا أو "
        "متابعين تيك توك) يتحكم بأسعار منتجاتها فعلياً حتى لو كانت "
        "المنتجات سحوبة تلقائياً. من «إدارة الأقسام» ← أي قسم فرعي ← "
        "«💵 هامش ربح هذا القسم»: تضبط نسبته ويعاد حساب أسعار كل منتجات "
        "القسم فوراً، وهامش المنتج الذي يضبطه الأدمن يدوياً يبقى محترماً.",
    ),
    (
        "🧹",
        "حذف منتجات أي قسم بضغطة — مع إبقاء الأقسام الفرعية",
        "من «إدارة الأقسام»: عند فتح أي قسم رئيسي أو فرعي يوجد زر «🧹 حذف "
        "كل منتجات القسم». يحذف المنتجات فقط (القسم الفرعي والأقسام الداخلية "
        "تبقى سليمة) حتى تعيد السحب بضغطة واحدة. يعمل على كل الأقسام: رشق، "
        "ألعاب، تطبيقات، وأي قسم مخصص.",
    ),
    (
        "🖥",
        "السيرفرات/المزودين المتعددين لقسم الأرقام",
        "كل خدمة أرقام يمكن أن تحوي عدة سيرفرات (مزودين). قبل أن يرى "
        "المستخدم الدول يختار السيرفر الذي يناسبه، وكل سيرفر مربوط بمزود "
        "مستقل. من لوحة الأدمن: «إدارة خدمات الأرقام» ← أي خدمة ← «⚙️ "
        "السيرفرات/المزودين التابعين» لإضافة/تعطيل/تغيير المزود أو إنشاء "
        "سيرفر لكل مزود مضبوط تلقائياً — بلا تعديل كود.",
    ),
    (
        "🖥",
        "السيرفرات العامة — لكل الأقسام (متجر/رشق/ألعاب/أرقام)",
        "لم يعد نظام السيرفرات خاصاً بالأرقام فقط. من زر «🖥 السيرفرات "
        "العامة» في تبويب المتجر يضيف الأدمن سيرفراً جديداً مرتبطاً "
        "بقسم رئيسي أو فرعي أو بكل الأقسام، ويربطه بمزود (متجر/رشق/ألعاب) "
        "ويضبط نسبة ربح مستقلة. السيرفر يظهر تلقائياً للمستخدم قبل عرض "
        "المنتجات، ويُطبق هامش ربحه عند الشراء تلقائياً.",
    ),
    (
        "🔌",
        "إدارة «الخدمات المسحوبة» لكل مزود لحاله",
        "تبويب «المتجر والخدمات والرشق» في لوحة الأدمن أُعيد ترتيبه. "
        "داخل «الخدمات المسحوبة» يوجد الآن زر «إدارة كل مزود لحاله»: "
        "لكل مزود شاشة فيها بحث خاص به، كتالوجه كاملاً الأرخص أولاً، "
        "نشر أرخص 10 لكل نوع ضمن قسم الرشق، ومزامنة كاملة كقسم باسمه، "
        "و«مسح كل منتجات المزود من المتجر» ثم إعادة السحب بضغطة واحدة.",
    ),
    (
        "🧹",
        "إصلاح أخطاء «بعد أول ضغطة»",
        "أصبحت الأزرار القديمة (عرض المنتجات/إضافة منتج من شاشة القسم) "
        "تعمل بدل «زر غير معالج». أُصلح Error: KeyError عند إرسال Player ID "
        "لخدمة منتهية الجلسة، وحادثة «الرسالة القديمة لم تعد قابلة للتعديل» — "
        "الآن يرسل البوت رسالة جديدة بدل أن يفشل.",
    ),
    (
        "🛍",
        "زر المتجر الرئيسي",
        "زر «متجر» في القائمة الرئيسية يعرض كل الأقسام، "
        "مع زر «🛍 التحكم بالمتجر» لإظهاره/إخفائه عن المستخدمين.",
    ),
    (
        "🔢",
        "شبكة الأرقام",
        "25 رقم في الصفحة جنباً إلى جنب (شبكة مربعة) من الأرخص للأغلى، "
        "مع ترجمة أسماء الدول مع العلم والسعر.",
    ),
    (
        "📡",
        "لوحة التوفر",
        "لوحة تعرض الأرقام المتاحة لكل خدمة لحظياً.",
    ),
    (
        "💼",
        "برنامج الوكلاء",
        "كود إحالة (AGENT-XXXX-XXXX) يعطي خصماً على كل الخدمات، "
        "مع عمولة وإلغاء تلقائي أسبوعياً إن لم تحقق الحد الأدنى.",
    ),
    (
        "💵",
        "تحكم كامل بهوامش الربح",
        "هامش مستقل لكل منتج/قسم فرعي/قسم/عام، ويُطبق بالترتيب: "
        "المنتج ← القسم الفرعي ← القسم ← العام.",
    ),
    (
        "🌍",
        "ترجمة الخدمات المسحوبة تلقائياً",
        "كل خدمة تسحبها من أي مزود تُترجم للعربية عند إضافتها، "
        "بدون التأثير على الاتصال بالمزود — والأسماء المجهولة تبقى كما هي.",
    ),
    (
        "🎛",
        "سلطة كاملة على الأقسام والمنتجات",
        "شرح قابل للتعديل من الأدمن لكل قسم (رئيسي/فرعي/داخلي) ولكل "
        "خدمة، ويظهر للمستخدم، مع إمكانية إضافة قسم داخلي داخل أي قسم فيه منتجات.",
    ),
    (
        "🏬",
        "ربط أي متجر خارجي (Hyper Store)",
        "اربط أي متجر خارجي فيه API من اللوحة بدون تعديل كود البوت. "
        "زر «🏬 مزامنة متجر كامل ← قسم باسم المتجر» يسحب كل خدماته "
        "لقسم باسمه (بالعربية، التكلفة + الهامش، من الأرخص للأغلى) "
        "وإعادة المزامنة لا تكرّر.",
    ),
    (
        "🖐",
        "المنتجات اليدوية",
        "أنشئ منتجاً يدوياً: المشتري يُشعر أن طلبه يدوي وسيُنفذ لاحقاً، "
        "وقناة الإدارة تنبّه بأزرار قبول/رفض — قبول = إشعار المستخدم "
        "بالتنفيذ، رفض = استرجاع + إشعار.",
    ),
    (
        "🛍",
        "الاشتراكات الرقمية برصد ميزان المزود",
        "قبل إرسال الطلب يُفحص رصيد المزود: إن كان غير كافٍ لا يُرسل "
        "والمشتري يُشعر أن الكود سيصل خلال دقائق، وتُنبه القناة بأزرار "
        "نعم/لا — نعم = أرسل البيانات يدوياً ويُشعر المشتري، "
        "لا = إلغاء + استرجاع.",
    ),
]


def _changelog_chunks(entries: list[tuple[str, str, str]]) -> list[str]:
    """يقسّم التحديثات إلى رسائل ضمن حد تيليجرام."""
    header = "🆕 <b>آخر التحديثات والإضافات</b>\n\n"
    chunks: list[str] = []
    current = header
    limit = 4000
    for emoji, title, desc in entries:
        block = f"{emoji} <b>{title}</b>\n{desc}\n\n"
        if len(current) + len(block) > limit and len(current) > len(header):
            chunks.append(current.rstrip())
            current = header
        current += block
    chunks.append(current.rstrip())
    return chunks


@router.callback_query(F.data == "admin:changelog")
async def admin_changelog(callback: CallbackQuery):
    await callback.answer()
    chunks = _changelog_chunks(CHANGELOG_ENTRIES)
    first = chunks[0]
    if len(chunks) == 1:
        await callback.message.edit_text(
            first,
            reply_markup=admin_main_kb(),
        )
        return
    # أكثر من رسالة: الأخيرة تحمل زر الرجوع للوحة
    for text in chunks[1:-1]:
        await callback.message.answer(text)
    last = chunks[-1] + "\n\n🔙 <b>للعودة للوحة الرئيسية</b>"
    await callback.message.answer(
        last,
        reply_markup=admin_main_kb(),
    )


# ══════════════ وضع الصيانة ══════════════


@router.callback_query(F.data == "admin:maintenance")
async def maintenance_menu(callback: CallbackQuery):
    is_active = await SettingsService.get_bool("maintenance_mode", False)
    current_msg = await SettingsService.get("maintenance_message", "⚙️ البوت تحت الصيانة حالياً...")
    status = "🔴 مفعّل" if is_active else "🟢 غير مفعّل"
    await callback.message.edit_text(
        f"🔧 <b>وضع الصيانة</b>\n\n"
        f"الحالة: {status}\n\n"
        f"📝 رسالة الصيانة الحالية:\n"
        f"<i>{current_msg}</i>",
        reply_markup=admin_maintenance_kb(is_active),
    )


@router.callback_query(F.data == "admin:maintenance_on")
async def maintenance_on(callback: CallbackQuery, session):
    await SettingsService.set(session, "maintenance_mode", "true")
    await callback.answer("✅ تم تفعيل وضع الصيانة.")
    await maintenance_menu(callback)


@router.callback_query(F.data == "admin:maintenance_off")
async def maintenance_off(callback: CallbackQuery, session):
    await SettingsService.set(session, "maintenance_mode", "false")
    await callback.answer("✅ تم إيقاف وضع الصيانة.")
    await maintenance_menu(callback)


@router.callback_query(F.data == "admin:maintenance_msg")
async def maintenance_msg_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📝 أرسل رسالة الصيانة الجديدة:")
    await state.set_state(AdminMaintenanceStates.waiting_message)


@router.message(AdminMaintenanceStates.waiting_message)
async def maintenance_msg_received(message: Message, state: FSMContext, session):
    await SettingsService.set(
        session,
        "maintenance_message",
        message.text.strip(),
    )
    await message.answer(
        "✅ تم تحديث رسالة الصيانة.",
        reply_markup=admin_main_kb(),
    )
    await state.clear()
