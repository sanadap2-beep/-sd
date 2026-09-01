"""
هاندلر موحد لشراء المنتجات (ألعاب + تطبيقات + SMM).
كل الأقسام تعمل بنفس المنطق:
1) المستخدم يختار القسم الرئيسي → القسم الفرعي → المنتج
2) يدخل البيانات المطلوبة (Player ID أو رابط أو كمية)
3) يتم التحقق من الرصيد
4) يُرسل الطلب للمزود تلقائياً
5) يُتابع الطلب من order_monitor
"""
import logging
from decimal import Decimal
from html import escape
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from database.models import User, UserFavorite, UnifiedOrder, UnifiedOrderStatus, TransactionType, Product, ProductStatus, ProductFulfillmentType
from services.dynamic_service import DynamicService
from services.player_id_service import PlayerIdError, PlayerIdService
from services.balance_service import BalanceService, InsufficientBalanceError
from services.notification_service import NotificationService
from services.gamification_service import GamificationService
from services.inventory_service import InventoryError, InventoryService
from services.loyalty_service import LoyaltyService
from services.settings_service import SettingsService
from services.coupon_service import CouponService, CouponError
from services.cashback_service import CashbackService
from services.product_service import ProductService
from services.promotion_service import PromotionService
from services.currency_service import CurrencyService
from services.i18n_service import I18nService
from services.tiered_pricing_service import TieredPricingService
from services.upsell_service import UpsellService
from services.watch_service import WatchService
from protocols.base import ProtocolError
from protocols.factory import ProtocolFactory
from states.states import GamesOrderStates, ProductSearchStates, SMMOrderStates
from keyboards.games import sub_categories_kb, products_kb, product_confirm_kb, product_confirm_with_coupon_kb, product_search_results_kb, favorites_kb
from keyboards.main_menu import insufficient_balance_kb, confirm_large_order_kb, back_to_main_kb
logger = logging.getLogger(__name__)
router = Router(name='games')


def _auto_lang(scope=None) -> str:
    user = (scope or {}).get("db_user")
    if user is None:
        callback = (scope or {}).get("callback")
        user = getattr(callback, "from_user", None)
    return getattr(user, "language_code", "ar") or "ar"

async def _dual_price(amount, db_user, session):
    """السعر بالدولار + ما يعادله بعملة عرض المستخدم."""
    if db_user is None:
        return f'${amount}'
    return await CurrencyService.format_dual(amount, db_user, session)

def _glang(db_user) -> str:
    return getattr(db_user, 'language_code', 'ar') or 'ar'

@router.callback_query(F.data == 'menu:search')
async def search_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    await callback.message.edit_text(I18nService.t('ux_games_93_1', _auto_lang(locals())), reply_markup=back_to_main_kb())
    await state.set_state(ProductSearchStates.waiting_query)

@router.message(ProductSearchStates.waiting_query)
async def search_query_received(message: Message, state: FSMContext, session):
    query_text = (message.text or '').strip()
    if len(query_text) < 2:
        await message.answer(I18nService.t('ux_games_107_2', _auto_lang(locals())))
        return
    products = await ProductService.search_products(session, query_text, limit=20, active_only=True)
    await state.clear()
    if not products:
        await message.answer(f"{I18nService.t('ux_games_119_3', _auto_lang(locals()))}{query_text}</b>.", reply_markup=back_to_main_kb())
        return
    await message.answer(f"{I18nService.t('ux_games_125_4', _auto_lang(locals()))}{query_text}{I18nService.t('ux_games_125_5', _auto_lang(locals()))}{len(products)}{I18nService.t('ux_games_125_6', _auto_lang(locals()))}", reply_markup=product_search_results_kb(products))

@router.callback_query(F.data == 'menu:favorites')
async def favorites_list(callback: CallbackQuery, session, db_user: User):
    result = await session.execute(select(UserFavorite).options(selectinload(UserFavorite.product)).where(UserFavorite.user_id == db_user.id).order_by(UserFavorite.created_at.desc()))
    products = [favorite.product for favorite in result.scalars().all() if favorite.product and favorite.product.status == ProductStatus.ACTIVE]
    await callback.answer()
    if not products:
        await callback.message.edit_text(I18nService.t('ux_games_150_7', _auto_lang(locals())), reply_markup=back_to_main_kb())
        return
    await callback.message.edit_text(I18nService.t('ux_games_155_8', _auto_lang(locals())), reply_markup=favorites_kb(products))

@router.callback_query(F.data.startswith('favorite:toggle:'))
async def favorite_toggle(callback: CallbackQuery, session, db_user: User):
    product_id = int(callback.data.split(':')[2])
    product = await session.get(Product, product_id)
    if not product or product.status != ProductStatus.ACTIVE:
        await callback.answer(I18nService.t('ux_games_169_9', _auto_lang(locals())), show_alert=True)
        return
    result = await session.execute(select(UserFavorite).where(UserFavorite.user_id == db_user.id, UserFavorite.product_id == product_id))
    favorite = result.scalar_one_or_none()
    if favorite:
        await session.delete(favorite)
        message = '🗑 تمت إزالة المنتج من المفضلة.'
    else:
        session.add(UserFavorite(user_id=db_user.id, product_id=product_id))
        message = '⭐ تمت إضافة المنتج إلى المفضلة.'
    await session.commit()
    await callback.answer(message, show_alert=True)

@router.callback_query(F.data.startswith('watch:toggle:'))
async def watch_toggle(callback: CallbackQuery, session, db_user: User):
    product_id = int(callback.data.split(':')[2])
    try:
        enabled = await WatchService.toggle(session, db_user.id, product_id)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(I18nService.t('ux_games_202_10', _auto_lang(locals())) if enabled else I18nService.t('ux_games_202_11', _auto_lang(locals())), show_alert=True)

@router.callback_query(F.data.startswith('favorite:remove:'))
async def favorite_remove(callback: CallbackQuery, session, db_user: User):
    product_id = int(callback.data.split(':')[2])
    result = await session.execute(select(UserFavorite).where(UserFavorite.user_id == db_user.id, UserFavorite.product_id == product_id))
    favorite = result.scalar_one_or_none()
    if favorite:
        await session.delete(favorite)
        await session.commit()
    await favorites_list(callback, session, db_user)

@router.callback_query(F.data.startswith('cat:'))
async def category_selected(callback: CallbackQuery, session):
    category_id = int(callback.data.split(':')[1])
    category = await DynamicService.get_category(session, category_id)
    if not category or not category.is_active:
        await callback.answer(I18nService.t('ux_games_236_12', _auto_lang(locals())), show_alert=True)
        return
    await callback.answer()
    sub_cats = await DynamicService.get_active_sub_categories(session, category_id)
    if not sub_cats:
        await callback.message.edit_text(f"{category.emoji} <b>{category.name_ar}{I18nService.t('ux_games_246_13', _auto_lang(locals()))}", reply_markup=back_to_main_kb())
        return
    await callback.message.edit_text(f"{category.emoji} <b>{category.name_ar}{I18nService.t('ux_games_252_14', _auto_lang(locals()))}", reply_markup=sub_categories_kb(category_id, sub_cats))

@router.callback_query(F.data.startswith('subcat:'))
async def sub_category_selected(callback: CallbackQuery, session):
    sub_cat_id = int(callback.data.split(':')[1])
    sub_cat = await DynamicService.get_sub_category(session, sub_cat_id)
    if not sub_cat or not sub_cat.is_active:
        await callback.answer(I18nService.t('ux_games_266_15', _auto_lang(locals())), show_alert=True)
        return
    await callback.answer()
    products = await DynamicService.get_active_products(session, sub_cat_id)
    if not products:
        await callback.message.edit_text(f"{sub_cat.emoji} <b>{sub_cat.name_ar}{I18nService.t('ux_games_276_16', _auto_lang(locals()))}", reply_markup=back_to_main_kb())
        return
    await callback.message.edit_text(f"{sub_cat.emoji} <b>{sub_cat.name_ar}{I18nService.t('ux_games_282_17', _auto_lang(locals()))}", reply_markup=products_kb(sub_cat_id, products, sub_cat.category_id))

@router.callback_query(F.data.startswith('prod:'))
async def product_selected(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    await state.clear()
    product_id = int(callback.data.split(':')[1])
    product = await DynamicService.get_product(session, product_id)
    if not product or product.status != ProductStatus.ACTIVE:
        await callback.answer(I18nService.t('ux_games_305_18', _auto_lang(locals())), show_alert=True)
        return
    await callback.answer()
    sub_cat = product.sub_category
    language = _glang(db_user)
    price_display = await _dual_price(product.price_usd, db_user, session)
    price_label = I18nService.t('price', language)
    confirm_q = I18nService.t('confirm_purchase_q', language)
    if product.requires_player_id:
        await callback.message.edit_text(f'🎮 <b>{product.name_ar}</b>\n💰 {price_label}: <b>{price_display}</b>\n\n' + I18nService.t('send_player_id', language))
        await state.update_data(product_id=product_id)
        await state.set_state(GamesOrderStates.waiting_player_id)
    elif product.requires_link:
        if product.requires_quantity:
            await callback.message.edit_text(f'📈 <b>{product.name_ar}</b>\n💰 {price_label}: <b>{price_display}</b> / {product.min_quantity}\n' + I18nService.t('quantity_limits', language, min_q=product.min_quantity, max_q=product.max_quantity) + '\n\n' + I18nService.t('send_link', language))
            await state.update_data(product_id=product_id)
            await state.set_state(SMMOrderStates.waiting_link)
        else:
            await callback.message.edit_text(f'📈 <b>{product.name_ar}</b>\n💰 {price_label}: <b>{price_display}</b>\n\n' + I18nService.t('send_link', language))
            await state.update_data(product_id=product_id, quantity=1)
            await state.set_state(SMMOrderStates.waiting_link)
    else:
        await callback.message.edit_text(f'📦 <b>{product.name_ar}</b>\n💰 {price_label}: <b>{price_display}</b>\n\n{confirm_q}', reply_markup=product_confirm_kb(product_id, sub_cat.id if sub_cat else 0))

@router.message(GamesOrderStates.waiting_player_id)
async def player_id_received(message: Message, state: FSMContext, session, db_user: User):
    data = await state.get_data()
    product_id = data['product_id']
    product = await DynamicService.get_product(session, product_id)
    if not product:
        await message.answer(I18nService.t('ux_games_386_19', _auto_lang(locals())))
        await state.clear()
        return
    sub_cat = product.sub_category
    context = f"{(sub_cat.name_ar if sub_cat else '')} {product.name_ar}"
    try:
        player_id = await PlayerIdService.validate(message.text or '', context=context)
    except PlayerIdError as exc:
        await message.answer(f"⚠️ {exc}{I18nService.t('ux_games_397_20', _auto_lang(locals()))}")
        return
    await state.update_data(target=player_id, quantity=1)
    await message.answer(f"🎮 <b>{product.name_ar}{I18nService.t('ux_games_406_21', _auto_lang(locals()))}{player_id}{I18nService.t('ux_games_406_22', _auto_lang(locals()))}{product.price_usd}{I18nService.t('ux_games_406_23', _auto_lang(locals()))}", reply_markup=product_confirm_kb(product_id, sub_cat.id if sub_cat else 0))

@router.message(SMMOrderStates.waiting_link)
async def smm_link_received(message: Message, state: FSMContext, session):
    link = message.text.strip()
    if not link.startswith('http'):
        await message.answer(I18nService.t('ux_games_425_24', _auto_lang(locals())))
        return
    data = await state.get_data()
    product_id = data['product_id']
    product = await DynamicService.get_product(session, product_id)
    if not product:
        await message.answer(I18nService.t('ux_games_433_25', _auto_lang(locals())))
        await state.clear()
        return
    await state.update_data(target=link)
    if product.requires_quantity:
        await message.answer(f"{I18nService.t('ux_games_441_26', _auto_lang(locals()))}{product.min_quantity}{I18nService.t('ux_games_441_27', _auto_lang(locals()))}{product.max_quantity}")
        await state.set_state(SMMOrderStates.waiting_quantity)
    else:
        await state.update_data(quantity=1)
        sub_cat = product.sub_category
        await message.answer(f"📈 <b>{product.name_ar}{I18nService.t('ux_games_450_28', _auto_lang(locals()))}{link}{I18nService.t('ux_games_450_29', _auto_lang(locals()))}{product.price_usd}{I18nService.t('ux_games_450_30', _auto_lang(locals()))}", reply_markup=product_confirm_kb(product_id, sub_cat.id if sub_cat else 0))

@router.message(SMMOrderStates.waiting_quantity)
async def smm_quantity_received(message: Message, state: FSMContext, session):
    try:
        quantity = int(message.text.strip())
    except ValueError:
        await message.answer(I18nService.t('ux_games_469_31', _auto_lang(locals())))
        return
    data = await state.get_data()
    product_id = data['product_id']
    product = await DynamicService.get_product(session, product_id)
    if not product:
        await message.answer(I18nService.t('ux_games_477_32', _auto_lang(locals())))
        await state.clear()
        return
    if quantity < product.min_quantity:
        await message.answer(f"{I18nService.t('ux_games_482_33', _auto_lang(locals()))}{product.min_quantity}")
        return
    if quantity > product.max_quantity:
        await message.answer(f"{I18nService.t('ux_games_485_34', _auto_lang(locals()))}{product.max_quantity}")
        return
    total_price = (product.price_usd * Decimal(str(quantity)) / Decimal(str(product.min_quantity))).quantize(Decimal('0.0001'))
    await state.update_data(quantity=quantity, total_price=str(total_price))
    sub_cat = product.sub_category
    link = data.get('target', '—')
    await message.answer(f"📈 <b>{product.name_ar}{I18nService.t('ux_games_501_35', _auto_lang(locals()))}{link}{I18nService.t('ux_games_501_36', _auto_lang(locals()))}{quantity}{I18nService.t('ux_games_501_37', _auto_lang(locals()))}{total_price}{I18nService.t('ux_games_501_38', _auto_lang(locals()))}", reply_markup=product_confirm_kb(product_id, sub_cat.id if sub_cat else 0))

@router.callback_query(F.data.startswith('prod_coupon:'))
async def product_coupon_start(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(':')[1])
    await state.update_data(coupon_product_id=product_id)
    await callback.message.answer(I18nService.t('ux_games_520_39', _auto_lang(locals())))
    await state.set_state(GamesOrderStates.waiting_coupon)
    await callback.answer()

@router.message(GamesOrderStates.waiting_coupon)
async def product_coupon_received(message: Message, state: FSMContext, session, db_user: User):
    data = await state.get_data()
    product_id = data.get('coupon_product_id')
    product = await DynamicService.get_product(session, product_id)
    if not product:
        await message.answer(I18nService.t('ux_games_537_40', _auto_lang(locals())))
        await state.clear()
        return
    code = message.text.strip()
    try:
        coupon = await CouponService.validate_coupon(session, code, db_user.id, product.price_usd)
    except CouponError as e:
        await message.answer(str(e))
        await state.clear()
        return
    discount = CouponService.calculate_discount(coupon, product.price_usd)
    sub_cat = product.sub_category
    await message.answer(f"{I18nService.t('ux_games_553_41', _auto_lang(locals()))}{coupon.code}{I18nService.t('ux_games_553_42', _auto_lang(locals()))}{product.price_usd}{I18nService.t('ux_games_553_43', _auto_lang(locals()))}{discount}{I18nService.t('ux_games_553_44', _auto_lang(locals()))}{product.price_usd - discount}$</b>", reply_markup=product_confirm_with_coupon_kb(product_id, sub_cat.id if sub_cat else 0, coupon.code, str(discount)))

@router.callback_query(F.data.startswith('prod_confirm:'))
async def product_confirm(callback: CallbackQuery, session, db_user: User, bot, state: FSMContext):
    product_id = int(callback.data.split(':')[1])
    await _execute_purchase(callback, session, db_user, bot, state, product_id, coupon_code=None)

@router.callback_query(F.data.startswith('prod_confirm_coupon:'))
async def product_confirm_with_coupon(callback: CallbackQuery, session, db_user: User, bot, state: FSMContext):
    parts = callback.data.split(':')
    product_id = int(parts[1])
    coupon_code = parts[2]
    await _execute_purchase(callback, session, db_user, bot, state, product_id, coupon_code=coupon_code)

async def _execute_purchase(callback: CallbackQuery, session, db_user: User, bot, state: FSMContext, product_id: int, coupon_code: str | None):
    product = await DynamicService.get_product(session, product_id)
    if not product or product.status != ProductStatus.ACTIVE:
        await callback.answer(I18nService.t('ux_games_623_45', _auto_lang(locals())), show_alert=True)
        return
    fulfillment = getattr(product.fulfillment_type, 'value', product.fulfillment_type)
    if fulfillment == ProductFulfillmentType.INVENTORY.value:
        if await InventoryService.available_count(session, product.id) <= 0:
            await callback.answer(I18nService.t('ux_games_634_46', _auto_lang(locals())), show_alert=True)
            await state.clear()
            return
    elif fulfillment == ProductFulfillmentType.MANUAL.value:
        pass
    elif fulfillment != ProductFulfillmentType.API.value or not product.api_provider_id or (not product.provider_service_id) or (not product.api_provider) or (not product.api_provider.is_active):
        await callback.answer(I18nService.t('ux_games_650_47', _auto_lang(locals())), show_alert=True)
        await state.clear()
        return
    await callback.answer(I18nService.t('ux_games_656_48', _auto_lang(locals())))
    fsm_data = await state.get_data()
    target = fsm_data.get('target', '')
    quantity = fsm_data.get('quantity', 1)
    if product.requires_quantity and quantity > 1:
        total_price = (product.price_usd * Decimal(str(quantity)) / Decimal(str(product.min_quantity))).quantize(Decimal('0.0001'))
    else:
        total_price = product.price_usd
    promotion, promotion_discount = await PromotionService.get_best_promotion(session, product.id, total_price)
    if fulfillment == ProductFulfillmentType.INVENTORY.value and coupon_code:
        await callback.answer(I18nService.t('ux_games_678_49', _auto_lang(locals())), show_alert=True)
        await state.clear()
        return
    discount = Decimal('0')
    coupon = None
    if coupon_code:
        try:
            coupon = await CouponService.validate_coupon(session, coupon_code, db_user.id, total_price)
            discount = CouponService.calculate_discount(coupon, total_price)
        except CouponError:
            discount = Decimal('0')
            coupon = None
    tier_discount, _tier_label = await TieredPricingService.discount_for(session, db_user.id, product, total_price, quantity)
    if promotion_discount >= max(discount, tier_discount) and promotion_discount > 0:
        discount = promotion_discount
        coupon = None
    elif tier_discount > discount:
        discount = tier_discount
        coupon = None
        promotion = None
    elif discount > 0:
        promotion = None
    final_price = total_price - discount
    large_confirm = await SettingsService.get_decimal('large_order_confirm_usd', Decimal('20'))
    if final_price >= large_confirm:
        language = _glang(db_user)
        amount_display = await _dual_price(final_price, db_user, session)
        await callback.message.answer(I18nService.t('large_order_confirm', language, product=product.name_ar, amount=amount_display), reply_markup=confirm_large_order_kb(f"prod_final:{product_id}:{coupon_code or 'none'}", language))
        return
    await _finalize_purchase(callback, session, db_user, bot, state, product, target, quantity, final_price, discount, coupon, promotion)

@router.callback_query(F.data.startswith('prod_final:'))
async def product_final_confirm(callback: CallbackQuery, session, db_user: User, bot, state: FSMContext):
    parts = callback.data.split(':')
    product_id = int(parts[1])
    coupon_code = parts[2] if parts[2] != 'none' else None
    fsm_data = await state.get_data()
    target = fsm_data.get('target', '')
    quantity = int(fsm_data.get('quantity', 1))
    product = await DynamicService.get_product(session, product_id)
    if not product or product.status != ProductStatus.ACTIVE:
        await callback.answer(I18nService.t('ux_games_763_50', _auto_lang(locals())), show_alert=True)
        await state.clear()
        return
    await callback.answer(I18nService.t('ux_games_767_51', _auto_lang(locals())))
    if product.requires_quantity and quantity > 1:
        total_price = (product.price_usd * Decimal(str(quantity)) / Decimal(str(product.min_quantity))).quantize(Decimal('0.0001'))
    else:
        total_price = product.price_usd
    fulfillment = getattr(product.fulfillment_type, 'value', product.fulfillment_type)
    promotion, promotion_discount = await PromotionService.get_best_promotion(session, product.id, total_price)
    if fulfillment == ProductFulfillmentType.INVENTORY.value and coupon_code:
        await callback.answer(I18nService.t('ux_games_786_52', _auto_lang(locals())), show_alert=True)
        await state.clear()
        return
    discount = Decimal('0')
    coupon = None
    if coupon_code:
        try:
            coupon = await CouponService.validate_coupon(session, coupon_code, db_user.id, total_price)
            discount = CouponService.calculate_discount(coupon, total_price)
        except CouponError:
            pass
    tier_discount, _tier_label = await TieredPricingService.discount_for(session, db_user.id, product, total_price, quantity)
    if promotion_discount >= max(discount, tier_discount) and promotion_discount > 0:
        discount = promotion_discount
        coupon = None
    elif tier_discount > discount:
        discount = tier_discount
        coupon = None
        promotion = None
    elif discount > 0:
        promotion = None
    final_price = total_price - discount
    await _finalize_purchase(callback, session, db_user, bot, state, product, target, quantity, final_price, discount, coupon, promotion)

async def _finalize_purchase(callback, session, db_user, bot, state, product, target, quantity, final_price, discount, coupon, promotion):
    notifier = NotificationService(bot)
    fulfillment = getattr(product.fulfillment_type, 'value', product.fulfillment_type)
    if fulfillment == ProductFulfillmentType.API.value and (not product.api_provider_id or not product.provider_service_id or (not product.api_provider) or (not product.api_provider.is_active)):
        await callback.message.answer(I18nService.t('ux_games_859_53', _auto_lang(locals())))
        await state.clear()
        return
    if fulfillment == ProductFulfillmentType.INVENTORY.value:
        try:
            order, delivered_value, metadata = await InventoryService.purchase(session, user_id=db_user.id, product_id=product.id, price_usd=final_price, quantity=quantity, promotion_id=promotion.id if promotion else None)
        except (InventoryError, InsufficientBalanceError) as exc:
            await callback.message.answer(f'⚠️ {exc}')
            await state.clear()
            return
        if promotion:
            await PromotionService.mark_used(session, promotion.id)
        await DynamicService.increment_product_sold(session, product.id, quantity)
        cashback = await CashbackService.apply_cashback(session, db_user.id, order.id, 'unified_orders', final_price)
        await LoyaltyService.award_purchase_points(session, db_user.id, 'unified_orders', order.id, final_price)
        await GamificationService.progress_event(session, db_user.id, 'purchase')
        delivery_note = ''
        if metadata and metadata.get('note'):
            delivery_note = f"\\n📝 ملاحظة: {escape(str(metadata['note']))}"
        await callback.message.answer(f"{I18nService.t('ux_games_899_54', _auto_lang(locals()))}{escape(product.name_ar)}{I18nService.t('ux_games_899_55', _auto_lang(locals()))}{order.id}{I18nService.t('ux_games_899_56', _auto_lang(locals()))}{final_price}{I18nService.t('ux_games_899_57', _auto_lang(locals()))}{escape(delivered_value)}</code>{delivery_note}{I18nService.t('ux_games_899_58', _auto_lang(locals()))}")
        await notifier.notify_admin(f'📦 <b>تم تسليم منتج من المخزون</b>\\n\\n🆔 الطلب: #{order.id}\\n👤 المستخدم: {db_user.telegram_id}\\n📦 المنتج: {escape(product.name_ar)}\\n💰 المبلغ: {final_price}$')
        upsells = await UpsellService.recommend(session, product.id)
        if upsells:
            await callback.message.answer(I18nService.t('ux_games_917_59', _auto_lang(locals())), reply_markup=product_search_results_kb(upsells))
        await state.clear()
        return
    if db_user.balance < final_price:
        language = _glang(db_user)
        await notifier.notify_insufficient_balance(user_telegram_id=db_user.telegram_id, required_usd=str(final_price), current_balance_usd=f'{db_user.balance:.2f}', reply_markup=insufficient_balance_kb(language))
        await state.clear()
        return
    try:
        await BalanceService.deduct_balance(session, db_user.id, final_price, TransactionType.PURCHASE, description=f'شراء {product.name_ar}', is_purchase=True)
    except InsufficientBalanceError:
        await callback.message.answer(I18nService.t('insufficient_balance', _glang(db_user)))
        await state.clear()
        return
    if coupon and discount > 0:
        try:
            await CouponService.apply_coupon(session, coupon, db_user.id, discount)
        except CouponError as exc:
            logger.warning('تعذر تطبيق الكوبون أثناء الطلب: %s', exc)
            await BalanceService.add_balance(session, db_user.id, final_price, TransactionType.REFUND, description='استرجاع - تعذر تطبيق الكوبون')
            await callback.message.answer(I18nService.t('ux_games_968_60', _auto_lang(locals())))
            await state.clear()
            return
    external_order_id = None
    order_status = UnifiedOrderStatus.PENDING
    status_message = 'بانتظار تنفيذ الإدارة' if fulfillment == ProductFulfillmentType.MANUAL.value else 'بانتظار التنفيذ'
    if fulfillment == ProductFulfillmentType.MANUAL.value:
        pass
    elif product.api_provider_id and product.provider_service_id:
        provider = product.api_provider
        if provider and provider.is_active:
            try:
                protocol = ProtocolFactory.create_from_provider(provider)
                result = await protocol.place_order(service_id=product.provider_service_id, target=target, quantity=quantity)
                external_order_id = result.external_order_id
                order_status = UnifiedOrderStatus.PROCESSING
                status_message = 'تم إرسال الطلب للمزود'
            except ProtocolError as e:
                logger.error(f'فشل إرسال الطلب للمزود: {e}')
                await BalanceService.add_balance(session, db_user.id, final_price, TransactionType.REFUND, description='استرجاع - فشل الإرسال للمزود')
                await callback.message.answer(I18nService.t('ux_games_1007_61', _auto_lang(locals())))
                await state.clear()
                return
    order = UnifiedOrder(user_id=db_user.id, product_id=product.id, api_provider_id=product.api_provider_id, promotion_id=promotion.id if promotion else None, external_order_id=external_order_id, target=target, quantity=quantity, price_usd=final_price, cost_price_usd=product.cost_price_usd, status=order_status, status_message=status_message)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    if promotion:
        await PromotionService.mark_used(session, promotion.id)
    await DynamicService.increment_product_sold(session, product.id, quantity)
    cashback = await CashbackService.apply_cashback(session, db_user.id, order.id, 'unified_orders', final_price)
    result_text = f'✅ <b>تم إرسال طلبك بنجاح!</b>\n\n🆔 رقم الطلب: #{order.id}\n📦 المنتج: {product.name_ar}\n💰 المبلغ: {final_price}$\n'
    if discount > 0:
        label = 'العرض' if promotion else 'الكوبون'
        result_text += f'🎁 {label}: -{discount}$\n'
    if cashback > 0:
        result_text += f'🎁 كاشباك: {cashback}$\n'
    if target:
        result_text += f'🎯 الهدف: <code>{target}</code>\n'
    if quantity > 1:
        result_text += f'📊 الكمية: {quantity}\n'
    result_text += f'\n📊 الحالة: {status_message}\nستصلك إشعارات بتحديث حالة طلبك.'
    await callback.message.answer(result_text)
    await notifier.notify_admin(f"🛒 <b>طلب شراء جديد</b>\n\n👤 المستخدم: {db_user.telegram_id} (@{db_user.username or '-'})\n📦 المنتج: {product.name_ar}\n💰 المبلغ: {final_price}$\n🎯 الهدف: {target or '—'}\n📊 الكمية: {quantity}\n🆔 طلب #{order.id}")
    await notifier.notify_successful_unified_order(username=db_user.username, full_name=db_user.full_name, product_name=product.name_ar, price_usd=str(final_price))
    await state.clear()
