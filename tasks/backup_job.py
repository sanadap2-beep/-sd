"""
مهمة البكاب اليومي لقاعدة البيانات — SQLite وPostgreSQL.

القواعد:
- لا تُرسل قاعدة بيانات غير مشفرة أبداً (فيها مفاتيح مزودين نصية).
- PostgreSQL عبر pg_dump (VACUUM INTO خاص بـ SQLite فقط).
- إن تجاوزت النسخة 50MB لا تُرسل لتيليجرام بل تُحفظ في BACKUP_DIR
  مع سياسة احتفاظ، ويُشعَر الأدمن بالمسار.
"""

import asyncio
import gzip
import logging
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# حد رفع الملفات عبر Bot API هو 50 ميجابايت.
MAX_BACKUP_SIZE_BYTES = 50 * 1024 * 1024


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def _database_path() -> Path:
    """Resolve the SQLite path from DATABASE_URL for local and Docker runs."""
    from config import settings

    prefix = "sqlite+aiosqlite:///"
    if settings.DATABASE_URL.startswith(prefix):
        raw_path = settings.DATABASE_URL[len(prefix):]
        # Four slashes in a URL encode an absolute filesystem path.
        path = Path(raw_path)
        return path if path.is_absolute() else Path.cwd() / path
    return Path("bot_database.db")


def _backup_dir() -> Path:
    from config import settings

    d = Path(settings.BACKUP_DIR or "./backups")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fernet():
    from cryptography.fernet import Fernet

    from config import settings

    key = (settings.BACKUP_ENCRYPTION_KEY or settings.INVENTORY_ENCRYPTION_KEY or "").strip()
    if not key:
        raise RuntimeError("لا يوجد مفتاح تشفير للنسخ (BACKUP_ENCRYPTION_KEY)")
    return Fernet(key.encode())


def _encrypt_file(path: Path) -> Path:
    out = path.with_suffix(path.suffix + ".enc")
    out.write_bytes(_fernet().encrypt(path.read_bytes()))
    return out


def _create_sqlite_snapshot(db_path: Path) -> Path:
    target = Path(tempfile.gettempdir()) / (
        f"backup_snapshot_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{id(db_path):x}.db"
    )
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("VACUUM INTO ?", (str(target),))
    finally:
        connection.close()
    return target


def _pg_dump_sync(url: str, target: Path) -> None:
    """pg_dump بصيغة custom المضغوطة. يعمل في thread (حاجب)."""
    parsed = urlparse(url.replace("+asyncpg", "").replace("+psycopg", ""))
    env = {
        "PGPASSWORD": parsed.password or "",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    cmd = [
        "pg_dump",
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "bot",
        "-F", "c",
        "-f", str(target),
        (parsed.path or "/botdb").lstrip("/"),
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump فشل: {proc.stderr.decode()[:300]}")


async def _create_postgres_snapshot(url: str) -> Path:
    target = Path(tempfile.gettempdir()) / (
        f"backup_pg_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.dump"
    )
    await asyncio.to_thread(_pg_dump_sync, url, target)
    return target


def _prune_backups(keep_days: int = 14) -> None:
    try:
        cutoff = datetime.utcnow() - timedelta(days=keep_days)
        for child in _backup_dir().glob("backup_*"):
            try:
                if datetime.utcfromtimestamp(child.stat().st_mtime) < cutoff:
                    child.unlink()
            except OSError:
                pass
    except Exception:
        logger.exception("فشل تنظيف النسخ القديمة")


async def daily_backup(bot):
    """
    نسخة يومية مشفرة دائماً. تُرسل لتيليجرام إن كانت ≤50MB وإلا تُحفظ
    محلياً مع إشعار الأدمن. gating بميزة db_backup_telegram.
    """
    from config import settings
    from services.feature_service import FeatureService

    if not await FeatureService.enabled("db_backup_telegram"):
        logger.info(".daily_backup: ميزة db_backup_telegram معطلة — تم التخطي.")
        return

    notifier = NotificationService(bot)
    snapshot_path: Path | None = None
    enc_path: Path | None = None
    try:
        url = settings.DATABASE_URL
        if _is_postgres(url):
            if shutil.which("pg_dump") is None:
                logger.error("pg_dump غير متوفر — تعذر نسخ PostgreSQL. ثبّته في الصورة.")
                try:
                    await notifier.notify_admin(
                        "⚠️ <b>فشل النسخ الاحتياطي</b>\n\npg_dump غير متوفر في الحاوية."
                    )
                except Exception:
                    pass
                return
            snapshot_path = await _create_postgres_snapshot(url)
            ext, kind = ".dump", "PostgreSQL"
        else:
            db_path = _database_path()
            if not db_path.exists() or not db_path.is_file():
                logger.warning(f"ملف قاعدة البيانات غير موجود: {db_path}")
                return
            snapshot_path = _create_consistent_snapshot(db_path)
            ext, kind = ".db", "SQLite"

        # ضغط ثم تشفير — لا يغادر أي بايت غير مشفر.
        gz_path = snapshot_path.with_suffix(snapshot_path.suffix + ".gz")
        with open(snapshot_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        enc_path = _encrypt_file(gz_path)
        gz_path.unlink(missing_ok=True)

        size = enc_path.stat().st_size
        size_mb = size / (1024 * 1024)
        now = datetime.utcnow()
        filename = f"backup_{now.strftime('%Y%m%d_%H%M%S')}{ext}.gz.enc"
        caption = (
            "💾 <b>نسخة احتياطية تلقائية (مشفرة)</b>\n\n"
            f"🗄 النوع: {kind}\n"
            f"📅 التاريخ: {now.strftime('%Y-%m-%d %H:%M')} UTC\n"
            f"📦 الحجم المشفر: {size_mb:.2f} MB\n"
            f"📂 الملف: {filename}"
        )

        if size > MAX_BACKUP_SIZE_BYTES:
            dest = _backup_dir() / filename
            shutil.move(str(enc_path), str(dest))
            enc_path = None
            _prune_backups()
            logger.warning(
                "حجم النسخة %.2f MB يتجاوز حد تيليجرام — حُفظت في %s", size_mb, dest
            )
            try:
                await notifier.notify_admin(
                    "💾 <b>نسخة احتياطية محفوظة محلياً</b> (تجاوزت 50MB)\n\n"
                    f"📂 <code>{dest}</code>\n📦 {size_mb:.2f} MB مشفرة"
                )
            except Exception:
                pass
            return

        with enc_path.open("rb") as f:
            db_bytes = f.read()
        chat_id = await FeatureService.config("db_backup_telegram", "chat_id", "")
        success = await notifier.notify_backup_channel(
            document_bytes=db_bytes,
            filename=filename,
            caption=caption,
            chat_id_override=chat_id,
        )
        if success:
            logger.info("✅ تم إرسال البكاب المشفر: %s (%.2f MB)", filename, size_mb)
        else:
            # احتفظ بنسخة محلية عند فشل الإرسال بدل ضياعها
            dest = _backup_dir() / filename
            shutil.move(str(enc_path), str(dest))
            enc_path = None
            logger.warning("⚠️ فشل إرسال البكاب — حُفظ محلياً في %s", dest)
    except sqlite3.Error as e:
        logger.error(f"خطأ في إنشاء لقطة قاعدة البيانات: {e}")
    except Exception as e:
        logger.error(f"خطأ في مهمة البكاب: {e}")
    finally:
        for p in (snapshot_path, enc_path):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass


def _create_consistent_snapshot(db_path: Path) -> Path:
    """لقطة متسقة من SQLite عبر VACUUM INTO (تشمل محتوى WAL)."""
    target = Path(tempfile.gettempdir()) / (
        f"backup_snapshot_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{id(db_path):x}.db"
    )
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("VACUUM INTO ?", (str(target),))
    finally:
        connection.close()
    return target
