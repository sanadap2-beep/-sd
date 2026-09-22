# جسر واتساب — للتشغيل بجانب **البوت الثاني**

هذا المجلد هو الخدمة التي تجعل بوت SD (هذا المستودع) يعرض أزرار بوت واتساب
الثاني وينفّذ أوامره. المستخدم لا يرى إلا بوتاً واحداً.

```
بوت SD ── HTTP + Bearer secret ──► هذا الجسر ──► دوال البوت الثاني نفسه ──► مزوّد واتساب
```

## 1) التشغيل (5 دقائق)

```bash
pip install -r wa_bridge/requirements.txt          # fastapi + uvicorn
export BRIDGE_SECRET="$(openssl rand -hex 32)"     # نفس WA_BRIDGE_SECRET في بوت SD
export WA_BRIDGE_ADAPTER="wa_bridge.adapter_example:ExampleWaAdapter"  # ← وضع تجريبي
uvicorn wa_bridge.bridge:app --host 127.0.0.1 --port 8090
curl -s localhost:8090/ping -H "Authorization: Bearer $BRIDGE_SECRET"
```

الرد `{"ok": true, ...}` = الجسر حيّ. اذهب بعدها لبوت SD:
**لوحة الأدمن ← 📱 إدارة واتساب ← 🔌 الجسر** ← ضع العنوان والسر ← 🧪 اختبار الاتصال.

## 2) المتغيرات

| المتغير | الافتراضي | الوصف |
|---------|-----------|-------|
| `BRIDGE_SECRET` | **إلزامي** | السر المشترك. بلا هذا المتغير يرفض الجسر الإقلاع |
| `BRIDGE_HOST` | `127.0.0.1` | لا تضبطه `0.0.0.0` إلا خلف TLS + IP allowlist (انظر §5) |
| `BRIDGE_PORT` | `8090` | منفذ الاستماع |
| `WA_BRIDGE_ADAPTER` | `wa_bridge.adapter:StaticJsonAdapter` | `module.path:ClassName` للـ adapter الذي يربط الجسر ببوتك |
| `WA_BRIDGE_ADAPTER_CONFIG` | `{}` | JSON يُمرَّر kwargs لمُنشئ الـ adapter (مسار ملف، اسم جدول…) |
| `WA_BRIDGE_MENU_FILE` | `wa_bridge/menu.example.json` | ملف القائمة للـ adapter التجريبي |
| `WA_BRIDGE_ALLOW_PUBLIC` | — | `1` لإسكات تحذير الاستماع العام (لا تضبطه إلا لو تعرف ما تفعل) |

## 3) ما عليك تنفيذّه في الـ adapter

`wa_bridge/adapter.py:BridgeAdapter` — أربع دوال تكفي، وكلها async:

| الدالة | الوصف | ترجع |
|--------|-------|------|
| `menu(tg_id, page)` | الأزرار التي يراها هذا المستخدم عندك الآن | `{"status_text", "menu": [item], "page", "pages"}` |
| `action(tg_id, action_id, payload)` | تنفيذ ضغطة زر | `{"text", "menu"?, "files"?, "buttons"?, "awaiting_input"?, "alert"?}` |
| `input(tg_id, text, payload)` | نص كتبه المستخدم لزر `kind:"input"` | نفس شكل `action` |
| `start_link(tg_id, phone)` / `link_status(tg_id)` / `unlink(tg_id)` | جلسة واتساب | `{"link_code","instructions"}` / `{"state","connected_since"}` / `{"ok"}` |

`item` = `{"id", "label", "data"?, "kind"?, "prompt"?, "url"?}` حيث `kind`:
`action` (افتراضي) · `input` (يطلب نصاً) · `url` (يفتح رابطاً) · `back` / `refresh`
(يوليدهما بوت SD نفسه). انسخ `wa_bridge/adapter_example.py` وابدأ منه — قابل
للتشغيل مباشرة ويغطي كل الأشكال (بما فيها إرجاع ملف).

**أهم تفصيل**: `action_id` يجب أن يكون معرّف الـ `callback_data` نفسه عندك
(مثلاً `wa:send:123`)، فبذلك أي زر تضيفه في البوت الثاني يظهر في بوت SD
تلقائياً بلا تعديل هنا.

## 4) لو الأزرار عندك تبنيها دالة keyboard واحدة

اكتب `menu` بحيث تستدعي نفس الباني وتحوّل `InlineKeyboardMarkup` إلى قواميس،
وابقي `callback_data` هو `id`:

```python
from wa_bridge.adapter import BridgeAdapter

def markup_to_items(markup):
    items = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.url:
                items.append({"id": btn.text[:24], "label": btn.text, "url": btn.url, "kind": "url"})
            elif btn.callback_data:
                items.append({"id": btn.callback_data, "label": btn.text})
    return items

class MyWaAdapter(BridgeAdapter):
    name = "mywa"
    capabilities = ("menu", "action", "input", "files", "link", "unlink", "paging")

    async def menu(self, tg_id, page=0):
        markup = await mybot.keyboards.wa_home_kb(tg_id)      # نفس باني الأزرار عندك
        items, page_no, pages = self.paginate(markup_to_items(markup), page)
        return {"status_text": await mybot.services.status_text(tg_id),
                "menu": items, "page": page_no, "pages": pages}

    async def action(self, tg_id, action_id, payload=None):
        text, items = await mybot.services.run_wa_action(tg_id, action_id, payload or {})
        return {"text": text, "menu": markup_to_items(items) if items else None}
```

وحتى تبقى الردود الغنية (صور/ملفات/أزرار) ظاهرة في بوت SD، أرجعها في نفس الرد:
`"files": [{"name": "qr.png", "url": "https://…"}]` و
`"buttons": [{"text": "فتح اللوحة", "url": "https://…"}]`.

## 5) الأمان — اقرأها قبل أن تفتح المنفذ

`X-TG-User-Id` يُرسل كرأس نصي ويثق به الجسر كلياً؛ أي شخص يصل المنفذ ويحمل
السر يستطيع تنفيذ أوامر **باسم أي مستخدم** (إرسال رسائل من رقمه، إنهاء جلسته…).

- استمع على `127.0.0.1` أو عنوان داخلية، ولا تنشر `8090` على الإنترنت.
- لو البوتان في حاويتين: ضع الجسر على شبكة Docker داخلية، و`ports: ["127.0.0.1:8090:8090"]`
  أو بلا `ports` نهائياً (تواصل عبر اسم الخدمة).
- إن لزم الإنترنت: reverse proxy مع TLS + `allow` لمصدر بوت SD + header ثابت إضافي.
- غيّر السر دوريّاً: عدّله في `BRIDGE_SECRET` وفي بوت SD
  (📱 إدارة واتساب ← 🔌 الجسر ← 🔑 سر الجسر) في نفس اللحظة.
- الجسر لا يسجّل شيئاً من محتوى الرسائل؛ أضف `logging` عندك لو أردت تدقيقاً.

## 6) docker (بجانب البوت الثاني)

```yaml
  wa-bridge:
    image: python:3.11-slim
    working_dir: /app
    volumes: ["./wa_bridge:/app/wa_bridge"]
    command: sh -c "pip install -r wa_bridge/requirements.txt && uvicorn wa_bridge.bridge:app --host 127.0.0.1 --port 8090"
    environment:
      BRIDGE_SECRET: ${BRIDGE_SECRET}
      WA_BRIDGE_ADAPTER: ${WA_BRIDGE_ADAPTER}
    network_mode: service:crashbot      # أو نفس الشبكة الداخلية + اسم الخدمة
    restart: unless-stopped
```

ثم في `.env` بوت SD: `WA_BRIDGE_URL=http://wa-bridge:8090` (اسم الخدمة) و
`WA_BRIDGE_SECRET=<نفس السر>`.

## 7) اختبار العقد كاملاً بلا بوت SD

```bash
BRIDGE_SECRET=$(openssl rand -hex 32) python - <<'PY'
import os
from fastapi.testclient import TestClient
from wa_bridge.bridge import create_app
from wa_bridge.adapter import StaticJsonAdapter
h = {"Authorization": "Bearer " + os.environ["BRIDGE_SECRET"], "X-TG-User-Id": "42"}
c = TestClient(create_app(StaticJsonAdapter(), secret=os.environ["BRIDGE_SECRET"]))
print(c.get("/ping", headers=h).json())
print(c.get("/menu", headers=h).json())
print(c.post("/link/start", headers=h, json={"phone": "+966512345678"}).json())
PY
```

نجاح الاختبار يعني العقد سليم 100%؛ ما يتبقى عليك هو أن ترجع `menu`/`action`
قيم بوتك الحقيقية بدل التجريبية.
