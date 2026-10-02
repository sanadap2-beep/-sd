# دليل ربط البوتين: بوت SD ⇄ بوت واتساب (جسر `wa_bridge`)

قسم «📱 واتساب» في بوت SD لا يعيد كتابة أزرار البوت الثاني — **يعرضها كما هي**
ويُنفّذ أوامره عبر جسر HTTP. الجهة التي يجب أن تُشغَّل هي البوت الثاني.

```
┌──────────────────┐   HTTP + Bearer + X-TG-User-Id   ┌────────────────────────┐
│   بوت SD (هذا)   │ ───────────────────────────────► │ بوت الواتساب الثاني     │
│  قسم 📱 واتساب   │ ◄─────────────────────────────── │  + wa_bridge/bridge.py │
│  باقات + أزرار   │      قائمة/أوامر/ملفات           │    + adapter خاص بك    │
└──────────────────┘                                   └───────────┬────────────┘
                                                                     ▼
                                                          مزوّد الجلسات (BAAS/Baileys…)
```

> كل ملفات الجسر موجودة في هذا المستودع تحت `wa_bridge/`
> (`bridge.py` + `adapter.py` + `adapter_example.py` + `README_AR.md`).
> انسخ المجلد لمشروع البوت الثاني أو شغّله من هنا — لا فرق، هو مجرد FastAPI صغير.

## 1) تشغيل الجسر عند البوت الثاني

```bash
cd <مشروع-البوت-الثاني>
pip install -r wa_bridge/requirements.txt

export BRIDGE_SECRET="$(openssl rand -hex 32)"          # ثبّته في .env ولا يُولَّد وقت الإقلاع
export WA_BRIDGE_ADAPTER="wa_bridge.adapter_example:ExampleWaAdapter"   # ← وضع تجريبي أولاً
uvicorn wa_bridge.bridge:app --host 127.0.0.1 --port 8090
```

للتحقق السريع:

```bash
curl -s localhost:8090/ping -H "Authorization: Bearer $BRIDGE_SECRET"
# {"ok":true,"version":"2.0","adapter":"example-wa","capabilities":[…]}
```

بلا `BRIDGE_SECRET` يرفض الجسر الإقلاع (هذا مقصود). وبلا هيدر صحيح يرد `401`.

## 2) تعريف بوت SD بالجسر

`.env` في بوت SD:

```
WA_BRIDGE_URL=http://wa-bridge:8090        # اسم الخدمة داخل شبكة Docker، أو http://127.0.0.1:8090
WA_BRIDGE_SECRET=<نفس BRIDGE_SECRET>
```

أو من داخل البوت (له الأولوية على ملف البيئة):
**لوحة الأدمن ← 📱 إدارة واتساب ← 🔌 الجسر (URL + سر)** ← ضع القيم ← 🧪 اختبار الاتصال
(يعرض زمن الرد + إصدار الجسر + الـ adapter + القدرات التي أعلنها).

## 3) تفعيل القسم

لوحة الأدمن ← ⚙️ الميزات/مركز الإضافات ← فعّل `whatsapp_section`.
ثم من **📱 إدارة واتساب ← 📝 الوصف والسعر**: وصف القسم، سعر اليوم، والباقات
(JSON) — الافتراضي `يوم/3/7/30 = 1$/2.85$/6.3$/25.5$`.

خيارات إضافية في نفس الميزة (الميزات ← `whatsapp_section` ← الخيارات):
`menu_page_size` (12) · `menu_cache_ttl_minutes` (30) ·
`reminder_hours_before` (12) · `auto_renew_default` · `bridge_alert_failures` (3).

## 4) وصل الـ adapter بأزرار بوتك (الخطوة الحقيقية الوحيدة)

`wa_bridge/adapter_example.py` قابل للتشغيل ويغطي كل الأشكال؛ انسخه وعدّل ثلاث
دوال فقط:

| الدالة | ما يُطلب منها |
|--------|----------------|
| `menu(tg_id, page)` | نفس الأزرار التي يبنيها البوت الثاني للمستخدم الآن (`id` = `callback_data` الحالي) |
| `action(tg_id, id, payload)` | تنفيذ الأمر (استدعِ طبقة الخدمة عندك، لا معالج تليجرام) وإرجاع `{text, menu?, files?, buttons?, awaiting_input?}` |
| `input(tg_id, text)` | نص كتبه المستخدم رداً على زر `kind:"input"` (رسالة، رقم مستلم، …) |

و`start_link` / `link_status` / `unlink` لجلسة واتساب.
التوثيق الكامل لكل الحقول في `wa_bridge/README_AR.md`.

## 5) نقاط الجسر

| النقطة | الطريقة | الوصف |
|--------|---------|-------|
| `/ping` | GET | صحة + `version` + `adapter` + `capabilities` |
| `/link/start` | POST `{phone}` | `{link_code, instructions}` |
| `/link/status` | GET | `{state: none\|pending\|linked\|expired, connected_since}` |
| `/link/unlink` | POST | إنهاء الجلسة (اختياري — يُتجاهل بأمان لو غير موجود) |
| `/menu?page=N` | GET | `{status_text, menu, page, pages, awaiting_input}` |
| `/action` | POST `{action, payload}` | `{text, files, buttons, menu, page, pages, awaiting_input, alert}` |
| `/input` | POST `{text, payload}` | رد المستخدم على زر طلب كتابة |

كلها تتطلب:

```
Authorization: Bearer <WA_BRIDGE_SECRET>
X-TG-User-Id: <telegram user id>
```

## 6) الأمان — لا تتجاوزها

- الجسر **يثق** برأس `X-TG-User-Id`؛ من يعرف السر يستطيع التصرف باسم أي مستخدم.
  لذلك: `--host 127.0.0.1` أو شبكة Docker داخلية **فقط**. لا تنشر 8090 على الإنترنت.
- إن اضطررت للانترنت: TLS (عكس بروكسي) + `allow` لمصدر بوت SD، و`WA_BRIDGE_ALLOW_PUBLIC=1`
  لإسكات التحذير — مع فهمك للمخاطرة.
- سرّ ≥ 16 حرفاً (المولّد من `openssl rand -hex 32`)، وغيّره دورياً في المكانين.
- الجسر لا يسجّل محتوى الرسائل؛ إن أردت تدقيقاً أضفه في الـ adapter عندك.

## 7) تدفق المستخدم بعد الربط (ماذا سيحدث)

1. 📱 واتساب → باقة → يُخصم من الرصيد (حركة `wa_subscription` في دفتر الأستاذ).
2. «أرسل رقم واتساب» → `POST /link/start` → كود الربط + تعليمات المزود.
3. «فحص حالة الربط» → `GET /link/status` → عند `linked` ينفتح زر القائمة.
4. «قائمة بوت الواتساب» → `GET /menu` → أزراره الحقيقية؛ الضغطة → `POST /action`؛
   زر `✍️` → يطلب نصاً → `POST /input`. الملفات/الصور والروابط تظهر كما هي.
5. التجديد التلقائي يعمل كل ساعة (`wa_renewal_cycle` في `bot.py`)، وفحص صحة
   الجسر كل 10 دقائق ينذر الأدمن عند انقطاعه.

## 8) docker (اختياري) — تجربة سريعة في بوت SD

`docker-compose.yml` فيه خدمة `wa-bridge` خلف وضع `wa-bridge` (لا تشتغل إلا لو
طلبتها) لتجربة العقد كاملة بدون البوت الثاني:

```bash
export WA_BRIDGE_SECRET="$(openssl rand -hex 32)"
docker compose --profile wa-bridge up -d wa-bridge
# ثم في اللوحة: http://wa-bridge:8090 + نفس السر
```

## 9) تشخيص الأعطال الشائعة

| الأعراض | السبب الأرجح |
|---------|-------------|
| «تعذّر الوصول إلى جسر واتساب» | الجسر مطفي/عنوان خاطئ/منفذ غير مسموح بين الحاويات |
| «الجسر رفض المفتاح» (401) | `WA_BRIDGE_SECRET` ≠ `BRIDGE_SECRET` |
| القائمة فارغة | `menu()` ترجع عناصر بلا `id`، أو الحالة `linked` غير مُعلَمة |
| «انتهت صلاحية القائمة» | أعد تشغيل البوت أو مرّت مدة الكاش — الضغط التالي يعيد الجلب تلقائياً |
| لا تجديد تلقائي | ميزة `whatsapp_section` معطّلة، أو `auto_renew` مطفأ عند المستخدم، أو رصيد غير كافٍ |
| الأزرار التي تحتاج نصاً لا تعمل | الجسر لم يعلن قدرة `input` (نسخة قديمة من `bridge.py`) |
