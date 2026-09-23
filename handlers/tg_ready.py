"""واجهة الزبون لقسم الجلسات الجاهزة (داخل أرقام تلجرام).

العرض مفرز تلقائياً حسب الدولة (علم + سعر + مخزون حي).
كل عملية شراء ناجحة تنقص المخزون تلقائياً (العنصر يُعلَّم مباعاً).
"""

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from database.models import TgReadyItem, TransactionType
from keyboards.main_menu import back_to_main_kb, insufficient_balance_kb
from keyboards.tg_ready import (
    tg_ready_confirm_kb,
    tg_ready_countries_kb,
    tg_ready_owned_kb,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.currency_service import CurrencyService
from services.encryption_service import EncryptionService
from services.notification_service import NotificationService
from services.tg_ready_service import TgReadyService, reveal_payload

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

    payload = reveal_payload(item.payload_encrypted, item.phone_number)

    # عدّاد المخزون المتبقي بعد الشراء (النقصان التلقائي)
    remaining = [c for c in await TgReadyService.stock_overview(session) if c["key"] == key]
    left = remaining[0]["stock"] if remaining else 0

    import json as _json

    from services.tg_ready_service import (
        build_account_zip,
        download_file_bytes,
        extract_file_link,
        extract_twofa,
    )

    file_link = extract_file_link(payload)
    twofa = extract_twofa(payload)
    try:
        rel_paths = _json.loads(item.files_json) if item.files_json else []
    except (ValueError, TypeError):
        rel_paths = []

    # رسالة مبسطة: الرقم + خطوتان فقط (بدون إغراق المستخدم بالروابط الخام).
    # ملف ZIP يُرسل تلقائياً بالأسفل، والكود يُجلب بزر «طلب الكود».
    has_session_file = bool(rel_paths) or bool(file_link)
    twofa_line = f"\n🔐 <b>كلمة 2FA:</b> <code>{twofa}</code>" if twofa else ""
    if has_session_file:
        how_to = (
            "📁 <b>الدخول بدون كود (الأسهل):</b> حمّل ملف الـ ZIP بالأسفل وفك ضغطه — "
            "ستجد ملف <code>.session</code> + مجلد <code>tdata</code> + كلمة 2FA بملف <code>2FA.txt</code>."
            f"{twofa_line}\n\n"
            "🔢 <b>أو الدخول بالرقم:</b>\n"
            "1) افتح تيليجرام وأدخل الرقم فوق واطلب الكود حتى ترى شاشة إدخال الكود\n"
            "2) اضغط زر «📩 طلب الكود» هنا — البوت يجيبه لك تلقائياً"
        )
    else:
        how_to = (
            "🔢 <b>طريقة الدخول:</b>\n"
            "1) افتح تيليجرام وأدخل الرقم فوق واطلب الكود حتى ترى شاشة إدخال الكود\n"
            "2) اضغط زر «📩 طلب الكود» هنا — البوت يجيبه لك تلقائياً"
            f"{twofa_line}"
        )

    await callback.message.answer(
        f"✅ <b>تم الشراء بنجاح!</b>\n\n"
        f"{item.flag} <b>{item.country_name_ar}</b>\n"
        f"📱 الرقم: <code>{item.phone_number}</code>\n"
        f"💰 السعر: <b>{price}$</b>\n"
        f"📦 المتبقي من هذه الدولة: <b>{left}</b>\n\n"
        f"{how_to}\n\n"
        "⚠️ سجّل الدخول فوراً. الدعم خلال 24 ساعة للاستبدال.",
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


# ── زر طلب الكود: الجلسة (Telethon) أولاً ثم احتياطي رابط الكود ──

_CODE_COOLDOWN: dict[tuple[int, int], float] = {}
_CODE_COOLDOWN_SECONDS = 20
# عناصر قيد جلب كودها الآن — لمنع الضغط المزدوج أثناء الانتظار الطويل
_CODE_INFLIGHT: set[int] = set()


async def _send_code_result(callback: CallbackQuery, item, codes: list[str], twofa_show) -> None:
    """إرسال رسالة الكود الناجحة (قالب موحّد لكل المسارات)."""
    best = codes[0]
    extra = f"\n🔐 <b>كلمة 2FA:</b> <code>{twofa_show}</code>" if twofa_show else ""
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


async def _deliver_login_code(callback: CallbackQuery, session, db_user, item) -> None:
    """جلب كود الرقم وارساله للزبون: الجلسة أولاً ثم رابط الكود احتياطاً."""
    import json as _json

    from services.session_login_service import fetch_code_via_session, get_session_assets
    from services.tg_ready_service import extract_twofa, fetch_code_for_payload

    payload = reveal_payload(item.payload_encrypted, item.phone_number)

    # ── أصول الجلسة (.session من الملفات المحلية أو من ZIP عبر file_link) ──
    assets = await get_session_assets(item, payload or "")
    if assets.cached:
        # حفظ المسارات المحلية الجديدة حتى تعمل إعادة التسليم لاحقاً بدون تحميل
        try:
            rels = _json.loads(item.files_json) if item.files_json else []
            if not isinstance(rels, list):
                rels = []
            rels.extend(p for p in assets.cached if p not in rels)
            item.files_json = _json.dumps(rels, ensure_ascii=False)
            await session.commit()
        except Exception:
            pass

    twofa = assets.twofa or extract_twofa(payload or "")

    # ── المسار الأساسي: فتح الجلسة بـ Telethon وجلب كود 777000 ──
    sess_err = ""
    sess_retry = 0
    if assets.session_bytes:

        async def _on_wait() -> None:
            # رسالة مؤقتة قبل الانتظار — تُبتلع أخطاؤها حتى لا تُفشل الجلب
            try:
                extra = f"\n🔐 كلمة 2FA: <code>{twofa}</code>" if twofa else ""
                await callback.message.answer(
                    f"⏳ بانتظار كود الدخول للرقم <code>{item.phone_number}</code>…\n\n"
                    "افتح تيليجرام وأدخل الرقم واضغط التالي حتى ترى شاشة إدخال الكود —\n"
                    "البوت ينتظر الكود هنا حتى 40 ثانية ويرسله فور وصوله."
                    f"{extra}"
                )
            except Exception:
                pass

        sres = await fetch_code_via_session(
            assets.session_bytes, twofa=twofa, on_wait=_on_wait
        )
        if sres.get("ok"):
            await _send_code_result(callback, item, [str(sres["code"])], twofa)
            return
        sess_err = str(sres.get("error") or "")
        sess_retry = int(sres.get("retry_after") or 0)
    else:
        sess_err = str(assets.error or "no_session")

    # ── احتياطي: رابط الكود HTTP (dl_cloude وغيرها) ──
    result = await fetch_code_for_payload(payload or "", timeout_s=15)
    codes = [str(c) for c in (result.get("codes") or [])]
    err = str(result.get("error") or "")
    twofa_remote = result.get("twofa_remote")
    retry_after = int(result.get("retry_after") or 0)
    twofa_show = twofa_remote or twofa

    if codes:
        await _send_code_result(callback, item, codes, twofa_show)
        return

    # ── أولوية رسائل الخطأ حسب حالة المسارين ──
    # (1) أي مسار لم يصله كود بعد → إرشاد موحّد (الحالتان مدعومتان)
    if sess_err == "no_code_yet" or err == "no_code_yet":
        await callback.message.answer(
            f"⏳ كود الرقم <code>{item.phone_number}</code> لم يصل بعد.\n\n"
            "1) افتح تيليجرام وأدخل الرقم واضغط التالي حتى ترى شاشة إدخال الكود\n"
            "2) اضغط «📩 طلب الكود» هنا — البوت ينتظر الكود حتى 40 ثانية ويرسله فور وصوله.\n\n"
            "(أو اضغط الزر الآن ثم افتح تيليجرام — الحالتين مدعومتان.)"
            + (f"\n🔐 كلمة 2FA: <code>{twofa_show}</code>" if twofa_show else ""),
            reply_markup=tg_ready_owned_kb(item.id),
        )
        return
    # (2) الرابط أعطى كوده مسبقاً
    if err == "already_used":
        extra = f"\n🔐 كلمة 2FA: <code>{twofa_show}</code>" if twofa_show else ""
        await callback.message.answer(
            f"⚠️ هذا الرابط أعطى كوده مسبقاً ولا يمكن طلبه مرة ثانية.\n"
            f"📱 الرقم: <code>{item.phone_number}</code>\n"
            "تفقد شاشة إدخال الكود بتيليجرام — الكود السابق ما زال صالحاً لدقائق."
            f"{extra}\nتواصل مع الدعم إن لم يعمل.",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        return
    # (3) rate_limited من أي مسار
    if sess_err == "rate_limited" or err == "rate_limited":
        ra = sess_retry if sess_err == "rate_limited" else retry_after
        ra = ra or retry_after or sess_retry
        mins = max(1, -(-ra // 60)) if ra else 5
        await callback.message.answer(
            f"⏳ طلبات كثيرة على هذا الرابط. حاول بعد <b>{mins}</b> دقائق "
            "ثم اضغط «📩 طلب الكود» مجدداً.",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        return
    # (4) fetch_failed من أي مسار — صياغة موحّدة (الجلسة أو سيرفر الأكواد)
    if sess_err == "fetch_failed" or err == "fetch_failed":
        await callback.message.answer(
            "⚠️ تعذّر جلب الكود الآن — حاول بعد قليل.",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        return
    # (5) ملف الجلسة لم يعد صالحاً
    if sess_err == "not_authorized":
        extra = f"\n🔐 كلمة 2FA: <code>{twofa_show}</code>" if twofa_show else ""
        await callback.message.answer(
            f"⚠️ ملف جلسة الرقم <code>{item.phone_number}</code> لم يعد صالحاً للدخول.\n"
            f"استخدم ملف ZIP الذي استلمته يدوياً أو تواصل مع الدعم للحصول على بديل.{extra}",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        return
    # (6) خدمة الجلب المباشر غير مُهيّأة
    if sess_err == "not_configured":
        await callback.message.answer(
            "⚠️ خدمة جلب الكود المباشر غير مُفعّلة حالياً.\n"
            "تواصل مع الدعم — أو ادخل بالملف (ZIP) الذي استلمته مباشرة بلا كود.",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        return
    # (7) تعذّر تحميل ملف الجلسة — إعادة المحاولة مفيدة فوراً
    if sess_err == "download_failed":
        await callback.message.answer(
            f"⚠️ تعذّر تحميل ملف الجلسة للرقم <code>{item.phone_number}</code> — "
            "أعد المحاولة بعد قليل.",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        _CODE_COOLDOWN.pop((db_user.id, item.id), None)
        return
    # (8) لا جلسة محفوظة ولا رابط كود صالح
    if sess_err == "no_session" and err == "no_code_link":
        await callback.message.answer(
            f"⚠️ تعذّر جلب كود الرقم <code>{item.phone_number}</code> — "
            "لا توجد جلسة محفوظة ولا رابط كود صالح لهذا العنصر.\n"
            "تواصل مع الدعم لإعادة رفع الملف أو تغيير هذا الرقم.",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        _CODE_COOLDOWN.pop((db_user.id, item.id), None)
        return
    # (9) احتياط: أي حالة غير متوقعة → أسلوب no_code_yet
    await callback.message.answer(
        f"⏳ كود الرقم <code>{item.phone_number}</code> لم يصل بعد.\n\n"
        "1) افتح تيليجرام وأدخل الرقم واضغط التالي حتى ترى شاشة إدخال الكود\n"
        "2) اضغط «📩 طلب الكود» هنا — البوت ينتظر الكود حتى 40 ثانية ويرسله فور وصوله."
        + (f"\n🔐 كلمة 2FA: <code>{twofa_show}</code>" if twofa_show else ""),
        reply_markup=tg_ready_owned_kb(item.id),
    )


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
    if item_id in _CODE_INFLIGHT:
        await callback.answer("⏳ جاري جلب كود هذا الرقم الآن…")
        return

    _CODE_COOLDOWN[(db_user.id, item_id)] = now
    _CODE_INFLIGHT.add(item_id)
    await callback.answer("⏳ جاري جلب الكود...")

    try:
        await _deliver_login_code(callback, session, db_user, item)
    finally:
        _CODE_INFLIGHT.discard(item_id)
        # تحديث توقيت الكولداون بعد انتهاء الجلب (ما لم تُلغَ داخل التسليم بـ POP)
        ckey = (db_user.id, item_id)
        if ckey in _CODE_COOLDOWN:
            _CODE_COOLDOWN[ckey] = _time.monotonic()


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

    payload = reveal_payload(item.payload_encrypted, item.phone_number)
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
