"""مساعدات حقول الطلب الديناميكية لمزودي الألعاب.

بعض عروض الشحن تحتاج أكثر من قيمة، مثل ``player_id`` و ``zone_id``. نخزن
الحقول في ``UnifiedOrder.target`` على هيئة JSON صغير حتى تبقى قابلة للإرسال
للمزود وعرضها بصورة مفهومة في التأكيدات، من دون تغيير مخطط قاعدة البيانات.
"""

from __future__ import annotations

import json
from typing import Any


_FIELD_LABELS_AR = {
    "player_id": "معرّف اللاعب",
    "playerid": "معرّف اللاعب",
    "user_id": "معرّف اللاعب",
    "userid": "معرّف اللاعب",
    "uid": "معرّف اللاعب",
    "zone_id": "معرّف المنطقة / السيرفر",
    "zoneid": "معرّف المنطقة / السيرفر",
    "server_id": "معرّف السيرفر",
    "serverid": "معرّف السيرفر",
    "server": "السيرفر",
    "region": "المنطقة",
    "game_id": "معرّف اللعبة",
    "gameid": "معرّف اللعبة",
    "account_id": "معرّف الحساب",
    "accountid": "معرّف الحساب",
    "login": "اسم الدخول",
    "username": "اسم المستخدم",
    "character_id": "معرّف الشخصية",
    "characterid": "معرّف الشخصية",
}


def field_label_ar(key: object, fallback: object | None = None) -> str:
    """تسمية عربية قصيرة لمفاتيح المزود المتعارف عليها."""
    normalized = str(key or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _FIELD_LABELS_AR:
        return _FIELD_LABELS_AR[normalized]
    text = str(fallback or key or "البيان المطلوب").strip()
    return text or "البيان المطلوب"


def encode_provider_fields(values: dict[str, Any]) -> str:
    """يرمز حقول العميل بشكل ثابت وصغير صالح للتخزين في حقل الهدف."""
    clean = {
        str(key): str(value).strip()
        for key, value in values.items()
        if str(key).strip() and value is not None and str(value).strip()
    }
    return json.dumps(clean, ensure_ascii=False, separators=(",", ":"))


def decode_provider_fields(target: object) -> dict[str, str] | None:
    """يفك JSON الحقول إن كان ``target`` من مسار الحقول الديناميكية فقط."""
    if not isinstance(target, str) or not target.startswith("{") or len(target) > 500:
        return None
    try:
        parsed = json.loads(target)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not parsed or len(parsed) > 12:
        return None
    result: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not key.strip():
            return None
        if isinstance(value, (dict, list, tuple, set)) or value is None:
            return None
        clean = str(value).strip()
        if not clean or len(clean) > 128:
            return None
        result[key] = clean
    return result or None


def display_provider_target(target: object) -> str:
    """يحّول JSON الحقول إلى نص قصير مناسب لتأكيد العميل والإدارة."""
    fields = decode_provider_fields(target)
    if not fields:
        return str(target or "")
    return " · ".join(f"{field_label_ar(key)}: {value}" for key, value in fields.items())
