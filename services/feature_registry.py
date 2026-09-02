"""
السجل المرجعي لكل الإضافات في البوت.

كل إضافة جديدة تُعرَّف هنا مرة واحدة، فتحصل تلقائياً على:
- مفتاح تفعيل/إيقاف من لوحة الأدمن.
- إعدادات خاصة قابلة للتعديل من لوحة الأدمن.
- سجل استخدام يقيس هل هي مستعملة فعلاً أم ميتة.

لا يُضاف أي سطر كود لميزة جديدة في مكان آخر من البوت بدون تسجيلها هنا،
وإلا لن تظهر للأدمن ولن يستطيع التحكم بها.

الحقول:
- key: معرف فريد (يُستخدم في الكود وفي قاعدة البيانات).
- name_ar / name_en: الاسم الظاهر في اللوحة.
- category_ar: مجموعة العرض في اللوحة.
- desc_ar: شرح مختصر يفهمه الأدمن.
- default_enabled: هل تعمل فور التثبيت.
- defaults: الإعدادات الافتراضية القابلة للتعديل.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    name_ar: str
    name_en: str
    category_ar: str
    desc_ar: str
    default_enabled: bool = False
    defaults: dict = field(default_factory=dict)

    @property
    def emoji_category(self) -> str:
        return _CATEGORY_EMOJI.get(self.category_ar, "🧩")


_CATEGORY_EMOJI = {
    "الأساس": "⚙️",
    "الاقتصاد": "💰",
    "السوق": "🏪",
    "الأرقام": "📱",
    "الرشق": "📈",
    "الألعاب": "🎮",
    "التطبيقات": "📦",
    "الذكاء": "🤖",
    "الثقة": "🛡️",
    "النمو": "🚀",
    "التفاعل": "🎯",
    "القنوات": "🌐",
    "الإشعارات": "🔔",
}


def _spec(
    key: str,
    name_ar: str,
    name_en: str,
    category_ar: str,
    desc_ar: str,
    default_enabled: bool = False,
    **defaults,
) -> FeatureSpec:
    return FeatureSpec(
        key=key,
        name_ar=name_ar,
        name_en=name_en,
        category_ar=category_ar,
        desc_ar=desc_ar,
        default_enabled=default_enabled,
        defaults=dict(defaults),
    )


# ══════════════════════════════════════════════════════════════
#  السجل الكامل
# ══════════════════════════════════════════════════════════════

FEATURES: tuple[FeatureSpec, ...] = (
    # ─────────── الأساس ───────────
    _spec(
        "points_currency",
        "النقاط كعملة شراء",
        "Points as Currency",
        "الاقتصاد",
        "النقاط تُشترى بها الخدمات مباشرة، وكل عدد محدد من النقاط يساوي دولاراً واحداً.",
        True,
        points_per_usd=100,
        max_points_payment_percent=100,
    ),
    _spec(
        "transfer_fee",
        "عمولة التحويل بين المستخدمين",
        "Transfer Commission",
        "الاقتصاد",
        "نسبة تُقتطع عند تحويل الرصيد من مستخدم لآخر.",
        True,
        fee_percent=1,
        min_amount_usd=1,
        max_amount_usd=1000,
        require_min_account_age_hours=24,
    ),
    _spec(
        "tasks_system",
        "المهام مقابل نقاط",
        "Tasks for Points",
        "الاقتصاد",
        "مهام يحددها الأدمن بقيم نقاط يختارها، مع تحقق ذكي يمنع التكرار والغش.",
        True,
        daily_task_limit=10,
        min_account_age_hours=1,
        require_verification=True,
    ),
    _spec(
        "peer_marketplace",
        "سوق المستخدمين (Peer Marketplace)",
        "Peer Marketplace",
        "السوق",
        "المستخدمون يعرضون خدماتهم وحساباتهم، والأدمن يوافق ويحدد العمولة قبل النشر.",
        True,
        default_commission_percent=5,
        min_price_usd=1,
        max_price_usd=10000,
        listing_expire_days=30,
        max_active_listings_per_user=10,
        auto_release_hours=72,
        require_min_account_age_hours=24,
        require_min_balance_usd=5,
    ),
    _spec(
        "trusted_seller_auto_approve",
        "الموافقة التلقائية للبائعين الموثوقين",
        "Trusted Seller Auto Approval",
        "السوق",
        "ينشر عروض السوق تلقائياً للبائعين ذوي السجل الجيد ضمن حدود سعر آمنة.",
        True,
        min_successful_sales=5,
        min_success_rate=90,
        max_auto_price_usd=50,
    ),
    _spec(
        "escrow_engine",
        "محرك الضمان (Escrow)",
        "Escrow Engine",
        "السوق",
        "يحجز أموال المشتري حتى يؤكد الاستلام أو تنتهي المهلة، ثم يوزعها.",
        True,
        release_after_confirm=True,
        dispute_window_hours=24,
    ),
    _spec(
        "feature_usage_analytics",
        "قياس استخدام الإضافات",
        "Feature Usage Analytics",
        "الأساس",
        "يسجل استخدام كل إضافة ليعرف الأدمن أيها يستحق التطوير وأيها يجب إيقافه.",
        True,
        retention_days=90,
    ),

    # ─────────── الأرقام ───────────
    _spec(
        "warm_pool",
        "البركة المُسخَّنة للأرقام",
        "Warm Number Pool",
        "الأرقام",
        "شراء أرقام مسبقاً لأكثر الخدمات طلباً لتسليم فوري بدل الانتظار.",
        False,
        pool_size=20,
        top_services=10,
        top_countries=10,
        refill_threshold=5,
        max_idle_minutes=20,
    ),
    _spec(
        "instant_delivery",
        "التسليم اللحظي المتوازي",
        "Parallel Instant Delivery",
        "الأرقام",
        "فحص الطلبات بشكل متوازٍ بدل التسلسلي، مع webhook لمن يدعمه.",
        True,
        batch_size=25,
        poll_interval_seconds=5,
        enable_webhooks=True,
    ),
    _spec(
        "bulk_numbers",
        "شراء الأرقام بالجملة",
        "Bulk Number Purchase",
        "الأرقام",
        "شراء عدة أرقام بطلب واحد مع خصم تدريجي وتصدير النتائج.",
        True,
        max_quantity=500,
        concurrency=10,
        discount_tiers_json='[[10,1],[50,3],[100,5],[500,8]]',
    ),
    _spec(
        "number_exchange",
        "بورصة الأرقام وأوامر الحد",
        "Number Price Exchange",
        "الأرقام",
        "أسعار حية مع أوامر شراء تلقائية عند سعر محدد.",
        False,
        tick_retention_days=30,
        max_open_orders_per_user=20,
    ),
    _spec(
        "dedicated_numbers",
        "الأرقام المقيمة (اشتراك)",
        "Dedicated Numbers",
        "الأرقام",
        "استئجار رقم بشكل حصري لفترة مع تحويل كل الرسائل الواردة.",
        False,
        plans_json='[{"days":30,"price_usd":3.0},{"days":90,"price_usd":8.0},{"days":365,"price_usd":25.0}]',
        auto_renew=True,
    ),
    _spec(
        "sms_insurance",
        "تأمين الكود (استرجاع مضاعف)",
        "SMS Insurance",
        "الثقة",
        "صندوق تأمين يدفع استرجاعاً وتعويضاً فوراً عند فشل وصول الكود.",
        False,
        premium_percent=2,
        claim_window_seconds=60,
        double_refund_services_json="[]",
    ),
    _spec(
        "predictive_ban_risk",
        "المخاطر التنبؤية لحظر الرقم",
        "Predictive Ban Risk",
        "الأرقام",
        "يحسب احتمال فشل الرقم مع خدمة معينة من سجل النتائج ويعرضه قبل الشراء.",
        False,
        min_samples=20,
        warn_below_success_rate=70,
    ),
    _spec(
        "full_store_hub",
        "واجهة المتجر الشاملة",
        "Full Store Hub",
        "السوق",
        "واجهة اكتشاف موحدة لكل منتجات المتجر: ألعاب، SMM، تطبيقات، مخزون رقمي وعروض.",
        True,
        section_limit=8,
        cheap_max_usd=2.0,
    ),
    _spec(
        "ready_number_packages",
        "باقات الأرقام الجاهزة",
        "Ready Number Packages",
        "الأرقام",
        "باقات كمية جاهزة تظهر للمستخدم وتختصر اختيار الكمية للتجار والمبتدئين.",
        True,
        quantities_json='[5,10,25,50]',
        max_cards=8,
    ),
    _spec(
        "smart_number_routing",
        "توجيه مزودي الأرقام الذكي",
        "Smart Number Provider Routing",
        "الأرقام",
        "لا يختار الأرخص فقط؛ يرتب مزودي الأرقام حسب السعر ونسبة النجاح وسرعة وصول الكود.",
        True,
        days=14,
        min_samples=5,
        price_weight=65,
        quality_weight=35,
        min_success_rate=55,
    ),
    _spec(
        "number_portability",
        "قابلية نقل الرقم",
        "Number Portability",
        "الأرقام",
        "الرقم نفسه يُعاد تأجيره لنفس المستخدم لاحقاً دون فقدانه.",
        False,
        reserve_hours=72,
    ),
    _spec(
        "rare_number_drops",
        "إسقاطات الأرقام النادرة",
        "Rare Number Drops",
        "التفاعل",
        "أرقام بنمط مميز تظهر بكميات محدودة مع تنبيه لحظي وتسابق على الشراء.",
        False,
        drop_interval_hours=6,
        quantity_per_drop=5,
        pattern_rules_json='["(.)\\1{3,}","(\\d)(\\d)\\1\\2"]',
    ),
    _spec(
        "vip_number_certificates",
        "شهادات ملكية أرقام VIP",
        "VIP Number Certificates",
        "التفاعل",
        "ملكية دائمة موثقة لرقم نادر قابلة للتحويل بين المستخدمين.",
        False,
        certificate_fee_usd=10,
    ),
    _spec(
        "free_trial",
        "التجربة المجانية الأولى",
        "Zero-Risk Trial",
        "النمو",
        "أول رقم للمستخدم الجديد مجاناً مع حماية من إساءة الاستخدام.",
        False,
        max_value_usd=0.5,
        require_account_age_minutes=0,
        require_phone_verification=False,
    ),

    # ─────────── الرشق ───────────
    _spec(
        "refill_guarantee",
        "ضمان التعويض الآلي",
        "Auto Refill Guarantee",
        "الرشق",
        "يفحص الطلب بعد اكتماله ويعيد تعويض النقص تلقائياً خلال فترة الضمان.",
        True,
        guarantee_days=30,
        recheck_interval_hours=12,
        min_drop_to_refill=1,
    ),
    _spec(
        "catalog_failover",
        "Failover لمزودي الكتالوج",
        "Catalog Provider Failover",
        "الرشق",
        "المنتج يُوجَّه لأكثر من مزود، فإذا فشل الأول ينتقل للتالي تلقائياً.",
        True,
        respect_priority=True,
        respect_price=True,
    ),
    _spec(
        "drip_feed",
        "التدريج المجدول",
        "Drip-Feed Scheduler",
        "الرشق",
        "تقسيم الطلب على دفعات مجدولة بدل تنفيذه دفعة واحدة.",
        False,
        max_runs=50,
        min_interval_minutes=30,
    ),
    _spec(
        "live_progress",
        "لوحة التقدّم الحية",
        "Live Order Progress",
        "الرشق",
        "شريط تقدّم وETA وكشف هبوط تلقائي للطلبات المنفذة.",
        True,
        update_interval_seconds=60,
        notify_on_drop=True,
    ),
    _spec(
        "campaign_builder",
        "منشئ الحملات",
        "Campaign Builder",
        "الرشق",
        "خطة كاملة من عدة خدمات بميزانية وجدول زمني واحد.",
        False,
        max_services_per_campaign=10,
    ),
    _spec(
        "smart_mix",
        "مُحسِّن المزيج الذكي",
        "Smart Mix Optimizer",
        "الرشق",
        "يبني أرخص/أفضل مزيج خدمات لميزانية محددة من كل المزودين.",
        False,
        options_count=3,
    ),
    _spec(
        "smm_quality_score",
        "تقييم جودة خدمات الرشق",
        "SMM Quality Score",
        "الرشق",
        "وسوم جودة محسوبة من نتائج الطلبات الحقيقية (هبوط/إكمال/سرعة).",
        False,
        min_orders_to_rate=10,
    ),
    _spec(
        "child_panels",
        "لوحات الريسلر الفرعية",
        "Hosted Child Panels",
        "النمو",
        "أي مستخدم يصير صاحب لوحة بهامشه الخاص ورابطه الخاص.",
        False,
        default_markup_percent=10,
        min_deposit_to_open_usd=10,
    ),

    # ─────────── الألعاب ───────────
    _spec(
        "player_id_validation",
        "التحقق من آيدي اللاعب",
        "Player ID Validation",
        "الألعاب",
        "يتحقق من صيغة الآيدي ويعرض اسم اللاعب قبل الخصم لمنع النزاعات.",
        True,
        strict_format=True,
        show_player_name=True,
    ),
    _spec(
        "account_wallet",
        "محفظة حسابات الألعاب",
        "Game Account Wallet",
        "الألعاب",
        "حفظ آيديات المستخدم والشحن بضغطة واحدة مع جدولة.",
        False,
        max_saved_accounts=10,
    ),
    _spec(
        "game_price_tracker",
        "متتبّع أسعار عملات الألعاب",
        "Game Currency Tracker",
        "الألعاب",
        "مقارنة حية للأسعار بين المزودين وتوجيه للأرخص.",
        False,
        update_interval_minutes=15,
    ),
    _spec(
        "tournaments",
        "البطولات والجوائز",
        "Tournaments",
        "التفاعل",
        "بطولات باشتراك من الرصيد وجوائز تُدفع رصيداً أو أكواداً.",
        False,
        entry_fee_usd=1,
        prize_pool_percent=80,
    ),
    _spec(
        "p2p_code_market",
        "سوق الأكواد بين المستخدمين",
        "P2P Code Market",
        "السوق",
        "المستخدمون يبيعون أكوادهم لبعض مع ضمان الوسيط.",
        False,
        commission_percent=4,
    ),

    # ─────────── التطبيقات ───────────
    _spec(
        "subscription_lifecycle",
        "مدير دورة حياة الاشتراك",
        "Subscription Lifecycle",
        "التطبيقات",
        "يتتبع انتهاء الاشتراكات وينبه ويجدد تلقائياً.",
        False,
        warn_days_before=3,
        auto_renew=True,
    ),
    _spec(
        "shared_plans",
        "الاشتراك العائلي المقسّم",
        "Shared Plan Splitting",
        "التطبيقات",
        "خطة متعددة المقاعد بتقسيم شهري وملء تلقائي للمقاعد الشاغرة.",
        False,
        default_seats=6,
        waitlist_enabled=True,
    ),
    _spec(
        "key_swap_guarantee",
        "ضمان المفتاح مع تبديل فوري",
        "Instant Key Swap",
        "التطبيقات",
        "تبديل تلقائي للمفتاح الفاسد من المخزون بلا تدخل أدمن.",
        True,
        max_swaps_per_order=2,
    ),

    # ─────────── الذكاء ───────────
    _spec(
        "ai_agent_layer",
        "طبقة وكلاء الذكاء الاصطناعي",
        "AI Agent Layer",
        "الذكاء",
        "وكلاء للمحادثة والتسعير والدعم بدل المنطق القائم على الكلمات المفتاحية.",
        False,
        provider="none",
        daily_spend_cap_usd=5,
        read_only_tools=True,
        require_confirm_before_spend=True,
    ),
    _spec(
        "autonomous_purchase_agent",
        "وكيل الشراء المستقل",
        "Autonomous Purchase Agent",
        "الذكاء",
        "يجدول وينفذ عمليات شراء متكررة بميزانية وحد سعري يحدده المستخدم.",
        False,
        max_budget_usd=500,
        max_price_multiplier=1.5,
    ),
    _spec(
        "ai_merchandiser",
        "الرفّ الذكي",
        "AI Merchandiser",
        "الذكاء",
        "ترتيب شخصي للمنتجات حسب سلوك كل مستخدم بدل الترتيب اليدوي.",
        False,
        personalization_weight=70,
    ),
    _spec(
        "behavioral_fingerprint",
        "البصمة السلوكية لمكافحة الاحتيال",
        "Behavioral Fraud Detection",
        "الثقة",
        "يحلل نمط التوقيت والسرعة لاكتشاف الحسابات المؤتمتة وعصابات إعادة البيع.",
        False,
        block_threshold=80,
        review_threshold=50,
    ),
    _spec(
        "smart_verification",
        "نظام التحقق الذكي من المستخدمين",
        "Smart User Verification",
        "الثقة",
        "تحقق متدرج يفتح ميزات حساسة (سوق/تحويل/ائتمان) بعد إثبات الهوية.",
        True,
        tier0_max_usd=10,
        tier1_max_usd=100,
        tier2_max_usd=100000,
        require_captcha_for_marketplace=True,
    ),
    _spec(
        "local_personas",
        "شخصيات محلية لكل سوق",
        "Local Market Personas",
        "التفاعل",
        "أسلوب مخاطبة مختلف لكل سوق بدل ترجمة آلية واحدة.",
        False,
    ),

    # ─────────── الثقة ───────────
    _spec(
        "public_trust_api",
        "واجهة الثقة العامة",
        "Public Trust API",
        "الثقة",
        "واجهة عامة للتحقق من موثوقية رقم أو مزود دون كشف بيانات.",
        False,
        rate_limit_per_ip=60,
        expose_provider_stats=True,
    ),
    _spec(
        "compliance_engine",
        "محرك الامتثال",
        "Compliance Engine",
        "الثقة",
        "مستويات KYC وقواعد لكل دولة تحدد ما يُسمح بيعه.",
        False,
    ),
    _spec(
        "number_lineage",
        "شجرة نسب الرقم",
        "Number Lineage",
        "الثقة",
        "سجل غير قابل للتلاعب لكل رقم يمنع بيع رقم سبق استخدامه.",
        False,
        block_recycled=True,
    ),

    # ─────────── النمو ───────────
    _spec(
        "revenue_sharing_tokens",
        "أسهم حصة الإحالة",
        "Revenue Sharing Tokens",
        "النمو",
        "المستخدم يبيع جزءاً من عمولته المستقبلية مقابل مبلغ فوري.",
        False,
        max_share_percent=50,
        min_price_usd=1,
    ),
    _spec(
        "task_to_credit",
        "المهام مقابل رصيد",
        "Task-to-Credit Economy",
        "النمو",
        "كسب رصيد عبر مهام مفيدة للبوت (تقييم/ترجمة/إبلاغ).",
        False,
    ),
    _spec(
        "public_leaderboard",
        "لوحة الصدارة العامة",
        "Public Leaderboard",
        "التفاعل",
        "ترتيب شهري عام مع وصول مبكر حصري للفائزين.",
        False,
        early_access_hours=24,
    ),
    _spec(
        "pooled_rooms",
        "غرف الشراء الجماعي",
        "Pooled Purchase Rooms",
        "النمو",
        "مستخدمون يتجمعون على طلب كبير لفتح خصم الجملة.",
        False,
        min_members=5,
    ),
    _spec(
        "affiliate_tiers",
        "الإحالة متعددة المستويات",
        "Tiered Affiliate",
        "النمو",
        "شجرة إحالات بمستويات وعمولات مختلفة بدل مستوى واحد.",
        False,
        levels=3,
        level_percents_json="[5,2,1]",
    ),
    _spec(
        "growth_optimizer",
        "محرك النمو الذاتي",
        "Growth Optimizer",
        "النمو",
        "اختبارات A/B دائمة على الأسعار والعروض مع فوز تلقائي.",
        False,
    ),
    _spec(
        "public_storefront",
        "المتجر العام وSEO",
        "Public Storefront",
        "النمو",
        "صفحات عامة مفهرسة لكل منتج مع سعر حي وزر شراء في تليجرام.",
        False,
    ),

    # ─────────── القنوات ───────────
    _spec(
        "omnichannel",
        "التواجد متعدد القنوات",
        "Omnichannel Presence",
        "القنوات",
        "نفس المحرك عبر واتساب وديسكورد وإضافة متصفح.",
        False,
    ),
    _spec(
        "voice_ordering",
        "الطلب الصوتي",
        "Voice Ordering",
        "القنوات",
        "طلب بالصوت ورد صوتي بالحالة.",
        False,
    ),
    _spec(
        "market_intelligence",
        "تقارير ذكاء السوق",
        "Market Intelligence",
        "القنوات",
        "تقارير مجمعة مجهولة الهوية عن اتجاهات الطلب.",
        False,
        anonymize=True,
    ),
    _spec(
        "white_label_factory",
        "مصنع العلامات البيضاء",
        "White-Label Factory",
        "القنوات",
        "تشغيل عدة بوتات بعلامات مختلفة على نفس الكود.",
        False,
    ),
    _spec(
        "provider_bidding",
        "محرك مزايدة المزودين",
        "Provider Bidding Engine",
        "القنوات",
        "مزودون يسجلون ويعرضون أسعارهم والنظام يختار الأنسب لحظياً.",
        False,
    ),
    _spec(
        "self_hosted_sim",
        "شبكة SIM ذاتية",
        "Self-Hosted SIM Gateway",
        "القنوات",
        "ربط بوابة GSM فعلية كمصدر أرقام خاص.",
        False,
    ),

    # ─────────── الأساس التقني ───────────
    _spec(
        "redis_cache",
        "كاش Redis المشترك",
        "Shared Redis Cache",
        "الأساس",
        "نقل كاش الأسعار من ذاكرة العملية إلى Redis المشترك.",
        True,
        price_ttl_seconds=20,
    ),
    _spec(
        "live_fx_feed",
        "أسعار الصرف الحية",
        "Live FX Feed",
        "الأساس",
        "تحديث أسعار الصرف من مصدر خارجي بدل الإدخال اليدوي.",
        False,
        source="exchangerate_host",
        refresh_minutes=60,
        max_deviation_percent=20,
    ),
    _spec(
        "self_healing_ops",
        "غرفة العمليات التنبؤية",
        "Self-Healing Ops",
        "الأساس",
        "كشف تدهور المزود قبل فشله وتخفيض وزنه تلقائياً.",
        False,
        zscore_threshold=2.5,
        min_weight=10,
    ),
    _spec(
        "catalog_autopilot",
        "الإدارة الذاتية للكتالوج",
        "Catalog Autopilot",
        "الأساس",
        "مزامنة أسعار وضبط هوامش وتعطيل تلقائي للمنتجات المعطوبة.",
        False,
        target_margin_percent=50,
        auto_disable_deleted=True,
    ),
    _spec(
        "cross_category_cart",
        "السلة العابرة للأقسام",
        "Cross-Category Cart",
        "السوق",
        "دفع رقم وUC ومتابعين واشتراك في عملية واحدة مع حزم.",
        False,
    ),
    # ─────────── الإضافات الثلاث الهدية (على مستوى البوت كامل) ───────────
    _spec(
        "bot_cockpit",
        "مركز القيادة الحي",
        "Live Bot Cockpit",
        "الأساس",
        "شاشة واحدة تجمع صحة كل نظام في البوت: الأموال، الطلبات، المزودون، "
        "الميزات، وما يحتاج قراراً.",
        True,
    ),
    _spec(
        "unified_refund",
        "محرك الاسترجاع الموحّد",
        "Unified Refund Engine",
        "الأساس",
        "سلطة استرجاع واحدة لكل المسارات بسجل موحّد، فلا يُسترجع شيء مرتين "
        "ويصير ممكناً معرفة كم استُرجع ولماذا.",
        True,
    ),
    _spec(
        "self_heal_sentinel",
        "الحارس الذاتي (وضع آمن)",
        "Self-Heal Sentinel",
        "الأساس",
        "يراقب معدل الأخطاء ويُدخل البوت وضعاً آمناً يعطّل الميزات الخطرة "
        "تلقائياً قبل أن تتفاقم الخسارة.",
        True,
        error_threshold=20,
        window_minutes=10,
    ),
    _spec(
        "developer_platform",
        "منصة المطورين",
        "Developer Platform",
        "القنوات",
        "Webhooks موقّعة وSDK وبيئة اختبار رملية.",
        False,
    ),
    # ─────────── الرشق: الأقسام الداخلية والبناء التلقائي ───────────
    _spec(
        "smm_inner_sections",
        "الأقسام الداخلية في قسم الرشق",
        "SMM Inner Sections",
        "الرشق",
        "داخل كل تطبيق (إنستغرام/تيك توك...) أقسام داخلية حسب النوع: "
        "متابعون، لايكات، مشاهدات، تعليقات... ويُعرض المنتج داخل قسمه.",
        True,
    ),
    _spec(
        "smm_auto_sections",
        "البناء التلقائي لأقسام الرشق",
        "SMM Auto Sections Builder",
        "الرشق",
        "عند الإقلاع أو من زر اللوحة: ينشئ تلقائياً لكل تطبيق أقسامه الداخلية "
        "حسب الخدمات المسحوبة وينشر أرخص 5 خدمات في كل نوع بسعر تكلفة المزود "
        "+ هامش الربح، دون تكرار أو مساس بالمنتجات اليدوية.",
        True,
        max_per_section=5,
        margin_percent=50,
        auto_on_startup=True,
    ),
    # ─────────── الثقة: حماية الإحالة من البوتات ───────────
    _spec(
        "referral_bot_guard",
        "حماية الإحالة من البوتات",
        "Referral Bot Guard",
        "الثقة",
        "من يدخل عبر رابط إحالة يمر باختبار بشري قبل التفعيل ومكافأة المحيل؛ "
        "وعند تكرار الفشل يُعتبر روبوتاً فيُحظر هو ويُعاقب صاحب رابط الإحالة.",
        True,
        max_fails=3,
        ban_referrer_on_bot=True,
        ban_joiner_on_bot=True,
    ),
    # ─────────── الإشعارات: مباشر البوت ───────────
    _spec(
        "live_bot_feed",
        "📡 مباشر البوت (أحداث الشراء والاسترجاع)",
        "Bot Live Feed",
        "الإشعارات",
        "إشعارات فورية للوحة الأدمن عند كل عملية شراء تنجح وتُفعَّل (خصم "
        "الرصيد) وعند كل استرجاع/فشل (رجوع الرصيد للمستخدم) — ليعرف صاحب "
        "البوت مباشرة ماذا يحدث دون انتظار شكوى من المستخدم.",
        True,
        notify_success=True,
        notify_refund=True,
    ),
)


BY_KEY: dict[str, FeatureSpec] = {spec.key: spec for spec in FEATURES}


def get_spec(key: str) -> FeatureSpec | None:
    return BY_KEY.get(key)


def all_keys() -> list[str]:
    return [spec.key for spec in FEATURES]


def by_category() -> dict[str, list[FeatureSpec]]:
    grouped: dict[str, list[FeatureSpec]] = {}
    for spec in FEATURES:
        grouped.setdefault(spec.category_ar, []).append(spec)
    return grouped


def categories_ordered() -> list[str]:
    order = list(_CATEGORY_EMOJI.keys())
    present = [c for c in order if c in by_category()]
    present += [c for c in by_category() if c not in order]
    return present
