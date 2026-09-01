# التشغيل السريع للبوت

## 1) تجهيز البيئة

```bash
cp .env.example .env
```

عدّل `.env` وضع القيم الأساسية:

```env
BOT_TOKEN=
BOT_USERNAME=
ADMIN_IDS=
ADMIN_NOTIFY_CHAT_ID=
PUBLIC_CHANNEL_ID=0
POSTGRES_PASSWORD=change_me
```

## 2) التشغيل بالإنتاج

```bash
docker compose up -d --build
```

سيعمل:

- PostgreSQL
- Redis
- خدمة البوت
- خدمة API / Mini App

## 3) أول إعداد داخل البوت

1. افتح البوت من حساب الأدمن.
2. ادخل لوحة الأدمن.
3. اضبط قناة الأدمن الخاصة.
4. اضبط قناة الإشعارات العامة.
5. اضبط طرق الدفع.
6. أضف مزودين أو منتجات أو مخزون رقمي.
7. اختبر عملية شراء صغيرة قبل الإطلاق.

## 4) فحص سريع قبل التسليم

```bash
python -m compileall -q .
pytest -q
python scripts/security_audit.py
python scripts/i18n_audit.py
```
