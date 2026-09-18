"""
هاندلر موحد لشراء المنتجات (ألعاب + تطبيقات + SMM).
كل الأقسام تعمل بنفس المنطق:
1) المستخدم يختار القسم الرئيسي → القسم الفرعي → المنتج
2) يدخل البيانات المطلوبة (Player ID أو رابط أو كمية)
3) يتم التحقق من الرصيد
4) يُرسل الطلب للمزود تلقائياً
5) يُتابع الطلب من order_monitor
"""
import json
import logging
from datetime import datetime
from decimal import Decimal
from aiogram import Router, F
from aiogram.filters import Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from database.models import User, UserFavorite, UnifiedOrder, UnifiedOrderStatus, TransactionType, Product, ProductStatus, ProductFulfillmentType, StoreServer, ApiProvider
from services.store_server_service import StoreServerService
from services.agent_service import AgentService
from services.html_guard import esc
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
from services.campaign_service import CampaignCodeError, CampaignService
from services.feature_service import FeatureService
from services.product_service import ProductService
from services.promotion_service import PromotionService
from services.currency_service import CurrencyService
from services.i18n_service import I18nService
from services.tiered_pricing_service import TieredPricingService
from services.upsell_service import UpsellService
from services.watch_service import WatchService
from protocols.base import ProtocolError, ProtocolInsufficientFundsError
from protocols.factory import ProtocolFactory
from states.states import GamesOrderStates, ProductSearchStates, SMMOrderStates
from keyboards.games import sub_categories_kb, sections_kb, products_kb, product_confirm_kb, product_confirm_with_coupon_kb, product_search_results_kb, favorites_kb, store_servers_kb
from keyboards.main_menu import insufficient_balance_kb, confirm_large_order_kb, back_to_main_kb
logger = logging.getLogger(__name__)
router = Router(name='games')


class SmmAppLabelFilter(Filter):
    """Matches leftover reply-keyboard / typed labels like ``تيك توك 🎵``."""

    async def __call__(self, message: Message) -> bool:
        from services.smm_catalog import is_smm_app_label

        return is_smm_app_label(message.text or "")


@router.callback_query(F.data == "none")
async def cosmetic_detail_button(callback: CallbackQuery):
    """أزرار العرض في تفاصيل الخدمة — شكلية فقط (callback_data="none")."""
    await callback.answer("هذي معلومة عرض فقط 🏷", show_alert=False)


def _auto_lang(scope=None) -> str:
    user = (scope or {}).get("db_user")
    if user is None:
        callback = (scope or {}).get("callback")
        user = getattr(callback, "from_user", None)
    return getattr(user, "language_code", "ar") or "ar"

async def _dual_price(amount, db_user, session):
    """السعر بالدولار + ما يعادله بعملة عرض المستخدم (+ ليرة سورية إن فعلها الأدمن)."""
    if db_user is None:
        return f'${amount}'
    display = await CurrencyService.format_dual(amount, db_user, session)
    note = await CurrencyService.syp_note(amount, db_user, session)
    return display + note


async def _resolve_discount_code(session, code: str, user_id: int, order_amount):
    """يحل كود الخصم: كوبون أولاً ثم كود حملة (يدعم ميزة campaign_codes).

    يُرجع (kind, obj, discount) حيث kind ∈ {"coupon", "campaign"}.
    يرمي CouponError برسالة موحدة إن لم يكن الكود صالحاً في الجهتين
    (مع إعادة رسالة السبب الأصلي إن كان الكود معروفاً لكنه مرفوض).
    """
    if not await FeatureService.enabled("campaign_codes"):
        coupon = await CouponService.validate_coupon(session, code, user_id, order_amount)
        discount = CouponService.calculate_discount(coupon, order_amount)
        return "coupon", coupon, discount

    try:
        coupon = await CouponService.validate_coupon(session, code, user_id, order_amount)
        discount = CouponService.calculate_discount(coupon, order_amount)
        return "coupon", coupon, discount
    except CouponError as e:
        campaign_error = None
        try:
            campaign = await CampaignService.validate(session, code, user_id, order_amount)
            discount = CampaignService.calculate_discount(campaign, order_amount)
            return "campaign", campaign, discount
        except CampaignCodeError as ce:
            campaign_error = ce
        if "غير موجود" in str(e):
            raise CouponError(str(campaign_error or e)) from None
        raise

def _glang(db_user) -> str:
    return getattr(db_user, 'language_code', 'ar') or 'ar'


def _eta_line(product, language: str = "ar") -> str:
    """سطر الوقت التقريبي للاكتمال إن وُجد."""
    from services.smm_price_service import SMM_DEFAULT_ETA

    # خدمات الرشق (رابط/كمية) وقتها موحد: بين 1 و 25 دقيقة.
    if getattr(product, "requires_link", False) or getattr(product, "requires_quantity", False):
        return f"\n{I18nService.t('eta_label', language)}: {SMM_DEFAULT_ETA}"
    eta = getattr(product, "estimated_time", None)
    if eta:
        return f"\n{I18nService.t('eta_label', language)}: {esc(str(eta))}"
    return ""


def catalog_header(title: str, description: str | None = None) -> str:
    """رأس شاشة المتجر: عنوان عريض + شرح اختياري، كله نص آمن للـ HTML.

    كل النصوص هنا قادمة من قاعدة البيانات (اسم القسم، شرح كتبه الأدمن، أو
    اسم خدمة مسحوب من المزود) لذا تُهرّب وإلا كسرت تحليل تيليجرام للرسالة
    بأكملها: ``Bad Request: can't parse entities``.
    """
    head = f"<b>{esc(title)}</b>"
    desc = (description or "").strip()
    if desc:
        head += f"\n<i>{esc(desc)}</i>\n"
    else:
        head += "\n"
    return head

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
        await message.answer(f"{I18nService.t('ux_games_119_3', _auto_lang(locals()))}{esc(query_text)}</b>.", reply_markup=back_to_main_kb())
        return
    await message.answer(f"{I18nService.t('ux_games_125_4', _auto_lang(locals()))}{esc(query_text)}{I18nService.t('ux_games_125_5', _auto_lang(locals()))}{len(products)}{I18nService.t('ux_games_125_6', _auto_lang(locals()))}", reply_markup=product_search_results_kb(products))

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

async def _servers_for_subcategory(session, sub_category_id: int):
    """السيرفرات المتاحة لقسم فرعي.

    أولوية البحث:
    1. سيرفرات هذا القسم الفرعي نفسه.
    2. سيرفرات القسم الأب (لتطبيقات الرشق ذات الأقسام الداخلية).
    3. سيرفرات «عام» (scope=global, scope_id=0).
    """
    sub_cat = await DynamicService.get_sub_category(session, sub_category_id)
    parent_id = getattr(sub_cat, "parent_sub_category_id", None) if sub_cat else None
    for candidate_id in (sub_category_id, parent_id):
        if candidate_id:
            servers = await StoreServerService.active_for_scope(session, "subcategory", candidate_id)
            if servers:
                return servers
    return await StoreServerService.active_for_scope(session, "global", 0)


async def _server_from_state(session, state: FSMContext) -> StoreServer | None:
    """السيرفر المختار في حالة اللوحة (يرجَّع None إذا عُطّل/حُذف)."""
    data = await state.get_data()
    server_id = data.get("server_id")
    if not server_id:
        return None
    try:
        server_id = int(server_id)
    except (TypeError, ValueError):
        return None
    server = await StoreServerService.get(session, server_id)
    if server is None or not server.is_active:
        return None
    return server


async def _server_unit_price(session, product, server: StoreServer | None) -> Decimal:
    """سعر الوحدة بعد تطبيق أولوية الهوامش.

    الترتيب: المنتج اليدوي > القسم الفرعي/التطبيق > القسم > السيرفر >
    السعر المحفوظ. لذلك هامش «لايكات انستا» أو «متابعين تيك توك»
    يتحكم بسعرها حتى لو كان للسيرفر هامش آخر.

    للمنتجات المربوطة بمزود (provider_service_ref_id): يُحسب السعر
    من سعر المزود اللحظي مباشرة بدلاً من cost_price_usd المخزّن،
    فيبقى السعر محدثاً تلقائياً دون حاجة لإعادة المزامنة.
    """
    from services.margin_service import MarginService
    from services.smm_price_service import live_provider_price

    # هل المنتج مربوط بخدمة مزود حية؟
    live_cost = await live_provider_price(product, session)
    if live_cost is not None:
        margin, _ = await MarginService.effective_margin(session, product)
        return MarginService.price_from_cost(live_cost, margin)

    return await MarginService.product_sell_price(session, product, server)


async def _server_total_price(session, product, quantity: int, server: StoreServer | None) -> Decimal:
    """السعر الإجمالي (يدعم حساب الرشق لكل 1000)."""
    from services.margin_service import MarginService

    unit = await MarginService.product_sell_price(session, product, server)
    if not getattr(product, "requires_quantity", False):
        return unit.quantize(Decimal("0.0001"), rounding="ROUND_HALF_UP")
    display = getattr(product, "display_type", None)
    value = getattr(display, "value", display)
    if value == "per_min_quantity":
        min_qty = int(product.min_quantity or 0)
        if min_qty > 0:
            total = unit * Decimal(str(quantity)) / Decimal(str(min_qty))
        else:
            total = unit
    elif value == "fixed_total":
        total = unit
    else:
        # PER_1000 — السعر هو لكمية 1000.
        total = unit * Decimal(str(quantity)) / Decimal("1000")
    return total.quantize(Decimal("0.0001"), rounding="ROUND_HALF_UP")


async def _show_subcategory(target, session, sub_cat, language: str = "ar", server: StoreServer | None = None, state: FSMContext | None = None):
    """Render a subcategory.

    - إذا كان القسم تطبيقاً يحوي أقساماً داخلية (متابعون/لايكات/مشاهدات)
      تعرض الأقسام الداخلية أولاً (ميزة أقسام الرشق الداخلية).
    - وإلا تعرض منتجات القسم مباشرة.
    ``target`` is a Message or CallbackQuery. Products are loaded with an
    explicit query so AsyncSession never tries a lazy ``sub_cat.products``
    IO (MissingGreenlet).
    """
    """Render a subcategory.

    - إذا كان القسم تطبيقاً يحوي أقساماً داخلية (متابعون/لايكات/مشاهدات)
      تعرض الأقسام الداخلية أولاً (ميزة أقسام الرشق الداخلية).
    - وإلا تعرض منتجات القسم مباشرة.
    ``target`` is a Message or CallbackQuery. Products are loaded with an
    explicit query so AsyncSession never tries a lazy ``sub_cat.products``
    IO (MissingGreenlet).
    """
    from services.feature_service import FeatureService
    from services.smm_catalog import button_label

    # العنوان والشرح من قاعدة البيانات → تهريب إلزامي، وإلا حرف «<» في اسم
    # خدمة مسحوب من المزود يكسر شاشة القسم كلها.
    header = catalog_header(button_label(sub_cat.name_ar, sub_cat.emoji), sub_cat.description)
    products = await DynamicService.get_active_products(session, sub_cat.id)

    if server is not None:
        products = await StoreServerService.filter_products(products, server)

    inner_sections: list[tuple[object, int]] = []
    if await FeatureService.enabled("smm_inner_sections", default=True):
        children = await DynamicService.get_active_child_sections(session, sub_cat.id)
        if children:
            counts = await DynamicService.active_product_counts_by_sub(
                session, [child.id for child in children]
            )
            inner_sections = [
                (child, counts.get(child.id, 0))
                for child in children
                if counts.get(child.id, 0) > 0
            ]

    if inner_sections:
        text = f"{header}{I18nService.t('smm_choose_inner_service', language)}"
        markup = sections_kb(sub_cat.category_id, inner_sections)
    elif products:
        back_sub_id = sub_cat.parent_sub_category_id
        server_line = ""
        if server is not None:
            server_line = f"\n🖥 السيرفر: <b>{esc(server.name_ar)}</b>"
        from services.margin_service import MarginService

        price_map = {
            p.id: await MarginService.product_sell_price(session, p, server)
            for p in products
        }
        text = f"{header}{server_line}{I18nService.t('ux_games_282_17', language)}"
        markup = products_kb(
            sub_cat.id,
            products,
            sub_cat.category_id,
            back_sub_id=back_sub_id,
            server=server,
            price_map=price_map,
        )
    elif server is not None:
        back_sub_id = sub_cat.parent_sub_category_id
        text = f"{header}\n⚠️ لا توجد منتجات لهذا السيرفر في هذا القسم حالياً."
        markup = store_servers_kb(
            sub_cat.id,
            await StoreServerService.active_for_scope(session, "subcategory", sub_cat.id)
            or await StoreServerService.active_for_scope(session, "global", 0),
            sub_cat.category_id,
        )
    else:
        text = f"{header}{I18nService.t('ux_games_276_16', language)}"
        markup = back_to_main_kb(language)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith('cat:'))
async def category_selected(callback: CallbackQuery, session, state: FSMContext = None):
    category_id = int(callback.data.split(':')[1])
    category = await DynamicService.get_category(session, category_id)
    if not category or not category.is_active:
        await callback.answer(I18nService.t('ux_games_236_12', _auto_lang(locals())), show_alert=True)
        return
    if state is not None:
        await state.update_data(server_id=None)
    await callback.answer()
    # المستوى الأول فقط (تطبيقات قسم الرشق مثلًا)؛ الأقسام الداخلية تظهر
    # عند فتح التطبيق نفسه عبر subcat:.
    language = _auto_lang(locals())
    # شرح القسم (إن ضبطه الأدمن) يظهر للزبون أعلى الشاشة — ضمن رأس مُهرَّب.
    header = f"{esc(category.emoji)} {catalog_header(category.name_ar, category.description)}"
    sub_cats = await DynamicService.get_active_root_sub_categories(session, category_id)
    if not sub_cats:
        await callback.message.edit_text(f"{header}{I18nService.t('ux_games_246_13', language)}", reply_markup=back_to_main_kb())
        return
    await callback.message.edit_text(f"{header}{I18nService.t('ux_games_252_14', language)}", reply_markup=sub_categories_kb(category_id, sub_cats))

@router.callback_query(F.data.startswith('subcat:'))
async def sub_category_selected(callback: CallbackQuery, session, db_user=None, state: FSMContext = None):
    sub_cat_id = int(callback.data.split(':')[1])
    sub_cat = await DynamicService.get_sub_category(session, sub_cat_id)
    if not sub_cat or not sub_cat.is_active:
        await callback.answer(I18nService.t('ux_games_266_15', _auto_lang(locals())), show_alert=True)
        return
    await callback.answer()
    language = _glang(db_user) if db_user else _auto_lang(locals())
    servers = await _servers_for_subcategory(session, sub_cat.id)
    if servers and state is not None:
        from services.smm_catalog import button_label

        # زر تغيير سيرفر في القسم: لا نعرض المنتجات حتى يختار السيرفر.
        header = catalog_header(button_label(sub_cat.name_ar, sub_cat.emoji), sub_cat.description)
        text = f"{header}🖥 اختر السيرفر الذي تريد الشراء منه:"
        await callback.message.edit_text(text, reply_markup=store_servers_kb(sub_cat.id, servers, sub_cat.category_id))
        return
    if state is not None:
        await state.update_data(server_id=None)
    await _show_subcategory(callback, session, sub_cat, language)


@router.callback_query(F.data.startswith('svc_pick:'))
async def subcategory_server_picked(callback: CallbackQuery, session, db_user=None, state: FSMContext = None):
    """اختيار سيرفر لقسم فرعي ثم عرض منتجات ذلك السيرفر."""
    parts = callback.data.split(':')
    if len(parts) < 3:
        await callback.answer("⚠️ بيانات السيرفر ناقصة", show_alert=True)
        return
    try:
        sub_cat_id = int(parts[1])
        server_id = int(parts[2])
    except ValueError:
        await callback.answer("⚠️ بيانات السيرفر غير صحيحة", show_alert=True)
        return
    sub_cat = await DynamicService.get_sub_category(session, sub_cat_id)
    if not sub_cat or not sub_cat.is_active:
        await callback.answer(I18nService.t('ux_games_266_15', _auto_lang(locals())), show_alert=True)
        return
    server = await StoreServerService.get(session, server_id)
    if server is None or not server.is_active:
        await callback.answer("⚠️ هذا السيرفر معطّل أو محذوف", show_alert=True)
        return
    if state is not None:
        await state.update_data(
            server_id=server.id,
            server_scope=server.scope,
            server_scope_id=server.scope_id,
            server_margin_percent=str(server.margin_percent) if server.margin_percent is not None else "",
        )
    language = _glang(db_user) if db_user else _auto_lang(locals())
    await callback.answer()
    await _show_subcategory(callback, session, sub_cat, language, server=server, state=state)


@router.message(StateFilter(None), SmmAppLabelFilter())
async def catalog_label_selected(message: Message, session, db_user=None, state: FSMContext = None):
    """Open a رشق app when the user sends its name, e.g. ``تيك توك 🎵``.

    Older clients keep a ReplyKeyboard whose buttons send the label as a
    Message. Without this handler the text either went nowhere or hit a
    leftover FSM that lazy-loaded ORM relations and raised MissingGreenlet.
    """
    label = (message.text or "").strip()
    sub_cat = await DynamicService.find_subcategory_by_label(session, label)
    language = _glang(db_user) if db_user else "ar"
    if sub_cat is None or not sub_cat.is_active:
        await message.answer(
            I18nService.t("ux_games_266_15", language),
            reply_markup=back_to_main_kb(language),
        )
        return
    servers = await _servers_for_subcategory(session, sub_cat.id)
    if servers and state is not None:
        from services.smm_catalog import button_label

        header = catalog_header(button_label(sub_cat.name_ar, sub_cat.emoji), sub_cat.description)
        text = f"{header}🖥 اختر السيرفر الذي تريد الشراء منه:"
        await message.answer(text, reply_markup=store_servers_kb(sub_cat.id, servers, sub_cat.category_id))
        return
    if state is not None:
        await state.update_data(server_id=None)
    await _show_subcategory(message, session, sub_cat, language)

def _product_head(product, icon: str) -> str:
    """سطر عنوان صفحة المنتج (الاسم + شرح الأدمن/المزود) مُهرّباً.

    أسماء الخدمات المسحوبة من المزود مليئة بـ ``<`` و``&`` (مثل
    ``Followers < 1h & HQ``)؛ بدون تهريب ترفض تيليجرام الرسالة كلها.
    """
    desc = (getattr(product, "description", None) or "").strip()
    desc_line = f"\n<i>{esc(desc)}</i>" if desc else ""
    return f"{icon} <b>{esc(product.name_ar)}</b>{desc_line}"


@router.callback_query(F.data.startswith('prod:'))
async def product_selected(callback: CallbackQuery, session, db_user: User, state: FSMContext):
    data = await state.get_data()
    server_id = data.get('server_id')
    await state.clear()
    product_id = int(callback.data.split(':')[1])
    product = await DynamicService.get_product(session, product_id)
    if not product or product.status != ProductStatus.ACTIVE:
        await callback.answer(I18nService.t('ux_games_305_18', _auto_lang(locals())), show_alert=True)
        return
    server = None
    if server_id:
        server = await StoreServerService.get(session, int(server_id))
        if server is None or not server.is_active:
            server = None
    await callback.answer()
    sub_cat = product.sub_category
    language = _glang(db_user)

    # Show rich SMM provider details when product has a linked provider service.
    # التفاصيل تُعرض كأزرار Inline منسّقة (قيمة + عنوان) بدل نص عادي،
    # والقيم كلها ديناميكية من المزود وقاعدة البيانات.
    if product.provider_service_ref_id and product.fulfillment_type == ProductFulfillmentType.API:
        from services.smm_price_service import service_details, format_details_kb, live_sell_price
        det = await service_details(product, session)
        live_price = await live_sell_price(product, session)
        back_callback = f"subcat:{sub_cat.id}" if sub_cat else "back_to_main"
        head = _product_head(product, '📈')
        details_kb = format_details_kb(det, live_price, back_callback)
        await callback.message.edit_text(head, reply_markup=details_kb)
        await state.update_data(product_id=product_id, price_override=str(live_price))
        if product.requires_link:
            await callback.message.answer(I18nService.t('send_link', language))
            await state.set_state(SMMOrderStates.waiting_link)
        else:
            confirm_q = I18nService.t('confirm_purchase_q', language)
            await callback.message.answer(confirm_q, reply_markup=product_confirm_kb(product_id, sub_cat.id if sub_cat else 0))
        return

    unit_price = await _server_unit_price(session, product, server)
    price_display = await _dual_price(unit_price, db_user, session)
    await state.update_data(product_id=product_id)
    if server is not None:
        await state.update_data(server_id=server.id)
    price_label = I18nService.t('price', language)
    confirm_q = I18nService.t('confirm_purchase_q', language)
    if product.requires_player_id:
        head = _product_head(product, '🎮')
        await callback.message.edit_text(f'{head}\n💰 {price_label}: <b>{esc(price_display)}</b>{_eta_line(product, language)}\n\n' + I18nService.t('send_player_id', language))
        await state.set_state(GamesOrderStates.waiting_player_id)
    elif product.requires_link:
        head = _product_head(product, '📈')
        if product.requires_quantity:
            await callback.message.edit_text(f'{head}\n💰 {price_label}: <b>{esc(price_display)}</b> / 1000{_eta_line(product, language)}\n' + I18nService.t('quantity_limits', language, min_q=product.min_quantity, max_q=product.max_quantity) + '\n\n' + I18nService.t('send_link', language))
            await state.set_state(SMMOrderStates.waiting_link)
        else:
            await callback.message.edit_text(f'{head}\n💰 {price_label}: <b>{esc(price_display)}</b>{_eta_line(product, language)}\n\n' + I18nService.t('send_link', language))
            await state.update_data(quantity=1)
            await state.set_state(SMMOrderStates.waiting_link)
    else:
        head = _product_head(product, '📦')
        await callback.message.edit_text(f'{head}\n💰 {price_label}: <b>{esc(price_display)}</b>\n\n{confirm_q}', reply_markup=product_confirm_kb(product_id, sub_cat.id if sub_cat else 0))

@router.message(GamesOrderStates.waiting_player_id)
async def player_id_received(message: Message, state: FSMContext, session, db_user: User):
    data = await state.get_data()
    product_id = data.get('product_id')
    if not product_id:
        # حالة فاقدة (جلسة قديمة / إعادة تشغيل) لا تُسقط البوت بخطأ KeyError.
        await message.answer(I18nService.t('ux_games_386_19', _auto_lang(locals())))
        await state.clear()
        return
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
        await message.answer(f"⚠️ {esc(exc)}{I18nService.t('ux_games_397_20', _auto_lang(locals()))}")
        return
    await state.update_data(target=player_id, quantity=1)
    server = await _server_from_state(session, state)
    unit_price = await _server_unit_price(session, product, server)
    await message.answer(f"🎮 <b>{esc(product.name_ar)}{_eta_line(product)}{I18nService.t('ux_games_406_21', _auto_lang(locals()))}{esc(player_id)}{I18nService.t('ux_games_406_22', _auto_lang(locals()))}{unit_price}{I18nService.t('ux_games_406_23', _auto_lang(locals()))}", reply_markup=product_confirm_kb(product_id, sub_cat.id if sub_cat else 0))

@router.message(SMMOrderStates.waiting_link)
async def smm_link_received(message: Message, state: FSMContext, session):
    link = message.text.strip()
    if not link.startswith('http'):
        await message.answer(I18nService.t('ux_games_425_24', _auto_lang(locals())))
        return
    data = await state.get_data()
    product_id = data.get('product_id')
    if not product_id:
        await message.answer(I18nService.t('ux_games_433_25', _auto_lang(locals())))
        await state.clear()
        return
    product = await DynamicService.get_product(session, product_id)
    if not product:
        await message.answer(I18nService.t('ux_games_433_25', _auto_lang(locals())))
        await state.clear()
        return
    await state.update_data(target=link)
    server = await _server_from_state(session, state)
    unit_price = await _server_unit_price(session, product, server)
    if product.requires_quantity:
        await message.answer(f"{I18nService.t('ux_games_441_26', _auto_lang(locals()))}{product.min_quantity}{I18nService.t('ux_games_441_27', _auto_lang(locals()))}{product.max_quantity}")
        await state.set_state(SMMOrderStates.waiting_quantity)
    else:
        await state.update_data(quantity=1)
        sub_cat = product.sub_category
        await message.answer(f"📈 <b>{esc(product.name_ar)}{_eta_line(product)}{I18nService.t('ux_games_450_28', _auto_lang(locals()))}{esc(link)}{I18nService.t('ux_games_450_29', _auto_lang(locals()))}{unit_price}{I18nService.t('ux_games_450_30', _auto_lang(locals()))}", reply_markup=product_confirm_kb(product_id, sub_cat.id if sub_cat else 0))

@router.message(SMMOrderStates.waiting_quantity)
async def smm_quantity_received(message: Message, state: FSMContext, session):
    try:
        quantity = int(message.text.strip())
    except ValueError:
        await message.answer(I18nService.t('ux_games_469_31', _auto_lang(locals())))
        return
    data = await state.get_data()
    product_id = data.get('product_id')
    if not product_id:
        await message.answer(I18nService.t('ux_games_477_32', _auto_lang(locals())))
        await state.clear()
        return
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
    server = await _server_from_state(session, state)
    total_price = await _server_total_price(session, product, quantity, server)
    await state.update_data(quantity=quantity, total_price=str(total_price))
    sub_cat = product.sub_category
    link = data.get('target', '—')
    await message.answer(f"📈 <b>{esc(product.name_ar)}{_eta_line(product)}{I18nService.t('ux_games_501_35', _auto_lang(locals()))}{esc(link)}{I18nService.t('ux_games_501_36', _auto_lang(locals()))}{quantity}{I18nService.t('ux_games_501_37', _auto_lang(locals()))}{total_price}{I18nService.t('ux_games_501_38', _auto_lang(locals()))}", reply_markup=product_confirm_kb(product_id, sub_cat.id if sub_cat else 0))

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
        kind, coupon, discount = await _resolve_discount_code(session, code, db_user.id, product.price_usd)
    except CouponError as e:
        await message.answer(str(e))
        await state.clear()
        return
    sub_cat = product.sub_category
    await message.answer(f"{I18nService.t('ux_games_553_41', _auto_lang(locals()))}{esc(coupon.code)}{I18nService.t('ux_games_553_42', _auto_lang(locals()))}{product.price_usd}{I18nService.t('ux_games_553_43', _auto_lang(locals()))}{discount}{I18nService.t('ux_games_553_44', _auto_lang(locals()))}{product.price_usd - discount}$</b>", reply_markup=product_confirm_with_coupon_kb(product_id, sub_cat.id if sub_cat else 0, coupon.code, str(discount)))

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

def _is_digital_subscription_provider(provider) -> bool:
    """هل مزود اشتراكات رقمية (تسليم لحظي)؟ ggsoma/partner_v1."""
    raw = getattr(provider, "custom_config", None)
    if not raw:
        return False
    try:
        cfg = json.loads(raw)
    except Exception:
        return False
    return isinstance(cfg, dict) and cfg.get("engine") in {"ggsoma", "partner_v1"}


async def _provider_balance_usd(protocol, provider) -> Decimal | None:
    """رصيد المزود بالدولار، أو None إن لم يُعرف (الجهل لا يمنع البيع)."""
    from protocols.base import ProtocolError

    try:
        balance = await protocol.get_balance()
        return Decimal(str(balance.amount)) * Decimal(str(getattr(provider, "rate_to_usd", None) or 1))
    except ProtocolError:
        pass
    except Exception:
        logger.warning("تعذر جلب رصيد المزود %s", getattr(provider, "id", "?"))
    if provider is not None and getattr(provider, "balance", None) is not None:
        try:
            return Decimal(str(provider.balance)) * Decimal(str(getattr(provider, "rate_to_usd", None) or 1))
        except Exception:
            return None
    return None


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
    elif fulfillment != ProductFulfillmentType.API.value or not product.api_provider_id or (not product.provider_service_id):
        # المزود الأساسي قد يكون معطلاً — لكن مساراً احتياطياً (تحت ميزة
        # catalog_failover) ما يزال قادراً على التنفيذ، لذا لا نحظر هنا.
        # يقرر المسار في _finalize_purchase عبر routes_for.
        await callback.answer(I18nService.t('ux_games_650_47', _auto_lang(locals())), show_alert=True)
        await state.clear()
        return
    await callback.answer(I18nService.t('ux_games_656_48', _auto_lang(locals())))
    fsm_data = await state.get_data()
    target = fsm_data.get('target', '')
    quantity = fsm_data.get('quantity', 1)
    server = await _server_from_state(session, state)
    total_price = await _server_total_price(session, product, quantity, server)
    promotion, promotion_discount = await PromotionService.get_best_promotion(session, product.id, total_price)
    if fulfillment == ProductFulfillmentType.INVENTORY.value and coupon_code:
        await callback.answer(I18nService.t('ux_games_678_49', _auto_lang(locals())), show_alert=True)
        await state.clear()
        return
    discount = Decimal('0')
    coupon = None
    campaign = None
    if coupon_code:
        try:
            kind, code_obj, code_discount = await _resolve_discount_code(
                session, coupon_code, db_user.id, total_price
            )
            if kind == "campaign":
                campaign = code_obj
            else:
                coupon = code_obj
            discount = code_discount
        except CouponError:
            discount = Decimal('0')
            coupon = None
    tier_discount, _tier_label = await TieredPricingService.discount_for(session, db_user.id, product, total_price, quantity)
    if promotion_discount >= max(discount, tier_discount) and promotion_discount > 0:
        discount = promotion_discount
        coupon = None
        campaign = None
    elif tier_discount > discount:
        discount = tier_discount
        coupon = None
        campaign = None
        promotion = None
    elif discount > 0:
        promotion = None
    final_price = total_price - discount
    final_price = await AgentService.apply_discount(session, db_user.id, final_price)
    large_confirm = await SettingsService.get_decimal('large_order_confirm_usd', Decimal('20'))
    if final_price >= large_confirm:
        language = _glang(db_user)
        amount_display = await _dual_price(final_price, db_user, session)
        await callback.message.answer(I18nService.t('large_order_confirm', language, product=esc(product.name_ar), amount=esc(amount_display)), reply_markup=confirm_large_order_kb(f"prod_final:{product_id}:{coupon_code or 'none'}", language))
        return
    await _finalize_purchase(callback, session, db_user, bot, state, product, target, quantity, final_price, discount, coupon, promotion, campaign)

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
    server = await _server_from_state(session, state)
    total_price = await _server_total_price(session, product, quantity, server)
    fulfillment = getattr(product.fulfillment_type, 'value', product.fulfillment_type)
    promotion, promotion_discount = await PromotionService.get_best_promotion(session, product.id, total_price)
    if fulfillment == ProductFulfillmentType.INVENTORY.value and coupon_code:
        await callback.answer(I18nService.t('ux_games_786_52', _auto_lang(locals())), show_alert=True)
        await state.clear()
        return
    discount = Decimal('0')
    coupon = None
    campaign = None
    if coupon_code:
        try:
            kind, code_obj, code_discount = await _resolve_discount_code(
                session, coupon_code, db_user.id, total_price
            )
            if kind == "campaign":
                campaign = code_obj
            else:
                coupon = code_obj
            discount = code_discount
        except CouponError:
            pass
    tier_discount, _tier_label = await TieredPricingService.discount_for(session, db_user.id, product, total_price, quantity)
    if promotion_discount >= max(discount, tier_discount) and promotion_discount > 0:
        discount = promotion_discount
        coupon = None
        campaign = None
    elif tier_discount > discount:
        discount = tier_discount
        coupon = None
        campaign = None
        promotion = None
    elif discount > 0:
        promotion = None
    final_price = total_price - discount
    final_price = await AgentService.apply_discount(session, db_user.id, final_price)
    await _finalize_purchase(callback, session, db_user, bot, state, product, target, quantity, final_price, discount, coupon, promotion, campaign)

async def _finalize_purchase(callback, session, db_user, bot, state, product, target, quantity, final_price, discount, coupon, promotion, campaign=None):
    notifier = NotificationService(bot)
    fulfillment = getattr(product.fulfillment_type, 'value', product.fulfillment_type)
    if fulfillment == ProductFulfillmentType.API.value and (not product.api_provider_id or not product.provider_service_id):
        # المزود الأساسي المعطّل لا يحظر الطلب: المسارات الاحتياطية
        # (catalog_failover) قد تنفذ بدلاً عنه — تُفحص داخل الفرع API أدناه.
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
            delivery_note = f"\n📝 ملاحظة: {esc(metadata['note'])}"
        await callback.message.answer(f"{I18nService.t('ux_games_899_54', _auto_lang(locals()))}{esc(product.name_ar)}{I18nService.t('ux_games_899_55', _auto_lang(locals()))}{order.id}{I18nService.t('ux_games_899_56', _auto_lang(locals()))}{final_price}{I18nService.t('ux_games_899_57', _auto_lang(locals()))}{esc(delivered_value)}</code>{delivery_note}{I18nService.t('ux_games_899_58', _auto_lang(locals()))}")
        await notifier.notify_admin(f'📦 <b>تم تسليم منتج من المخزون</b>\n\n🆔 الطلب: #{order.id}\n👤 المستخدم: {db_user.telegram_id}\n📦 المنتج: {esc(product.name_ar)}\n💰 المبلغ: {final_price}$', notification_type="order")
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
    elif campaign and discount > 0:
        try:
            await CampaignService.apply(session, campaign, db_user.id, discount)
        except CampaignCodeError as exc:
            logger.warning('تعذر تطبيق كود الحملة أثناء الطلب: %s', exc)
            await BalanceService.add_balance(session, db_user.id, final_price, TransactionType.REFUND, description='استرجاع - تعذر تطبيق كود الحملة')
            await callback.message.answer(I18nService.t('ux_games_968_60', _auto_lang(locals())))
            await state.clear()
            return
    external_order_id = None
    order_status = UnifiedOrderStatus.PENDING
    status_message = 'بانتظار تنفيذ الإدارة' if fulfillment == ProductFulfillmentType.MANUAL.value else 'بانتظار التنفيذ'
    instant_raw = None
    if fulfillment == ProductFulfillmentType.MANUAL.value:
        pass
    elif product.api_provider_id and product.provider_service_id:
        from services.catalog_routing_service import CatalogRoutingService
        routes = await CatalogRoutingService.routes_for(session, product)
        if not routes:
            await callback.message.answer(I18nService.t('ux_games_859_53', _auto_lang(locals())))
            await state.clear()
            return
        external_order_id = None
        instant_raw = None
        used_route = None
        route_errors: list[str] = []
        low_balance_names: list[str] = []
        non_balance_error = False
        for route in routes:
            provider = await session.get(ApiProvider, route.api_provider_id)
            if provider is None or not provider.is_active:
                route_errors.append(f"المزود {route.api_provider_id} غير نشط")
                continue
            try:
                protocol = ProtocolFactory.create_from_provider(provider)
                if _is_digital_subscription_provider(provider):
                    balance_usd = await _provider_balance_usd(protocol, provider)
                    if balance_usd is not None and balance_usd < final_price:
                        route_errors.append(f"رصيد {provider.name} غير كافٍ ({balance_usd}$)")
                        low_balance_names.append(provider.name)
                        continue
                result = await protocol.place_order(
                    service_id=route.provider_service_id, target=target, quantity=quantity,
                )
                external_order_id = result.external_order_id
                used_route = route
                if not route.is_primary:
                    try:
                        from services.feature_service import FeatureService as _FS
                        await _FS.track(
                            "catalog_failover", "failover_used",
                            user_id=db_user.id,
                            value=f"product:{product.id}:provider:{route.api_provider_id}",
                        )
                    except Exception:
                        pass
                if str(getattr(result, 'status', '') or '').lower() == 'completed':
                    order_status = UnifiedOrderStatus.COMPLETED
                    status_message = 'مكتمل - توصيل فوري'
                    raw = getattr(result, 'raw', None)
                    if isinstance(raw, dict):
                        instant_raw = raw
                else:
                    order_status = UnifiedOrderStatus.PROCESSING
                    status_message = 'تم إرسال الطلب للمزود'
                break
            except ProtocolInsufficientFundsError as e:
                route_errors.append(f"رصيد {getattr(provider, 'name', str(provider.id))}: {e}")
                low_balance_names.append(getattr(provider, 'name', str(provider.id)))
                logger.warning('فشل رصيد المزود %s للمنتج %s: %s', getattr(provider, 'id', '?'), product.id, e)
                continue
            except ProtocolError as e:
                non_balance_error = True
                route_errors.append(f"المزود {getattr(provider, 'name', str(provider.id))}: {e}")
                logger.error('فشل إرسال الطلب للمزود %s للمنتج %s: %s', getattr(provider, 'id', '?'), product.id, e)
                try:
                    from services.auto_failover_service import AutoFailoverService

                    await AutoFailoverService.record_failure(
                        session,
                        product.id,
                        route.api_provider_id,
                        is_backup_route=not route.is_primary,
                        bot=bot,
                    )
                except Exception:
                    pass
                continue
        if used_route is None:
            all_low_balance = bool(low_balance_names) and not non_balance_error
            if all_low_balance:
                low_bal_prov = await session.get(ApiProvider, routes[0].api_provider_id)
                low_bal_id = low_bal_prov.id if low_bal_prov else product.api_provider_id
                low_bal_name = low_balance_names[0]
                order = UnifiedOrder(
                    user_id=db_user.id, product_id=product.id,
                    api_provider_id=low_bal_id,
                    promotion_id=promotion.id if promotion else None,
                    target=target, quantity=quantity,
                    price_usd=final_price, cost_price_usd=product.cost_price_usd,
                    status=UnifiedOrderStatus.PENDING,
                    status_message='بانتظار تنفيذ الإدارة (رصيد المزود غير كافٍ)',
                )
                session.add(order)
                await session.commit()
                await session.refresh(order)
                if promotion:
                    await PromotionService.mark_used(session, promotion.id)
                await DynamicService.increment_product_sold(session, product.id, quantity)
                await CashbackService.apply_cashback(session, db_user.id, order.id, 'unified_orders', final_price)
                await LoyaltyService.award_purchase_points(session, db_user.id, 'unified_orders', order.id, final_price)
                await callback.message.answer(
                    f"✅ <b>تم شراء {esc(product.name_ar)}</b>\n\n"
                    f"🆔 رقم الطلب: #{order.id}\n"
                    f"💰 المبلغ: {final_price}$\n\n"
                    "🕐 <b>سيصلك الكود/الحساب خلال دقائق</b> — "
                    "أُشعرت الإدارة بتسليم طلبك وستصلك رسالة فور وصوله."
                )
                from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                await notifier.notify_admin(
                    "🚨 <b>رصيد المزود غير كافٍ — اشتراك رقمي</b>\n\n"
                    f"🔌 المزود: <b>{esc(low_bal_name)}</b>\n"
                    f"👤 المستخدم: {db_user.telegram_id} (@{db_user.username or '-'})\n"
                    f"📦 المنتج: <b>{esc(product.name_ar)}</b>\n"
                    f"🆔 الطلب: <b>#{order.id}</b>\n"
                    f"🎯 الهدف: {esc(target) if target else '—'}\n"
                    f"💰 المبلغ: {final_price}$\n\n"
                    "✅ «أرسل البيانات يدوياً» بعد شحن رصيد المزود وجلب "
                    "الحساب — يُشعر المستخدم فوراً.\n"
                    "❌ «إلغاء + استرجاع» إن لم تتمكن.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="✅ أرسل البيانات يدوياً", callback_data=f"admin:sub_send:{order.id}")],
                            [InlineKeyboardButton(text="❌ إلغاء + استرجاع", callback_data=f"admin:order_refund_ask:{order.id}", style="danger")],
                        ]
                    ),
                )
            else:
                await BalanceService.add_balance(
                    session, db_user.id, final_price,
                    TransactionType.REFUND, description='استرجاع - فشل الإرسال للمزود',
                )
                await callback.message.answer(I18nService.t('ux_games_1007_61', _auto_lang(locals())))
                errors_summary = " | ".join(route_errors[:3])
                await notifier.notify_admin(
                    '🚨 <b>فشل إرسال طلب للمزود</b>\n\n'
                    f'📦 المنتج: <b>{esc(product.name_ar)}</b>\n'
                    f'👤 المستخدم: <code>{db_user.telegram_id}</code>\n'
                    f'⚠️ الأخطاء: <code>{errors_summary}</code>\n\n'
                    '🛠 <b>الحل:</b> تحقق من آيدي الخدمة عند المزود، وصحة الرابط/الكمية، '
                    'وحالة المزود. تم استرجاع رصيد المستخدم.'
                )
            await state.clear()
            return
    order = UnifiedOrder(user_id=db_user.id, product_id=product.id, api_provider_id=used_route.api_provider_id if used_route else product.api_provider_id, promotion_id=promotion.id if promotion else None, external_order_id=external_order_id, target=target, quantity=quantity, price_usd=final_price, cost_price_usd=product.cost_price_usd, status=order_status, status_message=status_message, result_data=json.dumps(instant_raw, ensure_ascii=False) if instant_raw else None, completed_at=datetime.utcnow() if instant_raw else None)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    if promotion:
        await PromotionService.mark_used(session, promotion.id)
    await DynamicService.increment_product_sold(session, product.id, quantity)
    cashback = await CashbackService.apply_cashback(session, db_user.id, order.id, 'unified_orders', final_price)
    result_text = f'✅ <b>تم إرسال طلبك بنجاح!</b>\n\n🆔 رقم الطلب: #{order.id}\n📦 المنتج: {esc(product.name_ar)}\n💰 المبلغ: {final_price}$\n'
    if discount > 0:
        label = 'العرض' if promotion else 'الكوبون'
        result_text += f'🎁 {label}: -{discount}$\n'
    if cashback > 0:
        result_text += f'🎁 كاشباك: {cashback}$\n'
    if target:
        result_text += f'🎯 الهدف: <code>{esc(target)}</code>\n'
    if quantity > 1:
        result_text += f'📊 الكمية: {quantity}\n'
    result_text += f'\n📊 الحالة: {status_message}'
    if instant_raw:
        from services.digital_delivery import format_delivery_html

        delivery_html = format_delivery_html(instant_raw)
        if delivery_html:
            result_text += f'\n\n🎁 <b>تم التسليم فوراً — بياناتك:</b>{delivery_html}'
    else:
        if fulfillment == ProductFulfillmentType.MANUAL.value:
            result_text += (
                '\n\n🕐 <b>هذا منتج يدوي</b> — ستُنفذ الإدارة طلبك '
                'خارج البوت وستصلك رسالة فور اكتمال التنفيذ أو الاسترجاع.'
            )
        result_text += '\nستصلك إشعارات بتحديث حالة طلبك.'
    await callback.message.answer(result_text)
    if fulfillment == ProductFulfillmentType.MANUAL.value:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        await notifier.notify_admin(
            "🖐 <b>طلب يدوي جديد — بانتظار تنفيذك</b>\n\n"
            f"🆔 الطلب: <b>#{order.id}</b>\n"
            f"👤 المستخدم: {db_user.telegram_id} (@{db_user.username or '-'})\n"
            f"📦 المنتج: {esc(product.name_ar)}\n"
            f"🎯 الهدف: {esc(target) if target else '—'}\n"
            f"📊 الكمية: {quantity}\n"
            f"💰 المبلغ: {final_price}$\n\n"
            "✅ نفّذت الطلب؟ «نفذته» لإشعار المستخدم.\n"
            "❌ لا يمكنك تنفيذه؟ «ألغِه» لاسترجاع رصيد المستخدم.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ نفّذته — أُشعر المستخدم", callback_data=f"admin:order_complete:{order.id}", style="primary")],
                    [InlineKeyboardButton(text="❌ أَلْغِه — استرجاع", callback_data=f"admin:order_refund_ask:{order.id}", style="danger")],
                    [InlineKeyboardButton(text="👁 تفاصيل الطلب", callback_data=f"admin:order_view:{order.id}", style="primary")],
                ]
            ),
        )
    else:
        await notifier.notify_admin(f"🛒 <b>طلب شراء جديد</b>\n\n👤 المستخدم: {db_user.telegram_id} (@{db_user.username or '-'})\n📦 المنتج: {esc(product.name_ar)}\n💰 المبلغ: {final_price}$\n🎯 الهدف: {esc(target or '—')}\n📊 الكمية: {quantity}\n🆔 طلب #{order.id}", notification_type="order")
    from services.smm_catalog import button_label as _smm_label

    _sub = product.sub_category
    _cat = _sub.category if _sub is not None else None
    await notifier.notify_successful_unified_order(
        username=db_user.username,
        full_name=db_user.full_name,
        product_name=product.name_ar,
        price_usd=str(final_price),
        order_id=order.id,
        quantity=quantity,
        target=target,
        app_name=_smm_label(_cat.name_ar, _cat.emoji) if _cat is not None else None,
        section_name=_smm_label(_sub.name_ar, _sub.emoji) if _sub is not None else None,
        service_name=product.name_ar,
        user_telegram_id=db_user.telegram_id,
        is_smm=bool(product.requires_link or product.requires_quantity),
    )
    await state.clear()
