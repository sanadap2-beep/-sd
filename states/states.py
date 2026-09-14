"""
كل حالات FSM الخاصة بالبوت.
"""

from aiogram.fsm.state import State, StatesGroup


# ══════════════ المستخدم ══════════════


class DepositStates(StatesGroup):
    waiting_amount = State()
    waiting_proof_photo = State()
    waiting_tx_number = State()


class ProductSearchStates(StatesGroup):
    waiting_query = State()


class GiftRedeemStates(StatesGroup):
    waiting_code = State()


class AssistantStates(StatesGroup):
    waiting_request = State()


class ReviewStates(StatesGroup):
    waiting_rating = State()
    waiting_comment = State()


class AdminGiftStates(StatesGroup):
    waiting_amount = State()
    waiting_expires_days = State()
    waiting_max_uses = State()


class ProductRequestStates(StatesGroup):
    waiting_request = State()


class AdminProductRequestStates(StatesGroup):
    waiting_note = State()


class SupportTicketStates(StatesGroup):
    waiting_message = State()


class AiSupportStates(StatesGroup):
    waiting_question = State()


class AiSectionStates(StatesGroup):
    """المستخدم داخل قسم ذكاء اصطناعي يرد على طلباته (section_id في state data)."""

    waiting_prompt = State()


class AdminAiSectionStates(StatesGroup):
    waiting_key = State()
    waiting_name_ar = State()
    waiting_name_en = State()
    waiting_kind = State()
    waiting_model = State()
    waiting_cost = State()
    waiting_multiplier = State()
    waiting_desc_ar = State()
    waiting_desc_en = State()


class AdminAiProviderStates(StatesGroup):
    waiting_base_url = State()
    waiting_api_key = State()


class WaStates(StatesGroup):
    waiting_phone = State()
    # نص يكتبه المستخدم ردّاً على زر kind="input" عند البوت الثاني.
    waiting_action_input = State()


class AdminWaStates(StatesGroup):
    waiting_description = State()
    waiting_price = State()
    waiting_packages = State()
    waiting_bridge_url = State()
    waiting_bridge_secret = State()


class NumberBulkStates(StatesGroup):
    waiting_quantity = State()


class AdminTicketStates(StatesGroup):
    waiting_reply = State()


class AdminInventoryStates(StatesGroup):
    waiting_value = State()


class AdminPromotionStates(StatesGroup):
    waiting_product_id = State()
    waiting_name = State()
    waiting_discount_value = State()
    waiting_duration_hours = State()
    waiting_max_uses = State()


# ══════════════ الشحن اليدوي - شام كاش ══════════════


class ShamCashManualStates(StatesGroup):
    waiting_amount = State()
    waiting_proof_photo = State()
    waiting_tx_number = State()


# ══════════════ الشحن اليدوي - USDT ══════════════


class UsdtManualStates(StatesGroup):
    waiting_network = State()
    waiting_amount = State()
    waiting_proof_photo = State()
    waiting_tx_hash = State()


# ══════════════ الشحن التلقائي - شام كاش ══════════════


class ShamCashAutoStates(StatesGroup):
    waiting_currency = State()
    waiting_amount = State()
    waiting_transaction_ref = State()


# ══════════════ الشحن التلقائي - USDT ══════════════


class UsdtAutoStates(StatesGroup):
    waiting_amount = State()


# ══════════════ التحويل بين المستخدمين ══════════════


class TransferStates(StatesGroup):
    waiting_recipient_id = State()
    waiting_amount = State()


class SMMOrderStates(StatesGroup):
    waiting_link = State()
    waiting_quantity = State()
    waiting_coupon = State()


class GamesOrderStates(StatesGroup):
    waiting_player_id = State()
    waiting_coupon = State()


class AppsOrderStates(StatesGroup):
    waiting_player_id = State()
    waiting_coupon = State()


class ReferralGuardStates(StatesGroup):
    """حالة التحقق البشري لمن دخل عبر رابط إحالة."""
    waiting_answer = State()


# ══════════════ الأدمن - عام ══════════════


class AdminBroadcastStates(StatesGroup):
    waiting_content = State()


class AdminChannelStates(StatesGroup):
    waiting_channel_id = State()


class AdminUserSearchStates(StatesGroup):
    waiting_user_id = State()
    waiting_balance_amount = State()


class AdminPricingStates(StatesGroup):
    waiting_exchange_rate = State()
    waiting_margin_value = State()


class AdminSupportStates(StatesGroup):
    waiting_support_username = State()


class AdminPaymentStates(StatesGroup):
    waiting_payment_text = State()


class AdminLargeTxStates(StatesGroup):
    waiting_threshold = State()


class AdminOrderTimeoutStates(StatesGroup):
    waiting_minutes = State()


class AdminCountryStates(StatesGroup):
    waiting_code = State()
    waiting_name_ar = State()
    waiting_flag = State()
    waiting_fivesim_code = State()
    waiting_herosms_code = State()
    waiting_sms_activate_code = State()
    waiting_smshub_code = State()


# ══════════════ الأدمن - الأقسام الرئيسية ══════════════


class AdminCategoryStates(StatesGroup):
    waiting_name = State()
    waiting_emoji = State()
    waiting_type = State()
    waiting_sort_order = State()
    waiting_edit_field = State()
    waiting_edit_value = State()


# ══════════════ الأدمن - الأقسام الفرعية ══════════════


class AdminSubCategoryStates(StatesGroup):
    waiting_name = State()
    waiting_emoji = State()
    waiting_description = State()
    waiting_image = State()
    waiting_sort_order = State()
    waiting_edit_field = State()
    waiting_edit_value = State()


# ══════════════ الأدمن - المنتجات (Wizard) ══════════════


class AdminProductWizardStates(StatesGroup):
    """states للمعالج التفاعلي لإنشاء منتج جديد."""

    selecting_creation_mode = State()
    selecting_provider = State()
    selecting_category = State()
    browsing_services = State()
    searching_services = State()
    waiting_search_query = State()
    viewing_service = State()
    confirming_service = State()
    waiting_name = State()
    waiting_description = State()
    waiting_image_choice = State()
    waiting_image = State()
    selecting_pricing_type = State()
    waiting_fixed_price = State()
    waiting_margin_percent = State()
    selecting_display_type = State()
    waiting_min_quantity = State()
    waiting_max_quantity = State()
    confirming_creation = State()


# ══════════════ الأدمن - المنتجات (تعديل) ══════════════


class AdminProductStates(StatesGroup):
    waiting_name = State()
    waiting_description = State()
    waiting_image = State()
    waiting_price = State()
    waiting_cost_price = State()
    waiting_provider_service_id = State()
    waiting_min_quantity = State()
    waiting_max_quantity = State()
    waiting_estimated_time = State()
    waiting_sort_order = State()
    waiting_pricing_type = State()
    waiting_profit_margin = State()
    waiting_search_query = State()
    waiting_edit_field = State()
    waiting_edit_value = State()
    confirming_price_change = State()
    confirming_delete = State()


# ══════════════ الأدمن - المزودين ══════════════


class AdminPulledServicesStates(StatesGroup):
    waiting_sell_price = State()
    waiting_search = State()


class AdminPartnerPickStates(StatesGroup):
    waiting_sell_price = State()
    waiting_margin = State()


class AdminSubManualStates(StatesGroup):
    """التسليم اليدوي لاشتراك رقمي بعد نفاد رصيد المزود."""

    waiting_data = State()


class AdminApiProviderStates(StatesGroup):
    waiting_protocol_type = State()
    waiting_name = State()
    waiting_type = State()
    waiting_api_url = State()
    waiting_api_key = State()
    waiting_currency = State()
    waiting_rate_to_usd = State()
    waiting_priority = State()
    waiting_low_balance_threshold = State()
    waiting_custom_config = State()
    waiting_custom_wizard_value = State()
    waiting_edit_field = State()
    waiting_edit_value = State()
    confirming_sync = State()


class AdminProviderServicesStates(StatesGroup):
    """states للتعامل مع خدمات مزود محدد."""

    browsing_services = State()
    searching_services = State()
    waiting_search_query = State()
    viewing_service = State()
    selecting_for_product = State()


class AdminCouponStates(StatesGroup):
    waiting_code = State()
    waiting_discount_type = State()
    waiting_discount_value = State()
    waiting_max_uses = State()
    waiting_min_order = State()
    waiting_expires_days = State()


class AdminStarsStates(StatesGroup):
    waiting_stars_amount = State()
    waiting_usd_amount = State()
    waiting_label = State()
    waiting_edit_field = State()
    waiting_edit_value = State()


class AdminNumberServiceStates(StatesGroup):
    waiting_code = State()
    waiting_name = State()
    waiting_emoji = State()
    waiting_fivesim_code = State()
    waiting_herosms_code = State()
    waiting_sms_activate_code = State()
    waiting_smshub_code = State()
    waiting_edit_field = State()
    waiting_edit_value = State()
    waiting_availability_channel = State()
    waiting_availability_topn = State()
    waiting_availability_watchlist = State()
    waiting_availability_repost_every = State()
    # سيرفرات/مزودين الخدمة
    waiting_server_name = State()
    waiting_server_emoji = State()
    waiting_server_provider = State()
    waiting_server_edit_value = State()


class AdminStoreServerStates(StatesGroup):
    """معالج إضافة/تعديل السيرفرات العامة لكل الأقسام."""
    waiting_name = State()
    waiting_emoji = State()
    waiting_provider_kind = State()
    waiting_provider = State()
    waiting_margin = State()
    waiting_edit_value = State()


class AdminMaintenanceStates(StatesGroup):
    waiting_message = State()


class AdminWelcomeStates(StatesGroup):
    waiting_message = State()


class AdminSettingsStates(StatesGroup):
    waiting_value = State()


class AdminMultiAdminStates(StatesGroup):
    waiting_admin_id = State()


class AdminSendMessageStates(StatesGroup):
    waiting_user_id = State()
    waiting_message = State()


class AdminImportExportStates(StatesGroup):
    """states لاستيراد وتصدير البيانات."""

    waiting_import_file = State()
    confirming_import = State()


# ══════════════ مركز التحكم بالإضافات ══════════════


class AdminFeatureStates(StatesGroup):
    waiting_search = State()
    waiting_option_value = State()


class AdminBulkDiscountStates(StatesGroup):
    waiting_tiers = State()


class AdminMainButtonStates(StatesGroup):
    waiting_label = State()
    waiting_action = State()
    choosing_target_type = State()
    choosing_category = State()
    choosing_subcategory = State()
    choosing_product = State()


class AdminStoreSectionStates(StatesGroup):
    """إضافة قسم/زر جديد داخل المتجر من «🛍 التحكم بالمتجر»."""

    waiting_label = State()
    waiting_action = State()
    choosing_target_type = State()
    choosing_category = State()
    choosing_subcategory = State()
    choosing_product = State()


class AdminTaskStates(StatesGroup):
    waiting_title = State()
    waiting_type = State()
    waiting_points = State()
    waiting_limit = State()
    waiting_target = State()


class AdminMarketStates(StatesGroup):
    waiting_commission = State()
    waiting_search = State()
    waiting_dispute_note = State()


class AdminPointsStates(StatesGroup):
    waiting_rate = State()
    waiting_cap = State()


class SpecialOfferOrderStates(StatesGroup):
    waiting_target = State()


class AdminSpecialOfferStates(StatesGroup):
    waiting_provider_id = State()
    waiting_service_id = State()
    waiting_name = State()
    waiting_description = State()
    waiting_price = State()
    waiting_eta = State()
    waiting_input_label = State()


class SponsoredAdStates(StatesGroup):
    waiting_title = State()
    waiting_body = State()
    waiting_item_type = State()
    waiting_price = State()
    waiting_contact = State()
    waiting_photos = State()
    waiting_confirm = State()


class MarketProfileStates(StatesGroup):
    waiting_alias = State()
    waiting_password = State()


class WithdrawStates(StatesGroup):
    waiting_method = State()
    waiting_currency = State()
    waiting_amount = State()
    waiting_network = State()
    waiting_address = State()


class MarketCreateStates(StatesGroup):
    waiting_kind = State()
    waiting_title = State()
    waiting_description = State()
    waiting_price = State()
    waiting_photos = State()
    waiting_secret = State()
    waiting_proof = State()
    waiting_confirm = State()


class MarketBuyStates(StatesGroup):
    waiting_confirm = State()


class TaskUserStates(StatesGroup):
    waiting_content = State()
    waiting_captcha = State()


class ExtrasStates(StatesGroup):
    waiting_limit_order = State()
    waiting_vip_number = State()
    waiting_room = State()
    waiting_revshare = State()
    waiting_ai_query = State()


class AdminOpsStates(StatesGroup):
    waiting_jurisdiction = State()
    waiting_tenant = State()


class AdminNotificationStates(StatesGroup):
    waiting_template_title = State()
    waiting_template_body = State()


class AdminSponsoredAdStates(StatesGroup):
    waiting_body = State()


class AdminRefundStates(StatesGroup):
    waiting_number_refund = State()
    waiting_unified_refund = State()
    waiting_sentinel_config = State()


class AgentStates(StatesGroup):
    """برنامج الوكلاء: انتظار كود الوكيل."""

    waiting_code = State()


class AdminAgentStates(StatesGroup):
    """إدارة الوكلاء: انتظار نسبة خصم كود جديد."""

    waiting_code_percent = State()


class AdminMarginStates(StatesGroup):
    """ضبط هوامش الربح: قسم / قسم فرعي / منتج."""

    waiting_category_margin = State()
    waiting_sub_margin = State()
    waiting_product_margin = State()


class AdminSmmProductsStates(StatesGroup):
    """منتجات قسم الرشق: انتظار نسبة الربح للقسم/التطبيق المحدد."""

    waiting_margin = State()
