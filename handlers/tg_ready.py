"""واجهة الزبون لقسم الجلسات الجاهزة (داخل أرقام تلجرام).

العرض مفرز تلقائياً حسب الدولة (علم + سعر + مخزون حي).
كل عملية شراء ناجحة تنقص المخزون تلقائياً (العنصر يُعلَّم مباعاً).
"""

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy import select

from database.models import TgReadyCountry, TgReadyItem, TransactionType
from keyboards.main_menu import back_to_main_kb, insufficient_balance_kb
from keyboards.tg_ready import (
    tg_ready_after_kb,
    tg_ready_confirm_kb,
    tg_ready_countries_kb,
    tg_ready_owned_kb,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.currency_service import CurrencyService
from services.encryption_service import EncryptionService
from services.notification_service import NotificationService
from services.tg_ready_service import TgReadyService

router = Router(name="tg_ready")


@router.callback_query(F.data == "tgready:list")
async def tg_ready_list(callback: CallbackQuery, session, db_user=None):
    countries = await TgReadyService.stock_overview(session)
    if not countries:
        await callback.message.edit_text(
            "📦 <b>حسابات تلجرام جاهزة — جلسات</b>\n\n"
            "❌ لا يوجد مخزون متاح حالياً.\nيرجى المحاولة لاحقاً.",
            reply_markup=back_to_main_kb(),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "📦 <b>حسابات تلجرام جاهزة — جلسات</b>\n\n"
        "اختر الدولة: السعر يشمل الحساب كاملاً مع بيانات الدخول.\n"
        "التسليم فوري والمخزون ينقص تلقائياً بعد كل شراء.",
        reply_markup=tg_ready_countries_kb(countries),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tgready:country:"))
async def tg_ready_country(callback: CallbackQuery, session, db_user=None):
    key = callback.data.rsplit(":", 1)[-1]
    country = await session.get(TgReadyCountry, key)
    countries = await TgReadyService.stock_overview(session)
    entry = next((c for c in countries if c["key"] == key), None)
    if entry is None:
        await callback.answer("⚠️ نفد مخزون هذه الدولة.", show_alert=True)
        await tg_ready_list(callback, session, db_user)
        return
    price_display = await CurrencyService.format_dual(entry["price"], db_user, session)
    await callback.message.edit_text(
        f"{entry['flag']} <b>{entry['name']}</b>\n\n"
        f"💰 <b>السعر:</b> <b>{price_display}</b>\n"
        f"📦 <b>المتاح الآن:</b> <b>{entry['stock']}</b>\n\n"
        "📎 <b>ستستلم:</b> الرقم + بيانات الجلسة (tdata/session) + كلمة 2FA إن وجدت.\n"
        "⚡️ التسليم فوري بعد التأكيد.",
        reply_markup=tg_ready_confirm_kb(key),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tgready:buy:"))
async def tg_ready_buy(callback: CallbackQuery, session, db_user, bot):
    key = callback.data.rsplit(":", 1)[-1]
    countries = await TgReadyService.stock_overview(session)
    entry = next((c for c in countries if c["key"] == key), None)
    if entry is None:
        await callback.answer("❌ نفد المخزون.", show_alert=True)
        return
    price = entry["price"]
    if db_user.balance < price:
        notifier = NotificationService(bot)
        await notifier.notify_insufficient_balance(
            user_telegram_id=db_user.telegram_id,
            required_usd=str(price),
            current_balance_usd=f"{db_user.balance:.2f}",
            reply_markup=insufficient_balance_kb(),
        )
        return
    await callback.answer("⏳ جاري حجز حسابك...")
    try:
        await BalanceService.deduct_balance(
            session,
            db_user.id,
            price,
            TransactionType.PURCHASE,
            description=f"شراء جلسة تلجرام جاهزة - {entry['name']}",
            is_purchase=True,
        )
    except InsufficientBalanceError:
        await callback.message.answer("⚠️ رصيدك غير كافٍ.")
        return

    item, _ = await TgReadyService.buy_one(session, db_user.id, key)
    if item is None:
        # نفد أثناء الدفع — استرجاع فوري
        await BalanceService.add_balance(
            session, db_user.id, price, TransactionType.REFUND,
            description="استرجاع - نفد مخزون الجلسات",
        )
        await callback.message.answer("❌ نفد المخزون للتو، تم استرجاع رصيدك كاملاً.")
        return

    try:
        payload = EncryptionService.decrypt(item.payload_encrypted) if item.payload_encrypted else item.phone_number
    except Exception:
        payload = item.phone_number

    # عدّاد المخزون المتبقي بعد الشراء (النقصان التلقائي)
    remaining = [c for c in await TgReadyService.stock_overview(session) if c["key"] == key]
    left = remaining[0]["stock"] if remaining else 0

    import json as _json

    from services.tg_ready_service import (
        build_account_zip,
        download_file_bytes,
        extract_code_link,
        extract_file_link,
        extract_twofa,
    )

    code_link = extract_code_link(payload)
    file_link = extract_file_link(payload)
    twofa = extract_twofa(payload)
    code_line = f"\n🔑 <b>رابط الكود:</b> {code_link}" if code_link else ""
    file_line = f"\n📁 <b>رابط ملف الجلسة ZIP:</b> {file_link}" if file_link else ""
    twofa_line = f"\n🔐 <b>كلمة التحقق 2FA:</b> <code>{twofa}</code>" if twofa else ""
    try:
        rel_paths = _json.loads(item.files_json) if item.files_json else []
    except (ValueError, TypeError):
        rel_paths = []

    if rel_paths:
        how_to = (
            "📁 <b>طريقة الدخول (بدون كود):</b>\n"
            "1) حمّل ملف الـ ZIP تحت وفك ضغطه\n"
            "2) حط مجلد الجلسة (tdata) جنب برنامج تيليجرام ديسكتوب وافتحه — بيدخل مباشرة\n"
            "3) إذا طلب كلمة 2FA بتلاقيها فوق بسطر البيانات"
        )
    elif file_link:
        how_to = (
            "📁 <b>طريقة الدخول بملف الجلسة:</b>\n"
            "1) اضغط رابط ملف الجلسة فوق — بينزل عندك ملف ZIP\n"
            "2) فك ضغطه وحط مجلد الجلسة (tdata) جنب تيليجرام ديسكتوب — بيدخل مباشرة بلا كود\n"
            "3) إذا تيليجرام طلب كود دخول: اضغط زر «📩 طلب الكود» تحت والبوت بيجيب الكود جاهز من رابط الكود\n"
            "4) إذا طلب كلمة تحقق 2FA بتلاقيها فوق"
        )
    else:
        how_to = (
            "🔢 <b>طريقة الدخول بالرقم:</b>\n"
            "1) افتح تيليجرام وحط الرقم فوق\n"
            f"2) اضغط زر «📩 طلب الكود» تحت — البوت بيجيب الكود جاهز وبيرسله لك{'' if code_link else ' (إن توفر)'}\n"
            "3) حطه بتيليجرام ثم كلمة 2FA إن طُلبت"
        )

    await callback.message.answer(
        f"✅ <b>تم الشراء بنجاح!</b>\n\n"
        f"{item.flag} <b>{item.country_name_ar}</b>\n"
        f"📱 الرقم: <code>{item.phone_number}</code>\n"
        f"💰 السعر: <b>{price}$</b>\n"
        f"📦 المتبقي من هذه الدولة: <b>{left}</b>\n\n"
        f"📎 <b>بيانات الجلسة:</b>\n<code>{payload}</code>"
        f"{file_line}{code_line}{twofa_line}\n\n"
        f"{how_to}\n\n"
        "⚠️ سجّل الدخول فوراً واحفظ البيانات. الدعم خلال 24 ساعة للاستبدال.",
        reply_markup=tg_ready_owned_kb(item.id),
    )
    # تسليم الملفات: أرشيف ZIP بملفات الجلسة الفعلية إن وُجدت،
    # وإلا تحميل ZIP من رابط الملف مباشرة، وإلا ملف نصي.
    try:
        built = build_account_zip(item.phone_number, rel_paths) if rel_paths else None
        if built is not None:
            fname, blob = built
            await bot.send_document(
                chat_id=db_user.telegram_id,
                document=BufferedInputFile(blob, filename=fname),
                caption="📁 ملفات الجلسة — فك الضغط وسجّل الدخول مباشرة بلا كود",
            )
        elif file_link:
            blob = await download_file_bytes(file_link)
            if blob is not None:
                fname = f"telegram_session_{item.phone_number.replace('+', '')}.zip"
                # إن لم يكن zip فعلياً أرسله كما هو مع تنبيه
                await bot.send_document(
                    chat_id=db_user.telegram_id,
                    document=BufferedInputFile(blob, filename=fname),
                    caption="📁 ملف الجلسة من رابط البائع — فك الضغط وسجّل الدخول مباشرة",
                )
            else:
                await bot.send_document(
                    chat_id=db_user.telegram_id,
                    document=BufferedInputFile(
                        f"رقم: {item.phone_number}\nالدولة: {item.country_name_ar}\n\n{payload}\n".encode("utf-8-sig"),
                        filename=f"telegram_session_{item.phone_number.replace('+', '')}.txt",
                    ),
                    caption="📎 ملف بيانات الجلسة — رابط ZIP بالداخل، اضغط عليه للتحميل",
                )
        else:
            await bot.send_document(
                chat_id=db_user.telegram_id,
                document=BufferedInputFile(
                    f"رقم: {item.phone_number}\nالدولة: {item.country_name_ar}\n\n{payload}\n".encode("utf-8-sig"),
                    filename=f"telegram_session_{item.phone_number.replace('+', '')}.txt",
                ),
                caption="📎 ملف بيانات الجلسة — احتفظ به بمكان آمن",
            )
    except Exception:
        pass

    try:
        notifier = NotificationService(bot)
        await notifier.notify_admin(
            "📦 <b>بيع جلسة جاهزة</b>\n\n"
            f"👤 {db_user.telegram_id} (@{db_user.username or '-'})\n"
            f"{item.flag} {item.country_name_ar}\n"
            f"📱 <code>{item.phone_number}</code>\n"
            f"💰 {price}$ | المتبقي: {left}"
        )
    except Exception:
        pass


# ── زر طلب الكود: البوت يجيب الكود جاهز من رابط الكود + يرسل 2FA ──

_CODE_COOLDOWN: dict[tuple[int, int], float] = {}
_CODE_COOLDOWN_SECONDS = 20


@router.callback_query(F.data.startswith("tgready:code:"))
async def tg_ready_request_code(callback: CallbackQuery, session, db_user, bot):
    import time as _time

    try:
        item_id = int(callback.data.rsplit(":", 1)[-1])
    except (ValueError, TypeError):
        await callback.answer("⚠️ طلب غير صالح.", show_alert=True)
        return
    item = await session.get(TgReadyItem, item_id)
    if item is None or item.buyer_user_id != db_user.id:
        await callback.answer("⚠️ هذا الرقم ليس لك.", show_alert=True)
        return

    now = _time.monotonic()
    last = _CODE_COOLDOWN.get((db_user.id, item_id), 0.0)
    if now - last < _CODE_COOLDOWN_SECONDS:
        await callback.answer(
            f"⏳ انتظر {int(_CODE_COOLDOWN_SECONDS - (now - last))} ثانية قبل طلب الكود مجدداً.",
            show_alert=True,
        )
        return
    _CODE_COOLDOWN[(db_user.id, item_id)] = now
    await callback.answer("⏳ جاري جلب الكود...")

    try:
        payload = EncryptionService.decrypt(item.payload_encrypted) if item.payload_encrypted else item.phone_number
    except Exception:
        payload = item.phone_number

    from services.tg_ready_service import extract_twofa, fetch_code_for_payload

    twofa = extract_twofa(payload or "")
    result = await fetch_code_for_payload(payload or "")
    codes = result.get("codes") or []
    code_url = result.get("code_url")
    err = result.get("error") or ""

    if codes:
        best = codes[0]
        extra = f"\n🔐 <b>كلمة التحقق 2FA:</b> <code>{twofa}</code>" if twofa else ""
        if len(codes) > 1:
            extra += f"\n📋 كل الأكواد بالصفحة: <code>{'، '.join(codes[:5])}</code>"
        await callback.message.answer(
            f"📩 <b>كود الدخول جاهز!</b>\n\n"
            f"📱 الرقم: <code>{item.phone_number}</code>\n"
            f"🔢 الكود: <code>{best}</code>\n"
            f"{extra}\n\n"
            "انسخ الكود وحطه بتيليجرام فوراً.",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        return

    if err == "no_code_link":
        await callback.message.answer(
            f"⚠️ لا يوجد رابط كود مخزن لهذا الرقم <code>{item.phone_number}</code>.\n"
            "تواصل مع الدعم ليرسل لك الكود يدوياً.",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        # اسمح بإعادة المحاولة فوراً عند غياب الرابط (لا فائدة من الانتظار)
        _CODE_COOLDOWN.pop((db_user.id, item_id), None)
        return
    if err == "fetch_failed":
        await callback.message.answer(
            f"⚠️ تعذّر فتح رابط الكود الآن.\n🔑 رابط الكود: {code_url}\n"
            "افتحه يدوياً أو اضغط طلب الكود مجدداً بعد قليل.",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        return
    # no_code_yet: الصفحة انفتحت لكن لا كود بعد
    await callback.message.answer(
        f"⏳ لم يصل الكود بعد للرقم <code>{item.phone_number}</code>.\n"
        f"🔑 رابط الكود: {code_url}\n"
        "انتظر قليلاً ثم اضغط «📩 طلب الكود» مجدداً — البوت بيجيبه فور توفره."
        + (f"\n🔐 كلمة التحقق 2FA: <code>{twofa}</code>" if twofa else ""),
        reply_markup=tg_ready_owned_kb(item.id),
    )


@router.callback_query(F.data.startswith("tgready:file:"))
async def tg_ready_resend_file(callback: CallbackQuery, session, db_user, bot):
    try:
        item_id = int(callback.data.rsplit(":", 1)[-1])
    except (ValueError, TypeError):
        await callback.answer("⚠️ طلب غير صالح.", show_alert=True)
        return
    item = await session.get(TgReadyItem, item_id)
    if item is None or item.buyer_user_id != db_user.id:
        await callback.answer("⚠️ هذا الرقم ليس لك.", show_alert=True)
        return
    await callback.answer("⏳ جاري تجهيز ملف الجلسة...")

    import json as _json

    from services.tg_ready_service import (
        build_account_zip,
        download_file_bytes,
        extract_file_link,
    )

    try:
        payload = EncryptionService.decrypt(item.payload_encrypted) if item.payload_encrypted else item.phone_number
    except Exception:
        payload = item.phone_number
    try:
        rel_paths = _json.loads(item.files_json) if item.files_json else []
    except (ValueError, TypeError):
        rel_paths = []

    try:
        built = build_account_zip(item.phone_number, rel_paths) if rel_paths else None
        if built is not None:
            fname, blob = built
            await bot.send_document(
                chat_id=db_user.telegram_id,
                document=BufferedInputFile(blob, filename=fname),
                caption="📁 ملفات الجلسة — فك الضغط وسجّل الدخول مباشرة بلا كود",
            )
            return
        file_link = extract_file_link(payload or "")
        if file_link:
            blob = await download_file_bytes(file_link)
            if blob is not None:
                await bot.send_document(
                    chat_id=db_user.telegram_id,
                    document=BufferedInputFile(
                        blob, filename=f"telegram_session_{item.phone_number.replace('+', '')}.zip"
                    ),
                    caption=f"📁 ملف الجلسة للرقم <code>{item.phone_number}</code>",
                    parse_mode="HTML",
                )
                return
            await callback.message.answer(
                f"📁 رابط ملف الجلسة:\n{file_link}\n\nاضغط عليه — بينزل عندك ملف ZIP.",
                reply_markup=tg_ready_owned_kb(item.id),
            )
            return
        await callback.message.answer("⚠️ لا يوجد ملف جلسة مخزن لهذا الرقم.")
    except Exception:
        await callback.message.answer("⚠️ تعذّر إرسال الملف الآن، حاول مجدداً.")
