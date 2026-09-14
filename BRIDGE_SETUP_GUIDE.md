# دليل إعداد جسر واتساب

## نظرة عامة على البنية

```
┌─────────────────┐         HTTP         ┌──────────────────┐
│  البوت الأساسي   │ ◄───────► جسر ◄──────┤  بوت الكراش      │
│  (SD)           │  (wa_bridge_client) │  (wa_bridge)     │
└─────────────────┘                      └──────────────────┘
```

## الخطوات

### 1. إنشاء ملف الجسر (wa_bridge/bridge.py) بجانب بوت الكراش

```python
# wa_bridge/bridge.py
from flask import Flask, request, jsonify
import secrets

app = Flask(__name__)
SECRET = secrets.token_hex(32)  # أو ضع قيمة ثابتة آمنة

@app.route('/link/start', methods=['POST'])
def link_start():
    phone = request.json.get('phone')
    # افتح جلسة واتساب بالرقم
    return jsonify({"link_code": "123456", "instructions": "أدخل الكود عند مزودك"})

@app.route('/link/status', methods=['GET'])
def link_status():
    tg_id = request.headers.get('X-TG-User-Id')
    # تحقق من حالة الربط
    return jsonify({"state": "linked"})

@app.route('/menu', methods=['GET'])
def menu():
    tg_id = request.headers.get('X-TG-User-Id')
    # عُد قائمة البوت
    return jsonify({"menu": [{"id": "cmd1", "label": "أمر 1"}]})

@app.route('/action', methods=['POST'])
def action():
    tg_id = request.headers.get('X-TG-User-Id')
    action_id = request.json.get('action')
    # نفذ الأمر في بوت الكراش
    return jsonify({"text": "تم التنفيذ"})

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"ok": True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
```

### 2. تشغيل الجسر بجانب بوت الكراش

```bash
python wa_bridge/bridge.py
```

### 3. ضبط الإعدادات في البوت الأساسي

في ملف `.env`:
```
WA_BRIDGE_URL=http://your-server-ip:5000
WA_BRIDGE_SECRET=your-secret-here
```

أو من لوحة الأدمن: 📱 إدارة واتساب ← 🔌 الجسر

### 4. اختبار الاتصال

من لوحة الأدمن: 📱 إدارة واتساب ← 🧪 اختبار الاتصال

## نقاط الجسر

| نقطة | طريقة | وصف |
|------|--------|-----|
| `/link/start` | POST | فتح جلسة واتساب |
| `/link/status` | GET | فحص حالة الربط |
| `/menu` | GET | جلب قائمة البوت |
| `/action` | POST | تنفيذ الأوامر |
| `/ping` | GET | اختبار الاتصال |

## التوثيق

كل طلب يجب أن يحمل:
- `Authorization: Bearer <WA_BRIDGE_SECRET>`
- `X-TG-User-Id: <telegram user id>`