"""
مهمة البكاب اليومي لقاعدة البيانات.
تُرسل نسخة من قاعدة البيانات لقناة البكاب الخاصة.

تُؤخذ النسخة عبر VACUUM INTO بدلاً من قراءة الملف مباشرة، لأن قراءة ملف
SQLite حي أثناء الكتابة قد تنتج نسخة تالفة أو غير متسقة. VACUUM INTO
ينتج ملفاً لقطة متسقة (يشمل محتوى ملفات WAL).
"""

import logging
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# حد رفع الملفات عبر Bot API هو 50 ميجابايت.
MAX_BACKUP_SIZE_BYTES = 50 * 1024 * 1024


def _database_path() -> Path:
    """Resolve the SQLite path from DATABASE_URL for local and Docker runs."""
    from config import settings

    prefix = "sqlite+aiosqlite:///"
    if settings.DATABASE_URL.startswith(prefix):
        raw_path = settings.DATABASE_URL[len(prefix) :]
        # Four slashes in a URL encode an absolute filesystem path.
        path = Path(raw_path)
        return path if path.is_absolute() else Path.cwd() / path
    return Path("bot_database.db")


def _create_consistent_snapshot(db_path: Path) -> Path:
    """لقطة متسقة من قاعدة البيانات عبر VACUUM INTO.

    يعمل حتى أثناء نشاط البوت، وينتج ملفاً مضغوطاً وصالحاً للاستعادة.
    """
    target = Path(tempfile.gettempdir()) / (
        f"backup_snapshot_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{id(db_path):x}.db"
    )
    # VACUUM INTO يرفض الكتابة فوق ملف موجود؛ الاسم فريد أعلاه.
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("VACUUM INTO ?", (str(target),))
    finally:
        connection.close()
    return target


async def daily_backup(bot):
    """
    يأخذ نسخة احتياطية من قاعدة البيانات
    ويرسلها لقناة البكاب.
    """
    notifier = NotificationService(bot)

    db_path = _database_path()
    if not db_path.exists() or not db_path.is_file():
        logger.warning(f"ملف قاعدة البيانات غير موجود: {db_path}")
        return

    snapshot_path: Path | None = None
    try:
        snapshot_path = _create_consistent_snapshot(db_path)
        file_size_mb = snapshot_path.stat().st_size / (1024 * 1024)

        if snapshot_path.stat().st_size > MAX_BACKUP_SIZE_BYTES:
            logger.warning(
                f"⚠️ حجم النسخة الاحتياطية {file_size_mb:.2f} MB يتجاوز حد "
                f"رفع تيلجرام (50 MB)؛ تم تخطي الإرسال. قلّص حجم القاعدة أو "
                "استخدم رفعاً خارجياً."
            )
            return

        now = datetime.utcnow()
        filename = f"backup_{now.strftime('%Y%m%d_%H%M%S')}.db"

        caption = (
            f"💾 <b>نسخة احتياطية تلقائية</b>\n\n"
            f"📅 التاريخ: {now.strftime('%Y-%m-%d %H:%M')} UTC\n"
            f"📦 الحجم: {file_size_mb:.2f} MB\n"
            f"📂 الملف: {filename}"
        )

        with snapshot_path.open("rb") as f:
            db_bytes = f.read()

        success = await notifier.notify_backup_channel(
            document_bytes=db_bytes,
            filename=filename,
            caption=caption,
        )

        if success:
            logger.info(f"✅ تم إرسال البكاب بنجاح: {filename} ({file_size_mb:.2f} MB)")
        else:
            logger.warning("⚠️ فشل إرسال البكاب. تحقق من backup_channel_id في الإعدادات.")

    except sqlite3.Error as e:
        logger.error(f"خطأ في إنشاء لقطة قاعدة البيانات: {e}")
    except Exception as e:
        logger.error(f"خطأ في مهمة البكاب: {e}")
    finally:
        if snapshot_path is not None:
            try:
                snapshot_path.unlink(missing_ok=True)
            except OSError:
                logger.warning(f"تعذر حذف ملف اللقطة المؤقت: {snapshot_path}")
