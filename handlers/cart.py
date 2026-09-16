"""Cart flow for collecting several products before checkout."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from keyboards.cart import cart_add_cancel_kb, cart_kb
from services.cart_service import CartError, CartService
from services.campaign_service import CampaignCodeError, CampaignService
from services.coupon_service import CouponError, CouponService
from services.currency_service import CurrencyService
from services.feature_service import FeatureService
from services.i18n_service import I18nService
from services.operation_lock_service import OperationBusyError, OperationLockService
from states.states import UserCartStates

router = Router(name="cart")


async def _render_cart(target, session, db_user: User):
    language = db_user.language_code
    items = await CartService.get_items(session, db_user.id)
    lines = [I18nService.t("cart_title", language) + "\n"]
    if not items:
        lines.append(I18nService.t("cart_empty", language))
    else:
        for item in items:
            total = CartService.item_total(item)
            display = await CurrencyService.format_dual(total, db_user, session)
            lines.append(
                f"• {escape(item.product.name_ar)} × {item.quantity} — {display}"
            )
        grand = CartService.total(items)
        grand_display = await CurrencyService.format_dual(grand, db_user, session)
        lines.append(
            "\n" + I18nService.t("cart_total_before_offers", language, total=grand_display)
        )
    markup = cart_kb(items)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text("\n".join(lines), reply_markup=markup)
    else:
        await target.answer("\n".join(lines), reply_markup=markup)


@router.callback_query(F.data == "menu:cart")
async def cart_menu(callback: CallbackQuery, session, db_user: User):
    await callback.answer()
    await _render_cart(callback, session, db_user)


@router.message(F.text == "🛒 السلة")
async def cart_message(message: Message, session, db_user: User):
    await _render_cart(message, session, db_user)


@router.callback_query(F.data.startswith("cart:add:"))
async def cart_add(
    callback: CallbackQuery,
    session,
    db_user: User,
    state: FSMContext,
):
    product_id = int(callback.data.split(":")[2])
    data = await state.get_data()
    try:
        await CartService.add(
            session,
            db_user.id,
            product_id,
            data.get("target", ""),
            int(data.get("quantity", 1)),
        )
    except (CartError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("🛒 تمت إضافة المنتج للسلة.", show_alert=True)
    await callback.message.answer(
        "🛒 تمت الإضافة. يمكنك متابعة التسوق أو فتح السلة.",
        reply_markup=cart_add_cancel_kb(),
    )


@router.callback_query(F.data.startswith("cart:remove:"))
async def cart_remove(callback: CallbackQuery, session, db_user: User):
    product_id = int(callback.data.split(":")[2])
    await CartService.remove(session, db_user.id, product_id)
    await callback.answer("🗑 تمت إزالة المنتج.")
    await _render_cart(callback, session, db_user)


@router.callback_query(F.data == "cart:coupon")
async def cart_coupon(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    items = await CartService.get_items(session, db_user.id)
    if not items:
        await callback.answer("🛒 السلة فارغة.", show_alert=True)
        return
    await state.set_state(UserCartStates.waiting_coupon)
    await callback.message.answer(
        "🎟 أرسل رمز الكوبون الآن، أو أرسل «إلغاء» لتخطي الخصم.\n"
        "سيُطبَّق تلقائياً على أفضل خصم لكل منتج في السلة."
    )


@router.message(UserCartStates.waiting_coupon)
async def cart_coupon_input(message: Message, session, db_user: User, state: FSMContext):
    code = (message.text or "").strip()
    if code.lower() in {"الغاء", "إلغاء", "cancel", "لا"}:
        await state.clear()
        await message.answer("👍 تخطينا الكوبون — أتمم السلة من زر «تنفيذ السلة».")
        return
    items = await CartService.get_items(session, db_user.id)
    if not items:
        await state.clear()
        await message.answer("🛒 السلة فارغة — أعد ملؤها أولاً.", reply_markup=cart_kb([]))
        return
    grand = CartService.total(items)
    try:
        coupon = await CouponService.validate_coupon(session, code, db_user.id, grand)
        discount = CouponService.calculate_discount(coupon, grand)
        label = coupon.code
    except CouponError as exc:
        if not await FeatureService.enabled("campaign_codes"):
            await message.answer(str(exc) + "\n\nأرسل الكود الصحيح أو «إلغاء» للتخطي.")
            return
        try:
            campaign = await CampaignService.validate(session, code, db_user.id, grand)
            discount = CampaignService.calculate_discount(campaign, grand)
            label = campaign.code
        except CampaignCodeError as cexc:
            await message.answer(str(cexc) + "\n\nأرسل الكود الصحيح أو «إلغاء» للتخطي.")
            return
    # نُجري من حالة FSM دون مسح البيانات: يقرأها زر التنفيذ عند الضغط.
    await state.update_data(cart_coupon_code=code.upper().strip())
    await state.set_state(None)
    await message.answer(
        f"🎟 الكود <b>{label}</b> صالح — خصم يصل حتى {discount:g}$ "
        "يُطبَّق تلقائياً عند التنفيذ.\n\nأتمم السلة الآن:",
        reply_markup=cart_kb(items),
    )


@router.callback_query(F.data == "cart:checkout")
async def cart_checkout(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    await callback.answer("⏳ جاري تنفيذ السلة...")
    data = await state.get_data()
    coupon_code = data.get("cart_coupon_code")
    try:
        async with OperationLockService.acquire(f"cart-checkout:{db_user.id}"):
            result = await CartService.checkout(session, db_user.id, coupon_code=coupon_code)
        await state.clear()
    except OperationBusyError as exc:
        await callback.message.answer(str(exc))
        return
    completed = result["completed"]
    failed = result["failed"]
    saved = result["total_saved_usd"]
    lines = [f"🧾 <b>نتيجة السلة</b>\n✅ تم تنفيذ: {len(completed)}"]
    if saved > 0:
        lines.append(f"🎟 وفّرت خصماً إجمالياً: <b>{saved:g}$</b>")
    if failed:
        lines.append(f"⚠️ بقيت {len(failed)} عناصر للمحاولة لاحقاً.")
        for item, error in failed[:5]:
            lines.append(f"• {escape(item.product.name_ar)}: {escape(error)}")
    deliveries = [
        checkout.delivery_value for _item, checkout in completed if checkout.delivery_value
    ]
    if deliveries:
        lines.append("\n🎁 <b>بيانات التسليم:</b>")
        lines.extend(f"<code>{escape(value)}</code>" for value in deliveries)
    await callback.message.edit_text("\n".join(lines), reply_markup=cart_kb([]))


@router.callback_query(F.data == "cart:clear")
async def cart_clear(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    await CartService.clear(session, db_user.id)
    await state.clear()
    await callback.answer("🧹 تم تفريغ السلة.")
    await _render_cart(callback, session, db_user)
