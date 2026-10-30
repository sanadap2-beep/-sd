# مسح سوق موردي شحن الألعاب — 50 مصدراً مرشحاً

**تاريخ الرصد:** 30-10-2026
**الغرض:** اختيار مورد/موردين لشحن الألعاب في بوت Telegram قبل تمويل رصيد فعلي أو عرض المنتجات للعملاء.
**النطاق:** PUBG Mobile وFree Fire وMobile Legends أولاً، مع تفضيل الشحن المباشر بالـPlayer ID وZone/Server وواجهة API قابلة للأتمتة.

> **النتيجة المختصرة:** لا يوجد دليل كافٍ لوصف FazerCards بأنه «الأرخص مطلقاً». لكنه، وفق الأسعار الحية الظاهرة في لوحة الحساب التي أرسلها صاحب المشروع، منافس جداً: أرخص من السعر العلني لمتجر SHOP2TOPUP في Free Fire MENA بنحو 4% لنفس إجمالي الماس، وأرخص قليلاً من WDGZone في 60 UC و8100 UC. في المقابل، WDGZone أرخص قليلاً في PUBG First Purchase وPrime شهر.
> **لذلك:** لا تموّل FazerCards بكميات كبيرة بعد. أبقِ التكامل الحالي جاهزاً، وقارن أولاً الكتالوج/السعر المقروء فقط لدى **WDGZone وSHOP2TOPUP وMelostore وSEAGM**. لا يلزم شراء أو طلب مفتاح API من العميل لإجراء هذه المرحلة.

---

## 1) قواعد المقارنة العادلة

لا يجوز القول إن مورداً أرخص إلا عند تطابق العناصر التالية:

1. اللعبة و**القناة/المنطقة** (مثلاً Free Fire MENA وليس Global أو Indonesia).
2. نفس الـSKU وإجمالي العملة، مع توضيح البونص (`100+10` يساوي 110 فقط إن كانت المنطقة والقناة متماثلتين).
3. طريقة التسليم نفسها: Direct ID top-up مقابل PIN/voucher.
4. حقول العميل المطلوبة وقدرة المورد على التحقق منها (Player ID، Zone/Server، nickname إن لزم).
5. سعر الشراء الحقيقي في حساب المورد، وليس سعر صفحة تسويقية أو سعر تجزئة.
6. **التكلفة الواصلة** لا سعر SKU فقط:

   ```text
   تكلفة الطلب الواصلة = سعر SKU + (اشتراك المورد الشهري ÷ عدد الطلبات الشهرية)
                         + رسوم إيداع/شبكة/تحويل + فروق العملة + كلفة الطلبات الفاشلة
   ```

أسعار هذا الملف لقطة زمنية فقط؛ تتغير المخزونات والأسعار والقيود الإقليمية باستمرار.

---

## 2) لقطة أسعار أولية قابلة للمقارنة

### PUBG Mobile — أسعار بالدولار الأمريكي

| الباقة | FazerCards: لوحة الحساب المرسلة | WDGZone: كتالوج جملة علني | SHOP2TOPUP: واجهة تجزئة علنية | S7SH: تجزئة سورية | القراءة الصحيحة |
|---|---:|---:|---:|---:|---|
| Weekly Deal Pack 1 | **$0.861** | $0.862 | $0.90 | — | Fazer أقل من WDG بـ$0.001؛ لكن يجب تأكيد القناة/المنطقة قبل الاعتماد. |
| 60 UC | **$0.880** | $0.887 | $0.97 | $1.00 | Fazer أقل من WDG بـ$0.007، وأقل من السعر السوري المرصود بـ$0.12 قبل كل التكاليف. |
| First Purchase Pack | $0.880 | **$0.862** | $0.90 | — | WDG أقل بـ$0.018 (نحو 2%) إذا كانا نفس قناة الشحن. |
| Prime (1 Month) | $0.880 | **$0.862** | $0.90 | — | WDG أقل بـ$0.018 (نحو 2%) إذا كانا نفس قناة الشحن. |
| 8100 UC | **$87.870** | $90.399 | — | — | Fazer أقل بـ$2.529 (نحو 2.8%) إذا تطابقت المنطقة/القناة. |

**مصادر الرصد:**

- FazerCards: صور لوحة المورد التي أرسلها صاحب المشروع (السعر الفعلي للحساب، وليس سعر صفحة عامة).
- WDGZone: [PUBG Mobile Wholesale Rates](https://wdgzone.tech/catalog/pubg-mobile-id-auto-22) — يعرض كتالوجاً حياً ويصفه بأنه wholesale.
- SHOP2TOPUP: [PUBG Mobile](https://shop2topup.com/en/shop/pubg-mobile) — هذا سعر واجهة متجر علنية، **ليس برهاناً على سعر حساب reseller**.
- S7SH: [60 UC](https://s7sh.com/services/60-uc-pubg) — سعر تجزئة محلي معلن ($1.00)، مفيد لتحديد سقف البيع المحلي لا كمورد API مؤكّد.

### Free Fire MENA — تطابق إجمالي الماس

| إجمالي الماس | FazerCards: لوحة الحساب (MENA) | SHOP2TOPUP: MENA علني | الفرق لصالح Fazer | ملاحظة |
|---|---:|---:|---:|---|
| 100 + 10 = 110 | **$0.91** | $0.95 | $0.04 (4.2%) | نفس اسم/إجمالي الباقة تقريباً؛ سعر SHOP2TOPUP retail علني لا سعر B2B. |
| 210 + 21 = 231 | **$1.82** | $1.90 | $0.08 (4.2%) | نفس الشرط. |
| 530 + 53 = 583 | **$4.57** | $4.76 | $0.19 (4.0%) | نفس الشرط. |
| 1080 + 108 = 1188 | **$9.13** | $9.52 | $0.39 (4.1%) | نفس الشرط. |
| 2200 + 220 = 2420 | **$18.23** | $19.03 | $0.80 (4.2%) | نفس الشرط. |

مصدر SHOP2TOPUP: [Free Fire MENA Direct Topup](https://shop2topup.com/en/shop/free-fire/mena-direct-topup).
للمقارنة الإضافية فقط، [WDGZone Free Fire Global](https://wdgzone.tech/catalog/garena-free-fire-vouchers-official-15) يعرض 100+10 مقابل $0.927؛ لكنه **Global** وليس MENA، ولذلك ليس حكماً نهائياً على Fazer MENA.

### Mobile Legends

لا توجد بعد مقارنة نهائية صحيحة: لوحة FazerCards المرسلة تعرض مثلاً 12 Diamonds بـ$0.214 و28 بـ$0.49، بينما صفحة SHOP2TOPUP العلنية المعروضة هي قناة Philippines وباقات مختلفة. لا ينبغي تحويل فرق السعر لكل Diamond إلى نتيجة لأن المنطقة والقناة والـSKU مختلفة.

لكن من ناحية الحقول، يعد كل من FazerCards وSHOP2TOPUP ملائماً تقنياً: صفحة SHOP2TOPUP نفسها توثق أن MLBB يحتاج **User ID + Zone ID**، وهو ما يدعمه تكامل البوت الحالي.

---

## 3) الاستنتاج التجاري الحالي

### ما ثبت فعلاً

- FazerCards ليس أرخص في كل باقة: WDGZone أرخص في **First Purchase** و**Prime شهر** في هذه اللقطة.
- FazerCards أرخص في عينات PUBG الأساسية الكبيرة/الصغيرة المرصودة، وفي **كل مستويات Free Fire MENA الخمسة** مقارنة بسعر SHOP2TOPUP العلني.
- SHOP2TOPUP قوي تقنياً ويعطي API موثقاً وplayer validation، لكن الأسعار المعروضة للعامة لا تعني أنها أسعار شرائه كـreseller.
- WDGZone هو المنافس الوحيد الذي وجدنا له، حتى الآن، **سعراً جملة علنياً حياً** قريباً من FazerCards في PUBG؛ لذا يستحق المقارنة أولاً.

### ما لم يثبت بعد

- أن WDGZone وFazerCards يملكان القناة والقيود الإقليمية ذاتها لكل SKU PUBG.
- سعر API/reseller الحقيقي لـSHOP2TOPUP وMelostore وSEAGM وUniPin.
- الهوية القانونية، شروط التعويض، سياسة النزاعات، ومصدر المخزون لكل المرشحين الصغار.
- تكلفة الاشتراك الشهرية الحقيقية وخطة FazerCards المطبقة على الحساب الحالي؛ صفحة الخطط تؤكد وجود اشتراك شهري لكنها توضح أن الرسم الفعلي يظهر في صفحة الخطة/الحساب.

### قرار قابل للتنفيذ الآن

1. **لا تموّل رصيداً كبيراً في أي مورد.**
2. احتفظ بـFazerCards كمورد أول جاهز تقنياً، لأن التكامل مكتمل ويدعم الحقول الديناميكية.
3. اجعل البديل الأول للفحص التقني/السعري: **WDGZone**، ثم **SHOP2TOPUP**، ثم **Melostore**، ثم **SEAGM**.
4. لا تربط أي مصدر من فئة «يتطلب تدقيقاً» في البوت قبل التحقق من الشركة وكتالوج read-only وشروط ردّ الرصيد.

---

## 4) أفضل المرشحين العمليين للبوت الحالي

| الأولوية | المورد | لماذا هو مرشح فعلي | الحقول/التشغيل | السعر العلني | قرار المرحلة التالية |
|---:|---|---|---|---|---|
| 1 | [FazerCards](https://reseller.fazercards.com/en/docs) | API v2 وwebhooks وكتالوج متعدد الحقول؛ التكامل موجود بالفعل. | يدعم حقولاً ديناميكية؛ MLBB Player+Server ظاهر في كتالوجه. | نعم، لكن الصفحة العامة استرشادية وليست سعر الحساب. | لا تمويل كبير؛ راقب السعر الفعلي للحساب. |
| 2 | [WDGZone](https://wdgzone.tech/docs/) | وثائق API وكاتالوج جملة حي وأسعار PUBG/FF مرئية. | يلزم التأكد من schema الحقول لكل game/SKU قبل الربط. | **نعم: wholesale live**. | فحص كتالوج/سياسة/مطابقة SKU أولاً. |
| 3 | [SHOP2TOPUP](https://shop2topup.com/en/resellers) | REST API موثق: catalog، player validation، create order، status/history؛ يقول إن API يصدر بعد تفعيل 2FA. | مناسب لـPlayer ID وZone في صفحات الألعاب؛ يدعم مناطق متعددة. | نعم للواجهة العامة، وليس لسعر reseller. | المقارنة read-only للكتالوج ثم طلب عرض B2B عند الحاجة. |
| 4 | [Melostore H2H](https://h2h.melostore.id/en) | sandbox وPartner API وwebhooks HMAC، ويدرج PUBG/FF/ML في كتالوجه. | API key+secret وproduction IP whitelist؛ عملته MC/IDR. | كتالوج ظاهر، لكن يلزم حساب لمعرفة tier النهائي. | فحص التحويل IDR/USDT والقيود الإقليمية. |
| 5 | [SEAGM OpenAPI](https://doc.openapi.seagm.io/) | توثيق رسمي واضح للبطاقات والشحن المباشر والحالات وأخطاء الرصيد/المنطقة. | يتطلب اعتماد/صلاحيات حساب للمنتجات. | لا قائمة جملة عامة مفيدة للرصد. | تواصل B2B فقط بعد إثبات جدوى مقارنة الأسعار. |
| 6 | [UniPin Partner API](https://documenter.getpostman.com/view/8087848/SVfNvUt2) | توثيق partner flow لقائمة الألعاب والتفاصيل والتحقق والطلب والاستعلام. | شراكة/صلاحيات؛ مناسب خصوصاً لأسواق آسيا. | لا. | مرشح عقد أكبر لا بداية سريعة. |
| 7 | [Mochigamestore API](https://mochigamestore.com/api/docs) | API موثق للـservices/prices/order/status. | اتجاه إندونيسي؛ يلزم اختبار الألعاب والمناطق. | يحتاج حساب. | تدقيق فقط، لا تمويل. |
| 8 | [VIPayment Reseller API](https://vip-reseller.co.id/page/api/game-feature) | API game services/prices/order/status موثق علناً. | اتجاه إندونيسي/إقليمي. | يحتاج حساب. | تدقيق فقط، لا تمويل. |
| 9 | [MTCGame](https://www.mtcgame.com/page/contact-us) | يعرض طلبات B2B/Reseller/API وwholesale عبر قنوات رسمية في صفحة التواصل. | يحتاج موافقة تجارية وعرض سعر. | لا. | مرشح قوي للتواصل إذا زاد الحجم. |
| 10 | [G2Bulk](https://g2bulk.com/) | يعلن REST API وwebhooks ويدرج PUBG/FF/ML ومناطق MENA؛ crypto checkout only. | لا توجد وثائق تقنية مستقلة كافية في الرصد. | لا. | تحقق قانوني/تقني عميق قبل أي رصيد. |
| 11 | [Turgame B2B](https://wholesale.turgame.com/) | يعلن API وكتالوج بطاقات/مفاتيح؛ مناسب أكثر للقسائم من direct top-up. | يحتاج partner account. | لا. | مصدر ثانوي للبطاقات لا البديل الأول للشحن المباشر. |
| 12 | [FoxReload](https://foxreload.com/en) | يعلن API موحداً لشحنات ألعاب متعددة. | لا يكفي الإعلان لإثبات شروط المخزون أو الحقول. | أسعار ابتدائية فقط. | تجميد حتى تدقيق الهوية والـdocs. |

### ملاحظة تقنية

FazerCards هو الوحيد المدمج الآن. لا تبدأ برمجة adapter جديد قبل أن يظهر تفوق سعري متكرر على **عينة 20 SKU** على الأقل، وأن يثبت المورد دعم الحقول المطلوبة والـwebhook/الاستعلام وإعادة الرصيد عند الفشل.

---

## 5) قائمة 50 مصدراً مرشحاً، مفصولة حسب قوة الدليل

### مفتاح الحالة

- **A — API موثق/كتالوج مرئي:** مرشح حقيقي للربط، لكنه ليس اعتماداً مالياً أو قانونياً.
- **B — يدّعي B2B/API أو يتطلب تواصلاً:** لا يوصل بالبروتوكول ولا يموّل قبل تدقيق مستقل.
- **C — مؤسسة/موزع/حل مؤسسي:** مفيد للعرض التجاري أو البطاقات، وليس بالضرورة direct game top-up self-service.
- **R — متجر تجزئة/benchmark:** يقارن سعر البيع فقط؛ لا يفترض وجود API reseller.
- **L — محلي سوري/عربي:** benchmark محلي؛ لم يظهر API عام موثق في هذا الرصد.

| # | المصدر | الحالة | ما يظهر علناً | الاستخدام الصحيح |
|---:|---|---|---|---|
| 1 | [FazerCards](https://reseller.fazercards.com/en/docs) | A | API v2، webhooks، لعبة/بطاقات، أسعار استرشادية | مورد البوت الحالي؛ قارنه ولا تفترض الأرخص. |
| 2 | [WDGZone](https://wdgzone.tech/docs/) | A | API وكتالوج جملة حي وأسعار | أول منافس سعري لفحص PUBG/FF. |
| 3 | [SHOP2TOPUP](https://shop2topup.com/en/resellers) | A | REST API، validation، catalog، 30+ منطقة | منافس تقني/سعري، سعر reseller يحتاج حساب. |
| 4 | [Melostore H2H](https://h2h.melostore.id/en) | A | Sandbox، API، HMAC webhook، كتالوج كبير | منافس قوي مع تعقيد IDR/IP whitelist. |
| 5 | [SEAGM OpenAPI](https://doc.openapi.seagm.io/) | A | API للشحن المباشر والبطاقات | مرشح B2B؛ السعر/الوصول يحتاج اعتماداً. |
| 6 | [UniPin Partner API](https://documenter.getpostman.com/view/8087848/SVfNvUt2) | A | game list/details، validation، order، inquiry | شريك API محتمل، غالباً بعقد. |
| 7 | [Mochigamestore](https://mochigamestore.com/api/docs) | A | services/prices/order/status API | مرشح إقليمي يحتاج تدقيق مناطق. |
| 8 | [VIPayment](https://vip-reseller.co.id/page/api/game-feature) | A | game API وprices/order/status | مرشح إقليمي يحتاج تدقيق مناطق. |
| 9 | [GameVia](https://gamevia.shop/resellerprogram.php) | B | برنامج reseller/API معلن | فحص قانوني وتقني قبل التعامل. |
| 10 | [NinjaTopups](https://ninjatopups.com/) | B | reseller/API مُعلن | فحص هوية/شروط/قائمة حقول أولاً. |
| 11 | [BFG API](https://api.battlefieldgames.cc/) | B | بوابة API لشحن الألعاب | لا تمويل بلا توثيق/شركة/اختبار قراءة. |
| 12 | [Item4Gamer](https://item4gamer.com/) | B | موقع خدمات/إعادة بيع | اطلب فقط docs عامة بعد التدقيق. |
| 13 | [Yard API](https://yardapi.com/) | B | API ألعاب معلن | لا يُعتمد بلا تحقق مستقل. |
| 14 | [SkyZiro](https://skyziro.com/en) | B | مورد/منصة شحن معلنة | تدقيق مناطق ووسائل تسوية. |
| 15 | [PrepaidForge](https://prepaidforge.com/) | B | API/منتجات رقمية معلنة | تدقيق عالٍ قبل أي رصيد. |
| 16 | [DonatellaHub](https://donatella-hub.tech/) | B | API شحن مُعلن | تدقيق عالٍ قبل أي رصيد. |
| 17 | [BNT Topup](https://bnttopup.com/?lang=en) | B | portal reseller وAPI/callback عند التفعيل | يحتاج عضوية/موافقة. |
| 18 | [GMPAPA](https://gmpapa.com/) | B | مرشح API/سوق شحن مرصود | لا توجد أدلة كافية بعد. |
| 19 | [GoXtop](https://goxtop.com/) | B | reseller/API مرصود | لا توجد أدلة كافية بعد. |
| 20 | [GameCore API](https://gamecore-api.tech/en) | B | API شحن ألعاب معلن | لا تمويل قبل تدقيق مستقل. |
| 21 | [FoxReload](https://foxreload.com/en) | B | API موحد ومنتجات ألعاب معلنة | راجع هوية الشركة والتعويضات. |
| 22 | [G2Bulk](https://g2bulk.com/) | B | API/webhooks معلنة وكتالوج ألعاب | اختبر catalog فقط أولاً. |
| 23 | [MTCGame](https://www.mtcgame.com/page/contact-us) | B | قنوات B2B/API وwholesale رسمية | عرض سعر تجاري عند الحاجة. |
| 24 | [Turgame B2B](https://wholesale.turgame.com/) | B | API وبطاقات/مفاتيح بالجملة | مناسب للبطاقات أكثر من direct top-ups. |
| 25 | [GIFQ](https://www.gifq.com/use-cases/gaming-top-ups) | B | حل بطاقات/شحن B2B معلن | تحقق من الدول وdirect top-up. |
| 26 | [I Codes Wholesale](https://icodeswholesale.com/) | B | API بطاقات/ألعاب | قيد بلد/دفع محتمل؛ ليس بديلاً مؤكداً للشحن بالـID. |
| 27 | [Wholesale Digital Key](https://wholesaledigitalkey.com/) | B | API مفاتيح/بطاقات معلن | قارن القسائم فقط بعد التدقيق. |
| 28 | [CodesWholesale](https://codeswholesale.com/) | C | توزيع مفاتيح وأكواد للشركات | قد يفيد للـkeys/GC، لا تفترض direct top-up. |
| 29 | [Driffle](https://driffle.com/) | C | سوق/توزيع منتجات رقمية | مصدر تجزئة/تجاري محتمل، لا API direct مثبت هنا. |
| 30 | GamesDrop | C | موزع/شبكة رقمية ورد اسمه ضمن مرشحي سوق B2B | تحقق من الموقع والكيان قبل التواصل. |
| 31 | Wupex | C | اسم مورد ظهر ضمن شبكة منافسين | لا تعامل قبل الحصول على مصدر رسمي موثق. |
| 32 | [Xsolla](https://xsolla.com/) | C | commerce/publisher infrastructure | مسار enterprise/publisher، وليس مورداً ذاتي التسجيل. |
| 33 | [Coda / Codashop](https://www.coda.co/) | C | توزيع/دفع ألعاب رسمي/مؤسسي | عقد/شراكة، لا API reseller عام مفترض. |
| 34 | [Midasbuy](https://www.midasbuy.com/) | C | قناة رسمية/تجزئة لمنتجات Tencent | benchmark أو شراكة، لا تستخدم مواقع API تحمل الاسم بلا تأكيد رسمي. |
| 35 | [Razer Gold](https://gold.razer.com/) | C | محفظة/بطاقات ألعاب واسعة | قسائم أو شراكة، وليس direct top-up مفتوحاً مثبتاً. |
| 36 | [Zendit](https://zendit.io/) | C | بنية vouchers/top-up B2B | يحتاج عقد/تغطية منطقة. |
| 37 | [Reloadly](https://www.reloadly.com/) | C | API أرصدة/قسائم رقمية | غالباً مكمل للبطاقات، افحص الألعاب المتاحة. |
| 38 | [Tillo](https://www.tillo.com/) | C | بنية gift-card B2B | بطاقات، ليس منافساً مباشراً لـPlayer ID. |
| 39 | [Blackhawk Network](https://blackhawknetwork.com/) | C | توزيع بطاقات مؤسسي | عقد كبير/بطاقات، لا self-service مخصص للبوت. |
| 40 | [Smile.One](https://www.smile.one/) | R | متجر/قناة شحن مباشرة | benchmark للسعر والقنوات الرسمية. |
| 41 | [JollyMax](https://www.jollymax.com/) | R | متجر شحن عالمي ومنتجات رسمية معلنة | benchmark فقط؛ لم يظهر API reseller عام. |
| 42 | [BuffBuff](https://www.buffbuff.com/) | R | متجر شحن | benchmark فقط. |
| 43 | [TopUpLive](https://www.topuplive.com/) | R | متجر شحن | benchmark فقط حتى يثبت عقد API. |
| 44 | [BitTopup](https://bittopup.com/) | R | متجر شحن عالمي | benchmark فقط؛ لا تعتمد ادعاءات الخصم التسويقية. |
| 45 | [OffGamers](https://www.offgamers.com/) | R | retailer قديم للبطاقات والشحن | benchmark/مصدر شراء يدوي، لا API B2B مثبت هنا. |
| 46 | [Eneba](https://www.eneba.com/) | R | marketplace رقمي | benchmark فقط؛ لا يصلح مباشرة للشحن الآلي. |
| 47 | [LootBar](https://lootbar.gg/) | R | متجر شحن طرف ثالث | benchmark فقط؛ راعِ سياسات ناشر كل لعبة. |
| 48 | [Gamecardsdirect](https://gamecardsdirect.com/) | R | متجر بطاقات وأرصدة ألعاب | benchmark للبطاقات لا direct top-up B2B. |
| 49 | [S7SH](https://s7sh.com/) | L | متجر سوري، شام كاش، Player ID، سعر علني | benchmark محلي وسقف تسعير؛ API عام غير موثق. |
| 50 | [SHFLI](https://www.shfli.com/) | L | متجر/سوق شحن سوري مرصود | benchmark محلي فقط حتى ظهور عقد/API. |

### مصادر محلية إضافية ظهرت في الرصد (ليست ضمن عدّ الـ50)

- Dark Shot / إعلانات خدمات PUBG: مفيدة كإشارة سعر محلي فقط.
- Charger Quick وIsh7nha وDaleelStore: متاجر/مواقع تجزئة إقليمية، لا يثبت الرصد وجود API reseller أو تسعير جملة.

---

## 6) مخاطر لا يجوز تجاوزها

1. **ادعاء API لا يكفي.** أي موقع صغير أو حديث يجب أن يمر عبر: اسم شركة/عنوان قانوني، سياسة refund، شروط استخدام، سجل استجابة دعم، وثائق signatures/idempotency، وكشف مصدر الرصيد حيث أمكن.
2. **لا تعتمد على «from $…».** قد تكون أقل باقة أو منطقة مختلفة أو PIN بدلاً من direct top-up.
3. **لا تستخدم seller غير مصرح به.** بعض الناشرين يغيرون سياسة الشحن الخارجي؛ راجع سياسة اللعبة المحددة قبل إعلان الخدمة.
4. **لا تطلب كلمة مرور اللاعب أو رمز 2FA.** المورد المقبول هنا يحتاج Player ID/UID وZone/Server فقط عندما تطلب اللعبة ذلك.
5. **لا تموّل كامل الرصيد.** بعد التحقق، ابدأ بأصغر رصيد قادر على تنفيذ اختبار داخلي مشروع واحد لكل قناة/منطقة، مع عنوان طلب idempotent وسجل reconciliation.
6. **لا تضع مفاتيح API في المحادثة أو Git.** أي مفاتيح مستقبلية تحفظ فقط في secrets/deployment environment.

---

## 7) خطة حسم الاختيار دون شراء الآن

### المرحلة A — قراءة فقط (لا أموال)

1. افتح الحسابات/الكتالوجات العامة لدى WDGZone وSHOP2TOPUP وMelostore وSEAGM وفق شروطهم، من دون إيداع.
2. صدّر أو سجّل 20 SKU موحدة:
   - PUBG Global/Auto: Weekly 1، 60، 325، 660، 1800، 3850، 8100، First Purchase، Prime 1M.
   - Free Fire MENA: 110، 231، 583، 1188، 2420، Weekly، Monthly.
   - ML Global: نفس الباقات التي يعرضها المورد بعد تحديد Player ID + Zone/Server.
3. لكل صف، سجّل region، delivery type، required fields، price، timestamp، currency، fee، ونوع السعر (wholesale/reseller/retail).

### المرحلة B — قرار كمي

- استبعد فوراً أي مورد لا يطابق حقول اللاعب أو المنطقة أو لا يملك status/refund مسجلاً.
- لكل مورد احسب **المتوسط المرجّح لتكلفة 20 SKU** بعد رسومه الثابتة والمتغيرة.
- لا تستبدل FazerCards إلا إذا كان البديل:
  1. أرخص بوضوح بعد التكلفة الواصلة (اقتراح: 3–5% على الأقل في حجم الطلب المتوقع)،
  2. ينجح في كل الحقول المطلوبة،
  3. يثبت reconciliation/refund وSLA،
  4. وتجاوز فحص الهوية والسياسات.

### المرحلة C — اختبار محدود لاحقاً، فقط بعد موافقة صريحة

إن تقرر التمويل لاحقاً، نفّذ اختباراً صغيراً مشروعاً على حساب اختبار تملكه، طلباً واحداً لكل قناة، ثم راقب status/webhook والـrefund. لا يُعرض المورد للعميل قبل نجاح هذه المرحلة.

---

## 8) الروابط الأساسية التي دعمت الاستنتاج

- [FazerCards: PUBG public catalogue](https://reseller.fazercards.com/en/catalog/pubg) — يصرح بأن القائمة استرشادية وأن سعر الحساب الحي هو المرجع.
- [FazerCards: Free Fire public catalogue](https://reseller.fazercards.com/en/catalog/free-fire)
- [FazerCards: MLBB public catalogue](https://reseller.fazercards.com/en/catalog/mobile-legends)
- [FazerCards: plans](https://reseller.fazercards.com/en/pricing) — يوضح وجود رسم شهري واختلاف economics بين الخطط.
- [WDGZone: PUBG live wholesale](https://wdgzone.tech/catalog/pubg-mobile-id-auto-22)
- [WDGZone: Free Fire live wholesale](https://wdgzone.tech/catalog/garena-free-fire-vouchers-official-15)
- [SHOP2TOPUP: reseller/API description](https://shop2topup.com/en/resellers)
- [SHOP2TOPUP: Free Fire MENA public prices](https://shop2topup.com/en/shop/free-fire/mena-direct-topup)
- [SHOP2TOPUP: MLBB public flow and Player ID + Zone ID](https://shop2topup.com/en/shop/mobile-legends-bang-bang)
- [Melostore H2H: API and catalog](https://h2h.melostore.id/en)
- [SEAGM: OpenAPI](https://doc.openapi.seagm.io/)
- [S7SH: 60 UC local retail price](https://s7sh.com/services/60-uc-pubg)
