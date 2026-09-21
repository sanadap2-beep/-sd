"""إدارة الجلسات الجاهزة من لوحة الأدمن: رفع ملف + نسبة ربح + أسعار الدول.

التدفق: زر «📦 جلسات تلجرام الجاهزة» -> يعرض المخزون المفرز تلقائياً
-> «📤 رفع ملف» -> يسأل عن نسبة الربح % -> يسأل عن تكلفة الحساب $
-> يطلب الملف (txt/csv/zip) -> يفرز الدول (اسم+علم+سعر) ويجهزها للبيع.
"""

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from filters.admin_filter import IsAdmin
from keyboards.admin import admin_back_kb
from keyboards.tg_ready import (
    admin_tg_ready_country_kb,
    admin_tg_ready_kb,
    admin_tg_ready_wipe_kb,
)
from services.tg_ready_service import TgReadyService, extract_zip_files, parse_uploaded_file
from states.states import AdminTgReadyStates

router = Router(name="admin_tg_ready")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _home_text(session) -> tuple[str, list[dict], int, str]:
    margin = await TgReadyService.get_margin(session)
    countries = await TgReadyService.stock_overview(session)
    total = await TgReadyService.total_available(session)
    if not countries:
        text = (
            "📦 <b>جلسات تلجرام الجاهزة</b>\n\n"
            "لا يوجد مخزون بعد.\n\n"
            "اضغط «📤 رفع ملف أرقام جديد» وأرسل ملف <code>.txt</code> أو "
            "<code>.csv</code> أو <code>.zip</code> — كل سطر فيه رقم "
            "(ومعه اختيارياً بيانات الجلسة بعد |).\n\n"
            "البوت سيتعرف على الدولة تلقائياً ويضع اسمها وعلمها وسعرها "
            f"(التكلفة + ربح {margin}%)."
        )
    else:
        lines = [
            "📦 <b>جلسات تلجرام الجاهزة — المخزون المفرز تلقائياً</b>\n",
            f"📊 الإجمالي المتاح: <b>{total}</b> | 💰 الربح الافتراضي: <b>{margin}%</b>\n",
        ]
        for c in countries:
            lines.append(
                f"{c['flag']} {c['name']}: <b>{c['stock']}</b> بسعر <b>{c['price']}$</b>"
            )
        lines.append("\nاضغط أي دولة لتعديل سعرها أو إخفائها أو حذف مخزونها.")
        text = "\n".join(lines)
    return text, countries, total, str(margin)


@router.callback_query(F.data == "admin:tg_ready")
async def tg_ready_home(callback: CallbackQuery, session):
    text, countries, total, margin = await _home_text(session)
    await callback.message.edit_text(
        text, reply_markup=admin_tg_ready_kb(countries, total, margin)
    )
    await callback.answer()


@router.callback_query(F.data == "admin:tg_ready_upload")
async def tg_ready_upload_start(callback: CallbackQuery, state: FSMContext, session):
    margin = await TgReadyService.get_margin(session)
    await state.update_data(tg_ready_margin=str(margin))
    await state.set_state(AdminTgReadyStates.waiting_margin)
    await callback.message.edit_text(
        "📤 <b>رفع ملف أرقام جديد</b>\n\n"
        f"نسبة الربح الحالية: <b>{margin}%</b>\n\n"
        "أرسل نسبة الربح % لهذه الدفعة (مثال: <code>50</code>)،\n"
        "أو أرسل <code>-</code> لاستخدام النسبة الحالية.",
        reply_markup=admin_back_kb(),
    )
    await callback.answer()


@router.message(AdminTgReadyStates.waiting_margin)
async def tg_ready_margin_received(message: Message, state: FSMContext, session):
    raw = (message.text or "").strip()
    if raw in ("-", "—"):
        data = await state.get_data()
        margin = Decimal(str(data.get("tg_ready_margin", "50")))
    else:
        try:
            margin = Decimal(raw.replace("%", "").replace("٫", "."))
        except (InvalidOperation, ValueError, AttributeError):
            await message.answer("⚠️ أرسل رقماً فقط (مثال: 50) أو - للمتابعة.")
            return
        if margin < 0 or margin > 500:
            await message.answer("⚠️ النسبة بين 0 و 500.")
            return
        await TgReadyService.set_margin(session, margin)
    await state.update_data(tg_ready_margin=str(margin))
    await state.set_state(AdminTgReadyStates.waiting_cost)
    await message.answer(
        f"✅ نسبة الربح: <b>{margin}%</b>\n\n"
        "💵 الآن أرسل <b>تكلفة الحساب الواحد</b> بالدولار (مثال: <code>0.40</code>).\n"
        "سعر البيع سيُحسب تلقائياً = التكلفة + الربح."
    )


@router.message(AdminTgReadyStates.waiting_cost)
async def tg_ready_cost_received(message: Message, state: FSMContext):
    raw = (message.text or "").strip().replace("٫", ".")
    try:
        cost = Decimal(raw.replace("$", ""))
    except (InvalidOperation, ValueError, AttributeError):
        await message.answer("⚠️ أرسل التكلفة رقماً فقط (مثال: 0.40).")
        return
    if cost <= 0 or cost > 1000:
        await message.answer("⚠️ التكلفة يجب أن تكون أكبر من صفر.")
        return
    await state.update_data(tg_ready_cost=str(cost))
    await state.set_state(AdminTgReadyStates.waiting_file)
    data = await state.get_data()
    from services.tg_ready_service import calc_sell_price

    sell = calc_sell_price(cost, Decimal(str(data.get("tg_ready_margin", "50"))))
    await message.answer(
        f"✅ التكلفة: <b>{cost}$</b> → سعر البيع: <b>{sell}$</b>\n\n"
        "📎 الآن أرسل <b>الملف</b> كمستند (txt / csv / zip):\n"
        "• txt: كل سطر رقم (ومعه | بيانات الجلسة)\n"
        "• csv: عمود phone\n"
        "• zip: ملفات جلسات بأسماء فيها الأرقام"
    )


@router.message(AdminTgReadyStates.waiting_file)
async def tg_ready_file_received(message: Message, state: FSMContext, session, bot):
    doc = message.document
    # دعم لصق الأرقام كنص مباشرة بدل ملف
    raw: bytes | None = None
    fname = ""
    if doc is not None:
        fname = doc.file_name or "upload"
        if doc.file_size and doc.file_size > 30 * 1024 * 1024:
            await message.answer("⚠️ الملف كبير جداً (الحد 30MB).")
            return
        try:
            tg_file = await bot.get_file(doc.file_id)
            buf = await bot.download_file(tg_file.file_path)
            raw = buf.read() if hasattr(buf, "read") else bytes(buf)
        except Exception:
            await message.answer("⚠️ تعذر تحميل الملف، حاول مجدداً.")
            return
    elif message.text and any(ch.isdigit() for ch in message.text):
        fname = "pasted.txt"
        raw = message.text.encode("utf-8")
    else:
        await message.answer("⚠️ أرسل الملف كمستند، أو الصق الأرقام كنص.")
        return

    entries = parse_uploaded_file(fname, raw)
    if not entries:
        await message.answer(
            "⚠️ لم أجد أي رقم بالملف. تأكد أن كل سطر يحوي رقماً من 7-15 خانة."
        )
        return
    data = await state.get_data()
    cost = Decimal(str(data.get("tg_ready_cost", "0")))
    margin = Decimal(str(data.get("tg_ready_margin", "50")))
    await message.answer(f"⏳ تم العثور على <b>{len(entries)}</b> رقم، جاري الفرز وحفظ الملفات...")
    files_map = None
    if (fname or "").lower().endswith(".zip"):
        files_map = extract_zip_files(raw)
    try:
        result = await TgReadyService.import_entries(
            session, entries, cost, margin, file_name=fname, created_by=None,
            files_map=files_map,
        )
    except Exception as exc:
        await message.answer(f"❌ فشل الاستيراد: {exc}")
        return
    await state.clear()
    lines = [
        "✅ <b>تم فرز الملف تلقائياً وهو جاهز للبيع!</b>\n",
        f"📥 المضاف: <b>{result['added']}</b> | ⏭ المكرر: <b>{result['dupes']}</b>",
        f"💰 سعر البيع: <b>{result['sell']}$</b> (تكلفة {cost}$ + ربح {margin}%)\n",
        "<b>الدول المفرزة:</b>",
    ]
    for key, info in result["countries"].items():
        lines.append(f"{info['flag']} {info['name']}: <b>{info['count']}</b>")
    if result.get("with_files"):
        lines.append(
            f"\n📁 حسابات بملفات جلسة فعلية: <b>{result['with_files']}</b> — "
            "الزبون بيستلم ملف ZIP وبيدخل مباشرة بلا كود."
        )
    else:
        lines.append(
            "\n⚠️ الملف نصي بلا ملفات جلسة مرفقة — الزبون بيستلم الرقم + رابط "
            "الكود (إن وُجد بالسطر) + 2FA وبيدخل بالرقم والكود."
        )
    lines.append("\nالزبون الآن يرى هذه الدول بقسم أرقام تلجرام ← 📦 حسابات جاهزة.")
    await message.answer("\n".join(lines))
    text, countries, total, margin_s = await _home_text(session)
    await message.answer(text, reply_markup=admin_tg_ready_kb(countries, total, margin_s))


@router.callback_query(F.data == "admin:tg_ready_margin")
async def tg_ready_margin_edit_start(callback: CallbackQuery, state: FSMContext, session):
    margin = await TgReadyService.get_margin(session)
    await state.set_state(AdminTgReadyStates.waiting_price)
    await state.update_data(tg_ready_margin_edit=True)
    await callback.message.edit_text(
        f"💰 نسبة الربح الافتراضية الحالية: <b>{margin}%</b>\n\n"
        "أرسل النسبة الجديدة (مثال: <code>60</code>).\n"
        "تُستخدم في الدفعات الجديدة فقط — أسعار المخزون الحالي لا تتغير.",
        reply_markup=admin_back_kb(),
    )
    await callback.answer()


@router.message(AdminTgReadyStates.waiting_price)
async def tg_ready_margin_edit_received(message: Message, state: FSMContext, session):
    data = await state.get_data()
    if not data.get("tg_ready_margin_edit"):
        return
    raw = (message.text or "").strip().replace("%", "")
    try:
        margin = Decimal(raw)
    except (InvalidOperation, ValueError, AttributeError):
        await message.answer("⚠️ أرسل رقماً فقط.")
        return
    if margin < 0 or margin > 500:
        await message.answer("⚠️ النسبة بين 0 و 500.")
        return
    await TgReadyService.set_margin(session, margin)
    await state.clear()
    await message.answer(f"✅ تم تحديث نسبة الربح الافتراضية إلى <b>{margin}%</b>.")


@router.callback_query(F.data.startswith("admin:tg_ready_country:"))
async def tg_ready_country_view(callback: CallbackQuery, session):
    from database.models import TgReadyCountry

    key = callback.data.rsplit(":", 1)[-1]
    country = await session.get(TgReadyCountry, key)
    if country is None:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    from sqlalchemy import func, select

    from database.models import TgReadyItem, TgReadyItemStatus

    stock = (
        await session.execute(
            select(func.count(TgReadyItem.id)).where(
                TgReadyItem.country_key == key,
                TgReadyItem.status == TgReadyItemStatus.AVAILABLE,
            )
        )
    ).scalar_one()
    await callback.message.edit_text(
        f"{country.flag} <b>{country.name_ar}</b>\n\n"
        f"🔑 المفتاح: <code>{country.country_key}</code>\n"
        f"💰 السعر: <b>{country.price_usd}$</b> (تكلفة {country.last_cost_usd}$ + ربح {country.margin_percent}%)\n"
        f"📦 المخزون المتاح: <b>{stock}</b>\n"
        f"👁 الظهور: {'🟢 ظاهر' if country.is_active else '⚪ مخفي'}",
        reply_markup=admin_tg_ready_country_kb(key, country.is_active),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:tg_ready_price:"))
async def tg_ready_price_start(callback: CallbackQuery, state: FSMContext):
    key = callback.data.rsplit(":", 1)[-1]
    await state.update_data(tg_ready_price_country=key)
    await state.set_state(AdminTgReadyStates.waiting_price_country)
    await callback.message.edit_text(
        "💰 أرسل السعر الجديد بالدولار لهذه الدولة (مثال: <code>0.80</code>):",
        reply_markup=admin_back_kb(),
    )
    await callback.answer()


@router.message(AdminTgReadyStates.waiting_price_country)
async def tg_ready_price_received(message: Message, state: FSMContext, session):
    from decimal import Decimal as _D

    from sqlalchemy import update

    from database.models import TgReadyCountry, TgReadyItem, TgReadyItemStatus

    data = await state.get_data()
    key = data.get("tg_ready_price_country")
    if not key:
        await state.clear()
        return
    try:
        price = _D((message.text or "").strip().replace("$", ""))
    except (InvalidOperation, ValueError, AttributeError):
        await message.answer("⚠️ أرسل رقماً فقط.")
        return
    if price <= 0:
        await message.answer("⚠️ السعر يجب أن يكون أكبر من صفر.")
        return
    country = await session.get(TgReadyCountry, key)
    if country is None:
        await message.answer("⚠️ الدولة غير موجودة.")
        await state.clear()
        return
    country.price_usd = price
    # وحّد سعر كل عناصر الدولة المتاحة حتى لا يُباع عنصران بسعرين مختلفين
    await session.execute(
        update(TgReadyItem)
        .where(
            TgReadyItem.country_key == key,
            TgReadyItem.status == TgReadyItemStatus.AVAILABLE,
        )
        .values(price_usd=price)
    )
    await session.commit()
    await state.clear()
    await message.answer(f"✅ تم تحديث سعر {country.flag} {country.name_ar} إلى <b>{price}$</b>.")


@router.callback_query(F.data.startswith("admin:tg_ready_toggle:"))
async def tg_ready_toggle(callback: CallbackQuery, session):
    from database.models import TgReadyCountry

    key = callback.data.rsplit(":", 1)[-1]
    country = await session.get(TgReadyCountry, key)
    if country is None:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
        return
    country.is_active = not country.is_active
    await session.commit()
    await callback.answer("✅ تم التحديث.")
    await tg_ready_country_view(callback, session)


@router.callback_query(F.data.startswith("admin:tg_ready_del:"))
async def tg_ready_delete_country(callback: CallbackQuery, session):
    from sqlalchemy import delete

    from database.models import TgReadyCountry, TgReadyItem, TgReadyItemStatus

    key = callback.data.rsplit(":", 1)[-1]
    await session.execute(
        delete(TgReadyItem).where(
            TgReadyItem.country_key == key,
            TgReadyItem.status == TgReadyItemStatus.AVAILABLE,
        )
    )
    country = await session.get(TgReadyCountry, key)
    if country is not None:
        await session.delete(country)
    await session.commit()
    await callback.answer("🗑 تم حذف مخزون الدولة.")
    await tg_ready_home(callback, session)


@router.callback_query(F.data == "admin:tg_ready_wipe_ask")
async def tg_ready_wipe_ask(callback: CallbackQuery):
    await callback.message.edit_text(
        "🗑 <b>تصفير كل مخزون الجلسات الجاهزة؟</b>\n\nسيُحذف كل المتاح فقط (المباع يبقى مسجلاً).",
        reply_markup=admin_tg_ready_wipe_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:tg_ready_wipe_yes")
async def tg_ready_wipe_yes(callback: CallbackQuery, session):
    from sqlalchemy import delete

    from database.models import TgReadyCountry, TgReadyItem, TgReadyItemStatus

    await session.execute(
        delete(TgReadyItem).where(TgReadyItem.status == TgReadyItemStatus.AVAILABLE)
    )
    await session.execute(delete(TgReadyCountry))
    await session.commit()
    await callback.answer("🗑 تم التصفير.")
    await tg_ready_home(callback, session)
