# -*- coding: utf-8 -*-
"""
جلب كود دخول تيليجرام (777000) عبر الجلسة (Telethon) لقسم الجلسات الجاهزة.

مسار «📩 طلب الكود»:
  1) إيجاد ملف .session (+ 2FA.txt اختياري) — من files_json محلياً أو من ZIP عبر file_link.
  2) فتح الجلسة بتيليجرام الرسمي (API_ID/API_HASH من my.telegram.org) وقراءة رسائل 777000
     أو الانتظار لوصولها (سجّل الدخول بتطبيق آخر ثم اطلب الكود).
  3) إعادة الكود للـ handler يبعثه للزبون مع كلمة 2FA.

ملاحظات:
  - المسار الاحتياطي القديم (fetch_code_for_payload عبر رابط الكود) يبقى كما هو.
  - يُستورد tg_ready_service AS tgrs ليستطيع الاختبارات ترقيع المسامات (monkeypatch).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Awaitable, Callable, NamedTuple, Optional

import services.tg_ready_service as tgrs
from config import settings

logger = logging.getLogger(__name__)

# انتظار أقصى لرسالة كود 777000 بعد الضغط (ثوانٍ).
SESSION_CODE_WAIT_S = 40
# نافذة قِدَم الرسائل المقبولة من السجل (10 دقائق).
SESSION_CODE_FRESH_S = 600
# مهلة إنشاء الاتصال (ثوانٍ).
_CONNECT_TIMEOUT_S = 25
# مهلة جلب السجل (ثوانٍ).
_HISTORY_TIMEOUT_S = 15


class SessionAssets(NamedTuple):
    session_bytes: Optional[bytes]
    twofa: Optional[str]
    cached: list[str]  # مسارات نسبية حُفظت للتو (يلزم handler كتابتها في files_json + commit)
    error: Optional[str]  # no_session (لا شيء أصلاً) | download_failed (تحميل/ZIP بلا session) | None


def _phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _score_session_name(name: str, digits: str) -> int:
    """كلما قرب اسم الملف من آخر 7 أرقام من الرقم كان أفضل (0 = الأفضل)."""
    if not digits:
        return 1
    tail = digits[-7:]
    name_digits = re.sub(r"\D", "", name)
    if tail and tail in name_digits:
        return 0
    return 1


def _pick_from_zip(raw: bytes, phone: str) -> tuple[Optional[bytes], Optional[str]]:
    """(ملف .session الأقرب للاسم، كلمة 2FA من 2FA.txt إن وُجدت)."""
    digits = _phone_digits(phone)
    best_bytes: Optional[bytes] = None
    best_score = 2
    twofa: Optional[str] = None
    try:
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.file_size > tgrs.MAX_ONE_FILE_BYTES:
                    continue
                low = info.filename.lower()
                if low.endswith(".session"):
                    score = _score_session_name(info.filename, digits)
                    if score < best_score:
                        best_bytes = zf.read(info.filename)
                        best_score = score
                elif twofa is None and low.endswith("2fa.txt"):
                    twofa = zf.read(info.filename).decode("utf-8", errors="ignore").strip() or None
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        logger.warning("zip extract failed: %s", exc)
        return None, None
    return best_bytes, twofa


def _local_paths(item: Any) -> list[str]:
    try:
        data = json.loads(item.files_json or "[]")
    except (TypeError, ValueError):
        return []
    return [str(p) for p in data if isinstance(p, str)]


def _read_local(rels: list[str]) -> tuple[Optional[bytes], Optional[str]]:
    """قراءة .session و 2FA.txt من ملفات محفوظة تحت READY_FILES_ROOT."""
    root: Path = tgrs.READY_FILES_ROOT
    session_bytes: Optional[bytes] = None
    twofa: Optional[str] = None
    for rel in rels:
        low = rel.lower()
        if session_bytes is None and low.endswith(".session"):
            try:
                session_bytes = (root / rel).read_bytes()
            except OSError:
                continue
        elif twofa is None and low.endswith("2fa.txt"):
            try:
                twofa = (root / rel).read_text("utf-8", errors="ignore").strip() or None
            except OSError:
                continue
    return session_bytes, twofa


async def get_session_assets(item: Any, payload: str) -> SessionAssets:
    """
    إيجاد ملف الجلسة (+ كلمة 2FA) للرقم.

    الأولوية: نسخة محلية في files_json ← ZIP عبر file_link.
    عند نجاح ZIP تُحفظ نسخة محلية عبر save_account_files وتُعاد المسارات
    (cached_paths) ليكتبها الـ handler في files_json مع commit صريح.
    """
    phone = str(getattr(item, "phone_number", "") or "")
    local_session, local_twofa = _read_local(_local_paths(item))
    if local_session:
        return SessionAssets(local_session, local_twofa, [], None)

    file_link = tgrs.extract_file_link(payload or "")
    if not file_link:
        # لا جلسة محفوظة ولا أي رابط ملف أصلاً
        return SessionAssets(None, None, [], "no_session")

    try:
        raw = await tgrs.download_file_bytes(file_link)
    except Exception as exc:  # noqa: BLE001 — نُعيد خطأً منظماً للـ handler
        logger.warning("session zip download failed: %s", exc)
        return SessionAssets(None, None, [], "download_failed")
    if not raw:
        return SessionAssets(None, None, [], "download_failed")

    session_bytes, twofa = _pick_from_zip(raw, phone)
    if not session_bytes:
        # الرابط موجود لكن ZIP بلا ملف .session صالح
        return SessionAssets(None, twofa, [], "download_failed")

    cached: list[str] = []
    try:
        batch_id = int(getattr(item, "batch_id", 0) or 0)
        entries: list[tuple[str, bytes]] = [(f"{_phone_digits(phone)}.session", session_bytes)]
        if twofa:
            entries.append(("2FA.txt", twofa.encode("utf-8")))
        cached = tgrs.save_account_files(batch_id, phone, entries)
    except Exception as exc:  # noqa: BLE001 — فشل الكاش غير قاتل
        logger.warning("could not cache session files: %s", exc)
    return SessionAssets(session_bytes, twofa, cached, None)


def _create_client(path: str):  # مسامًا للاختبارات (monkeypatch)
    from telethon import TelegramClient

    return TelegramClient(path, settings.API_ID, settings.API_HASH)


async def fetch_code_via_session(
    session_bytes: bytes,
    *,
    twofa: Optional[str] = None,
    wait_s: int = SESSION_CODE_WAIT_S,
    fresh_s: int = SESSION_CODE_FRESH_S,
    on_wait: Optional[Callable[[], Awaitable[None]]] = None,
) -> dict:
    """
    فتح ملف .session والتقاط كود 777000.

    يرجع dict: {ok, code, twofa, error, retry_after}
      error ∈ not_configured | not_authorized | no_code_yet | rate_limited | fetch_failed
    الترتيب: المستمع أولاً ثم فحص السجل (يغلق سباق الكود القادم بينهما)
    ثم الانتظار wait_s ثانية — الحالتان (قبل/بعد الضغط) مغطاتان.
    """
    if not settings.API_ID or not settings.API_HASH:
        logger.warning("session login: API_ID/API_HASH not configured")
        return {
            "ok": False, "code": None, "twofa": twofa,
            "error": "not_configured", "retry_after": 0,
        }

    tmp_path: Optional[str] = None
    client = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".session", prefix="tg_login_")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(session_bytes)

        from telethon import errors as terr
        from telethon import events as telethon_events

        client = _create_client(tmp_path)
        await asyncio.wait_for(client.connect(), timeout=_CONNECT_TIMEOUT_S)

        if not await client.is_user_authorized():
            return {
                "ok": False, "code": None, "twofa": twofa,
                "error": "not_authorized", "retry_after": 0,
            }

        # مستمع أولاً: أي كود يصل أثناء جلب السجل لا يتسرب.
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future" = loop.create_future()

        async def _on_new_message(event: Any) -> None:
            if fut.done():
                return
            msg = getattr(event, "message", None)
            text = getattr(msg, "text", "") or ""
            code = _fresh_code(text, getattr(msg, "date", None), fresh_s)
            if code and not fut.done():
                fut.set_result(code)

        try:
            client.add_event_handler(_on_new_message, telethon_events.NewMessage(chats=777000))

            # ── 1) السجل: كود وصل قبل ضغط الزبّون (سجّل ثم اضغط هنا) ──
            try:
                msgs = await asyncio.wait_for(
                    client.get_messages(777000, limit=10), timeout=_HISTORY_TIMEOUT_S
                )
            except terr.FloodWaitError:
                raise  # حظر مؤقت → رسالة rate_limited للزبون لا سجل فارغ
            except Exception:  # noqa: BLE001 — جيل بلا access_hash محفوظ → سجل فارغ
                msgs = []
            now = datetime.now(timezone.utc)
            for m in msgs:
                when = getattr(m, "date", None)
                if when is not None:
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    age = (now - when).total_seconds()
                    if age > fresh_s:
                        break  # القائمة من الأحدث فالأقدم — الباقي أقدم أيضاً
                    if age < -30:
                        continue  # تاريخ مستقبلي مشبوه — تجاهلها ونكمل
                code = _fresh_code(getattr(m, "text", "") or "", when, fresh_s)
                if code:
                    return {
                        "ok": True, "code": code, "twofa": twofa,
                        "error": None, "retry_after": 0,
                    }

            # ── 2) الانتظار: الزبّون سيذهب لشاشة الدخول الآن ──
            if wait_s > 0:
                if on_wait is not None:
                    try:
                        await on_wait()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("on_wait callback failed: %s", exc)
                try:
                    code = await asyncio.wait_for(fut, timeout=wait_s)
                    return {
                        "ok": True, "code": code, "twofa": twofa,
                        "error": None, "retry_after": 0,
                    }
                except asyncio.TimeoutError:
                    return {
                        "ok": False, "code": None, "twofa": twofa,
                        "error": "no_code_yet", "retry_after": 0,
                    }
            return {
                "ok": False, "code": None, "twofa": twofa,
                "error": "no_code_yet", "retry_after": 0,
            }
        finally:
            try:
                client.remove_event_handler(_on_new_message)
            except Exception:  # noqa: BLE001
                pass

    except asyncio.TimeoutError:
        logger.warning("session login: connect/history timed out")
        return {
            "ok": False, "code": None, "twofa": twofa,
            "error": "fetch_failed", "retry_after": 0,
        }
    except Exception as exc:  # noqa: BLE001
        from telethon import errors as terr

        if isinstance(exc, terr.FloodWaitError):
            return {
                "ok": False, "code": None, "twofa": twofa,
                "error": "rate_limited", "retry_after": int(getattr(exc, "seconds", 0) or 0),
            }
        logger.warning("session login fetch failed: %s", exc)
        return {
            "ok": False, "code": None, "twofa": twofa,
            "error": "fetch_failed", "retry_after": 0,
        }
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _fresh_code(text: str, when: Optional[datetime], fresh_s: int) -> Optional[str]:
    """استخراج كود حديث من نص رسالة 777000 إن كانت ضمن النافذة الزمنية."""
    if not text:
        return None
    if when is not None:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - when).total_seconds()
        if age > fresh_s or age < -30:
            return None
    codes = tgrs.parse_login_codes(text)
    return str(codes[0]) if codes else None
