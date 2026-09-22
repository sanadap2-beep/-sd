# القسمان الجديدان + ربط البوتين (جسر واتساب) — مراجعة وتنفيذ

> الحالة: **مراجعة commit `024234a` ثم تنفيذ التحسينات على فرع العمل**.
> هذا الملف يشرح (1) ما أُضيف، (2) كيف يعمل ربط البوتين بالضبط، (3) ما وُجد
> ناقصاً/مكسوراً وماذا فُعّل الآن، (4) خطوات التشغيل.
> دليل التشغيل اليومي: `BRIDGE_SETUP_GUIDE.md` و`wa_bridge/README_AR.md`.

---

## 1) ماذا أُضيف في PR #30

### القسم الأول — 🤖 الذكاء الاصطناعي (كان مكتملاً أصلاً)

| الملف | الدور |
|-------|-------|
| `services/ai_provider_client.py` | عميل NanoGPT (متوافق OpenAI)؛ مفتاح/عنوان اللوحة يتقدمان على `.env` |
| `services/ai_section_service.py` | أقسام، تسعير، خصم، استرجاع idempotent، جلسات، استخراج ملفات الكود |
| `handlers/ai_sections.py` / `handlers/admin/ai_sections.py` | واجهة المستخدم + لوحة الأدمن (أقسام/مزود/إحصاءات) |
| `keyboards/ai_sections.py`، `migrations/.../e1a2b3c4d5f6`، `seed.py:349-382` | أزرار، جداول، زرع `coding`/`chat` معطّلين |
| `tests/test_ai_sections.py` | 14 اختبار تسعير/خصم/استرجاع/ملفات |

السعر: `التكلفة × (1 + profit_multiplier)` (الافتراضي 3×)، والزر يظهر فقط عند
تفعيل الميزة ووجود قسم مفعّل.

### القسم الثاني — 📱 واتساب (جهة بوت SD كانت شبه مكتملة)

| الملف | الدور |
|-------|-------|
| `services/wa_bridge_client.py` | عميل الجسر |
| `services/whatsapp_section_service.py` | باقات/شراء/ربط/تجديد/إحصاءات |
| `handlers/whatsapp.py` · `handlers/admin/whatsapp.py` | واجهة المستخدم · لوحة الأدمن |
| `WaSubscription`/`WaLinkState` + `migrations/.../f5e6d7c8b9a0` | جدول الاشتراكات |
| `feature_registry.py:570-588`، `locales/*.json` (30 مفتاح) | الميزة والنصوص |

---

## 2) كيف يتم ربط البوتين (الميكانيك كما هو في الكود)

أزرار تليجرام لا تُمرَّر بين بوتين، فالربط **جسر HTTP**: البوت الثاني يبقى مصدر
الحقيقة لواجهته، وبوت SD يجلب القائمة ويرسمها بأزراره، ويردّ كل ضغطة إليه.

```
مستخدم → بوت SD (📱 واتساب)
          │ 1) فحص wa_subscriptions.active_until
          │ 2) HTTP + Authorization: Bearer <سر> + X-TG-User-Id
          ▼
      wa_bridge/bridge.py (بجانب البوت الثاني)
          │ ينادي BridgeAdapter → خدمات البوت الثاني نفسها
          ▼
      بوت واتساب الثاني → مزوّد الجلسات
```

العقد (v2 بعد التوسيع): `GET /ping` · `POST /link/start` · `GET /link/status` ·
`POST /link/unlink` · `GET /menu?page=` · `POST /action` · `POST /input`.
عناصر القائمة `{id, label, data?, kind?, prompt?, url?, disabled?}` و`kind`:
`action|input|url|back|refresh`. الرد `{text, files?, buttons?, menu?, page,
pages, awaiting_input?, alert?}`.

- الأولوية في الإعداد: لوحة الأدمن (`wa_bridge_url` / `wa_bridge_secret`) ثم
  `.env` (`WA_BRIDGE_URL` / `WA_BRIDGE_SECRET`).
- أي فشل يرمي `WaBridgeError` **بلا خصم** (الخصم الوحيد = شراء الباقة).
- القائمة تُخزَّن بكاش محلي بمفتاح عشوائي `wa:act:<token>:<index>`، مع تحقق أن
  صاحب القائمة هو من يضغط (منع قراءة قائمة غيرك).
- التجديد التلقائي وتنبيه الانتهاء كانا **موصولين أصلاً**:
  `wa_renewal_cycle` في `bot.py:411` + `scheduler.add_job(..., hours=1)`
  في `bot.py:593`. (ملاحظة سابقة في هذه المراجعة قالت إنها غير مربوطة — كانت
  خاطئة؛ الفحص تم على `tasks/` فقط. مصحَّحة هنا.)

---

## 3) ما وُجد معيباً في PR #30 — وحالته الآن

| # | المشكلة (كما كانت) | الخطورة | الحالة |
|---|---------------------|---------|--------|
| 1 | **خدمة الجسر غير موجودة أصلاً**: `wa_bridge/bridge.py` مذكور في التوثيق ورسائل اللوحة («في هذا المستودع») لكنه لا يوجد — القسم كله يعطي «تعذّر الوصول إلى جسر واتساب» | 🚨 محرق | **تم**: `wa_bridge/` كامل (`bridge.py` + `adapter.py` + `adapter_example.py` + `README_AR.md` + `requirements.txt` + `menu.example.json`) |
| 2 | **انهيار شاشة القسم**: `WhatsAppSectionService.packages()` دالة **غير async** تستدعي `await FeatureService.config_json(...)` بلا `await` ⇒ `raw` كائن coroutine ⇒ `TypeError` عند رسم الباقات — أي مستخدم بلا اشتراك لا يرى القسم، وكذلك شاشة الأدمن بعد حفظ الباقات | 🚨 محرق (كان مخفياً) | **تم**: صارت `async def packages()` + تحديث كل المواضع، واختبار يثبّت التحقق (`tests/test_wa_bridge.py::test_packages_are_validated_and_sorted`) |
| 3 | عينة الجسر في `BRIDGE_SETUP_GUIDE.md`: Flask (غير موجود في `requirements.txt`)، **لا تتحقق من `Authorization` مطلقاً**، سرّ `token_hex` يُولَّد كل إقلاع فيكسر الربط بعد أي restart، `0.0.0.0:5000` مقابل `8090` في مثال اللوحة، وقيم ثابتة تجريبية | 🔴 أمان/تضليل | **تم**: دليل معاد كتابته + FastAPI فعلي يتحقق بـ `secrets.compare_digest` ويرفض الإقلاع بلا `BRIDGE_SECRET` |
| 4 | العقد لا يدعم أزرار البوت الثاني التي تحتاج **كتابة نص** (إرسال رسالة، رقم مستلم…)، ولا الملفات/الصور، ولا أزرار الروابط، ولا ترقيم القائمة (`items[:40]` فقط) | 🟠 قصور | **تم**: `kind:"input"` + `POST /input`، `files` (رابط/base64)، `buttons` (URL)، ترقيم عبر `GET /menu?page=` + أزرار ◀ ▶، وقدرات معلَنة عبر `/ping` |
| 5 | كاش القائمة في الذاكرة **بلا TTL** وبعد أي restart الأزرار القديمة كلها «انتهت صلاحية القائمة» | 🟠 UX | **تم**: TTL قابل للضبط (`menu_cache_ttl_minutes`) + **إعادة جلب تلقائية** عند token مفقود |
| 6 | `callback.answer` بعد انتهاء استدعاء الجسر (حتى 45 ثانية) ⇒ «ساعة» تليجرام بلا رد | 🟠 UX | **تم**: `await callback.answer("جارٍ التنفيذ…")` قبل أي شبكة |
| 7 | لا مؤشر حالة ولا إنذار للأدمن عند انقطاع الجسر؛ و`_wa_configured()` معرّفة وغير مستخدمة (dead code) | 🟠 مراقبة | **تم**: 🟢/🟠/🔴 في قائمة الإدارة + `wa_bridge_health_cycle` كل 10 دقائق ينذر قناة الأدمن عند العطل والعودة |
| 8 | لا يمكن بيع اشتراك في قسم مكسور — كان الشراء متاحاً دائماً | 🟠 مالي | **تم**: أزرار الباقات معطّلة + رسالة صريحة إذا لم يُضبط الجسر |
| 9 | `start_link` يترك الحالة `PENDING` بلا rollback عند فشل الجسر | 🟡 منطق | **تم**: إرجاع الحالة السابقة + تخزين `link_error` (عمود جديد) تظهر للأدمن |
| 10 | `auto_renew_default` إعداد ميت (لا يقرأه الكود) | 🟡 إعداد | **تم**: يُطبق عند إنشاء الاشتراك + اختبار |
| 11 | لوحة المشتركون للعرض فقط: بلا تمديد/فصل ربط/استرداد، وصفحة واحدة طويلة | 🟡 لوحة | **تم**: قائمة مضغوطة + شاشة مشترك فيها ⏱ تمديد (1/3/7/30 مجاناً)، 🚫 فصل ربط (ينادي `/link/unlink`)، 💵 استرداد آخر دفعة محمي من التكرار بـ `payment_reference` فريد |
| 12 | صفر اختبارات لقسم واتساب | 🟡 تغطية | **تم**: `tests/test_wa_bridge.py` — 36 اختباراً (العقد، العميل، التطبيع، الباقات، التجديد، الاسترداد، صحة الجسر، الكاش/الأزرار، خدمة الجسر نفسها) |
| 13 | توثيق قديم: مفاتيح الأقسام `ai_coding`/`ai_chat` مقابل `coding`/`chat` المزروع (من يتبعها ينشئ قسمين مكررين)، ووصف واتساب كـ«خطة تنفيذ» و«1$ يومياً» فقط | 🟡 توثيق | **تم**: تصحيح `docs/AI-AND-WHATSAPP-SECTIONS_AR.md` + README + `.env.example` |
| 14 | `sd-bot-latest.zip` (تاريخ 2026-09-02، 308 ملف) لا يحتوي أي ملف من القسمين — إن كان هو ما يُسلَّم للمشتري فنسخة ناقصة | 🟡 تسليم | **مفتوح**: يحتاج قراراً (إعادة بناء أو حذف واعتماد Git/Docker) — لم ألمسه |
| 15 | `wa_bridge_secret` و`ai_provider_api_key` نص صريح في جدول `settings` (نفس نمط بقية مفاتيح المشروع) | 🟡 أسرار | **مفتوح**: يمكن تمريرها عبر `services/encryption_service.py` |
| 16 | توقيع الطلبات: السر المشترك وحده + رأس `X-TG-User-Id` غير موقّع | 🔴 تصميم | **مخفَّف**: توثيق إلزامي (loopback/شبكة داخلية، بلا منفذ منشور، `compare_digest`) + تحذير في اللوحة عند `http://` خارجي. HMAC بانتظار طلبك |

### إضافات غير مطلوبة لكن مقترنة

- `migrations/versions/f6a7b8c9d0e1_add_wa_bridge_fields.py`: أعمدة
  `connected_since` و`link_error` (آمنة: تتحقق من وجود العمود).
- `wa_bridge_health_cycle` في `bot.py` (كل 10 دقائق).
- خدمة `wa-bridge` في `docker-compose.yml` خلف `profiles: ["wa-bridge"]`
  (لا تعمل تلقائياً، ولا تنشر port) لتجربة العقد كاملة قبل كتابة الـ adapter.
- خياران جديدان في الميزة: `menu_page_size` (12)، `menu_cache_ttl_minutes` (30)،
  `bridge_alert_failures` (3) — تُعدَّل من ⚙️ الميزات ← `whatsapp_section` ← الخيارات.
- 10 مفاتيح i18n جديدة في `ar.json`/`en.json` (456↔456 مفتاحاً، تناسق تام).

### اختبار كان فاشلاً قبل هذه التغييرات (أُصلح)

`tests/test_button_style_codemod.py::test_audit_reports_no_mismatch_in_keyboards`
كان أحمر على `main`: 6 أزرار بلا `style` في `keyboards/ai_sections.py` و
`keyboards/ready_codes.py` (`ai:cancel`، `menu:deposit`، `admin:ai_stats`،
`readycode:buy:`، `admin:readycode:add`). شغّلت أداة المستودع نفسها
`scripts/apply_button_styles.py` فأضافت الـ styles الستة، والاختبار صار أخضر.

---

## 4) التشغيل (الحد الأدنى بعد هذا التنفيذ)

```bash
# عند البوت الثاني — تجربة العقد أولاً (وضع تجريبي):
export BRIDGE_SECRET="$(openssl rand -hex 32)"
export WA_BRIDGE_ADAPTER="wa_bridge.adapter_example:ExampleWaAdapter"
uvicorn wa_bridge.bridge:app --host 127.0.0.1 --port 8090

# في بوت SD (.env أو لوحة الأدمن):
WA_BRIDGE_URL=http://127.0.0.1:8090
WA_BRIDGE_SECRET=<نفس السر>

# ثم: لوحة الأدمن ← 📱 إدارة واتساب ← 🔌 الجسر ← 🧪 اختبار الاتصال
#     ⚙️ الميزات ← تفعيل whatsapp_section
```

ثم وصّل `menu`/`action`/`input` بخدمات البوت الثاني الحقيقية
(انسخ `wa_bridge/adapter_example.py`) — عندها فقط تصبح «كل أزرار البوت الثاني»
حقيقية بدل ردود تجريبية.

### فحوصات تمت

| الفحص | النتيجة |
|-------|---------|
| `pytest tests/test_wa_bridge.py` | 36 passed (جديد) |
| `pytest` (المجموعة كاملة) | **570 passed، صفر إخفاق** — بعد إضافة 36 اختباراً وإصلاح اختبار الأنماط |
| `ruff check` على كل الملفات المضافة/المعدّلة | All checks passed (بقية الريبو فيه 122 ملاحظة قديمة غير متعلقة بالعمل) |
| `python -m compileall handlers services wa_bridge bot.py database migrations` | نظيف |
| اختبار حيّ: `uvicorn wa_bridge.bridge:app` + `services/wa_bridge_client` عبر HTTP حقيقي | `probe` (قدرات + 23ms) · `menu` بأنواعها · `start_link`/`link_status` · `action` رفض بـ«اكتب النص أولاً» (نفس نص الـ adapter، لا «كود 400») · `input` · `export` رجع ملف `outbox.txt` · `unlink: True` · سر خاطئ ⇒ «الجسر رفض المفتاح» وعدّاد الإخفاقات = 1 |
| سلسلة الـ migrations على قاعدة جديدة | `e1a2b3c4d5f6` → `f5e6d7c8b9a0` → `f6a7b8c9d0e1` (head) |
| `locales/ar.json` ↔ `en.json` | 456 ↔ 456 مفتاحاً، بلا نقص، ومتغيّرات كل مفتاح مطابقة للاستعمال |
