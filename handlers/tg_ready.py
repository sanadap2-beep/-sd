"""واجهة الزبون لقسم الجلسات الجاهزة (داخل أرقام تلجرام).

العرض مفرز تلقائياً حسب الدولة (علم + سعر + مخزون حي).
كل عملية شراء ناجحة تنقص المخزون تلقائياً (العنصر يُعلَّم مباعاً).
"""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from database.models import TgReadyItem, TransactionType
from keyboards.main_menu import back_to_main_kb, insufficient_balance_kb
from keyboards.tg_ready import (
    tg_ready_confirm_kb,
    tg_ready_countries_kb,
    tg_ready_owned_kb,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.currency_service import CurrencyService
from services.notification_service import NotificationService
from services.tg_ready_service import TgReadyService, reveal_payload

router = Router(name="tg_ready")

_SECTION_INTRO = (
    "هذا القسم يحتوي على أرقام تلجرام نسبة وصول كودها <b>100/100</b>.\n"
    "فقط عليك شراء رقم وطلب كوده، وبعدها الضغط على «طلب الكود» "
    "وسيصلك الكود والرمز السري.\n\n"
    "⚠️ <b>تنويه:</b> هذا القسم ليس فيه إعادة الرصيد أبداً، "
    "لأن نسبة وصول الكود مؤكدة."
)
_LIST_TEXT = (
    "📦 <b>أرقام تلجرام — جلسات</b>\n\n"
    f"{_SECTION_INTRO}\n\n"
    "اختر الدولة:"
)
_EMPTY_TEXT = (
    "📦 <b>أرقام تلجرام — جلسات</b>\n\n"
    f"{_SECTION_INTRO}\n\n"
    "❌ لا يوجد مخزون متاح حالياً.\nيرجى المحاولة لاحقاً."
)


def _country_caption(entry: dict, price_display: str) -> str:
    return (
        f"{entry['flag']} <b>{entry['name']}</b>\n\n"
        f"💰 <b>السعر:</b> <b>{price_display}</b>\n"
        f"📦 <b>المتاح الآن:</b> <b>{entry['stock']}</b>\n\n"
        f"{_SECTION_INTRO}"
    )


async def present_tg_ready(message, session, db_user, country_key: str | None = None) -> None:
    """فتح قسم الجلسات الجاهزة من رابط القناة العامة."""
    from services.currency_service import CurrencyService

    countries = await TgReadyService.stock_overview(session)
    if country_key:
        entry = next((c for c in countries if c["key"] == country_key), None)
        if entry is not None:
            price_display = await CurrencyService.format_dual(entry["price"], db_user, session)
            await message.answer(
                _country_caption(entry, price_display),
                reply_markup=tg_ready_confirm_kb(country_key),
            )
            return
    if not countries:
        await message.answer(_EMPTY_TEXT, reply_markup=back_to_main_kb())
        return
    await message.answer(_LIST_TEXT, reply_markup=tg_ready_countries_kb(countries))


@router.callback_query(F.data == "tgready:list")
async def tg_ready_list(callback: CallbackQuery, session, db_user=None):
    countries = await TgReadyService.stock_overview(session)
    if not countries:
        await callback.message.edit_text(_EMPTY_TEXT, reply_markup=back_to_main_kb())
        await callback.answer()
        return
    await callback.message.edit_text(_LIST_TEXT, reply_markup=tg_ready_countries_kb(countries))
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
        _country_caption(entry, price_display),
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

    # لا يُرسل ملف جلسة أبداً. الزبون يرى الرقم وزر «طلب الكود» فقط.
    remaining = [c for c in await TgReadyService.stock_overview(session) if c["key"] == key]
    left = remaining[0]["stock"] if remaining else 0

    await callback.message.answer(
        f"✅ <b>تم الشراء بنجاح!</b>\n\n"
        f"{item.flag} <b>{item.country_name_ar}</b>\n"
        f"📱 الرقم: <code>{item.phone_number}</code>\n"
        f"💰 السعر: <b>{price}$</b>\n"
        f"📦 المتبقي من هذه الدولة: <b>{left}</b>\n\n"
        "افتح تيليجرام وأدخل الرقم واطلب الكود، ثم اضغط «📩 طلب الكود» "
        "وسيصلك الكود والرمز السري.\n\n"
        "⚠️ لا يُعاد الرصيد في هذا القسم.",
        reply_markup=tg_ready_owned_kb(item.id),
    )

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


async def _announce_ready_code(callback: CallbackQuery, session, db_user, item) -> None:
    """نشر إشعار القناة العامة بعد وصول الكود بنجاح. الفشل لا يقطع التسليم."""
    try:
        from services.notification_service import NotificationService

        overview = await TgReadyService.stock_overview(session)
        match = next((c for c in overview if c["key"] == item.country_key), None)
        left = int(match["stock"]) if match else 0
        notifier = NotificationService(callback.bot)
        await notifier.notify_successful_tg_ready(
            country_name=item.country_name_ar,
            country_flag=item.flag,
            price_usd=item.price_usd,
            remaining=left,
            phone_number=item.phone_number,
            buyer_telegram_id=db_user.telegram_id,
            item_id=item.id,
            country_key=item.country_key,
        )
    except Exception:
        return


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
            await _announce_ready_code(callback, session, db_user, item)
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
        await _announce_ready_code(callback, session, db_user, item)
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
            f"⚠️ تعذّر جلب كود الرقم <code>{item.phone_number}</code> الآن.\n"
            f"أعد الضغط على «📩 طلب الكود» بعد قليل، أو تواصل مع الدعم.{extra}",
            reply_markup=tg_ready_owned_kb(item.id),
        )
        return
    # (6) خدمة الجلب المباشر غير مُهيّأة
    if sess_err == "not_configured":
        await callback.message.answer(
            "⚠️ خدمة جلب الكود المباشر غير مُفعّلة حالياً.\n"
            "تواصل مع الدعم ثم أعد الضغط على «📩 طلب الكود».",
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
    """أزرار قديمة كانت ترسل ملف الجلسة — القسم لم يعد يسلّم أي ملف."""
    try:
        item_id = int(callback.data.rsplit(":", 1)[-1])
    except (ValueError, TypeError):
        await callback.answer("⚠️ طلب غير صالح.", show_alert=True)
        return
    item = await session.get(TgReadyItem, item_id)
    if item is None or item.buyer_user_id != db_user.id:
        await callback.answer("⚠️ هذا الرقم ليس لك.", show_alert=True)
        return
    await callback.answer("هذا القسم لا يرسل ملف جلسة.", show_alert=True)
    await callback.message.answer(
        f"📱 الرقم: <code>{item.phone_number}</code>\n\n"
        "لا يُرسل ملف جلسة. اضغط «📩 طلب الكود» بعد طلب الكود من تيليجرام.",
        reply_markup=tg_ready_owned_kb(item.id),
    )
