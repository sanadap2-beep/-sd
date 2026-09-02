# تشغيل الإنتاج على PostgreSQL

تم تجهيز `docker-compose.yml` لاستخدام PostgreSQL بدلاً من SQLite في الإنتاج.

## التشغيل

```bash
cp .env.example .env
# غيّر كلمة المرور:
# POSTGRES_PASSWORD=strong-password

docker compose up -d --build
```

الخدمات `bot` و `api` تستخدم:

```text
postgresql+asyncpg://bot:${POSTGRES_PASSWORD}@postgres:5432/botdb
```

## ملاحظات

- SQLite مناسب للتجربة فقط.
- PostgreSQL أفضل للطلبات المتزامنة، السحب/الشحن، السوق، العروض، والإعلانات.
- Alembic يعمل عند بدء البوت/API عبر `init_db()`.
- خذ نسخة احتياطية قبل نقل قاعدة قديمة من SQLite.
