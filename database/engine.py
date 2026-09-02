from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)


@event.listens_for(engine.sync_engine, "connect")
def _tune_sqlite(dbapi_connection, _connection_record):
    """ضبط SQLite لكل اتصال جديد.

    - foreign_keys: SQLite يعطل فرض المفاتيح الأجنبية افتراضياً لكل اتصال.
    - journal_mode=WAL: يسمح بقراءات وكتابة متزامنة (البوت + API على نفس
      الملف) بدلاً من أخطاء "database is locked".
    - busy_timeout: ينتظر حتى 5 ثوانٍ بدلاً من الفشل الفوري عند التصادم.
    - synchronous=NORMAL: الإعداد الموصى به مع WAL (متانة كافية وأسرع).
    """
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
