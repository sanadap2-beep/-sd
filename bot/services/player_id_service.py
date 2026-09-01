"""
التحقق من آيدي اللاعب قبل الخصم.

المشكلة التي يصلحها هذا الملف:
handlers/games.py:373 كان يقبل آيدي اللاعب بمجرد أن يكون نصاً غير فارغ:

    player_id = message.text.strip()
    if not player_id:
        ...

لا فحص صيغة ولا تحقق من اللعبة. وهذا سبب النزاع رقم 1 في شحن الألعاب:
«شحنت لآيدي غلط» — ولا يمكن إثبات الخطأ على أحد بعد الخصم.

الحل:
1) فحص الصيغة حسب اللعبة (طول وأحرف مسموحة) قبل قبول أي شيء.
2) منع اللصق الخاطئ الشائع: مسافات داخلية، أصفار بادئة، رموز، روابط.
3) إظهار ملخص واضح للاعب قبل التأكيد حتى يلاحظ الخطأ بنفسه.
4) كل السلوك قابل للإيقاف من «مركز الإضافات».

ملاحظة أمانة: كشف اسم اللاعب الحقيقي يتطلب واجهة كل لعبة على حدة،
وهي غير متوفرة هنا. لذلك يركّز هذا الملف على ما يمكن إثباته محلياً
(الصيغة) ويعرض الآيدي للمراجعة قبل الخصم. إضافة اسم اللاعب تحتاج
مزوداً خارجياً لكل لعبة وتُضاف لاحقاً خلف نفس علم الميزة.
"""

from __future__ import annotations

import re

from services.feature_service import FeatureService


class PlayerIdError(Exception):
    pass


# صيغ تقريبية لأشهر الألعاب. الأدمن يستطيع تعطيل الفحص الصارم كلياً
# من مركز الإضافات إذا كانت لعبته غير مدرجة.
_GAME_PATTERNS: dict[str, tuple[re.Pattern, str]] = {
    "pubg": (re.compile(r"^[0-9]{6,15}$"), "6 إلى 15 رقماً"),
    "pubgmobile": (re.compile(r"^[0-9]{6,15}$"), "6 إلى 15 رقماً"),
    "freefire": (re.compile(r"^[0-9]{6,12}$"), "6 إلى 12 رقماً"),
    "garena": (re.compile(r"^[0-9]{6,12}$"), "6 إلى 12 رقماً"),
    "cod": (re.compile(r"^[0-9]{6,15}$"), "6 إلى 15 رقماً"),
    "call of duty": (re.compile(r"^[0-9]{6,15}$"), "6 إلى 15 رقماً"),
    "fortnite": (re.compile(r"^[0-9a-zA-Z_\-\.#]{3,32}$"), "3 إلى 32 حرفاً"),
    "roblox": (re.compile(r"^[0-9]{4,20}$"), "4 إلى 20 رقماً"),
    "clash": (re.compile(r"^#?[0-9A-Z]{6,15}$"), "# ثم 6 إلى 15 حرفاً كبيراً"),
    "mobile legends": (re.compile(r"^[0-9]{6,15}(\([0-9]{3,6}\))?$"), "آيدي(آيدي السيرفر)"),
}

# نمط عام يُستخدم عندما لا نعرف اللعبة: أرقام أو أحرف بلا مسافات أو رموز غريبة.
_GENERIC = re.compile(r"^[0-9A-Za-z_\-\.#()]{4,40}$")

# أنماط تدل على لصق خاطئ بوضوح.
_OBVIOUS_MISTAKES = (
    re.compile(r"^https?://", re.IGNORECASE),
    re.compile(r"^@?[A-Za-z0-9_]{5,32}$"),  # يوزرنيم بدل آيدي رقمي
    re.compile(r"\s"),
)


class PlayerIdService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("player_id_validation")

    @staticmethod
    async def strict() -> bool:
        return await FeatureService.config_bool("player_id_validation", "strict_format", True)

    @staticmethod
    def _match_game(haystack: str) -> str | None:
        for key in _GAME_PATTERNS:
            if key in haystack:
                return key
        return None

    @classmethod
    async def validate(cls, raw: str, context: str = "") -> str:
        """
        يتحقق من الآيدي ويرجعه منظَّفاً، أو يرمي PlayerIdError بسبب واضح.
        context: اسم المنتج/القسم الفرعي لمحاولة معرفة اللعبة.
        """
        text = (raw or "").strip()

        if not text:
            raise PlayerIdError("أرسل آيدي اللاعب.")

        if len(text) > 64:
            raise PlayerIdError("الآيدي طويل جداً. تأكد أنك لم تلصق نصاً كاملاً.")

        if not await cls.enabled():
            return text

        # ── أخطاء لصق شائعة نمنعها دائماً حتى لو كان الفحص غير صارم ──
        if _OBVIOUS_MISTAKES[0].match(text):
            raise PlayerIdError("هذا رابط وليس آيدي لاعب. أرسل الآيدي فقط.")
        if _OBVIOUS_MISTAKES[2].search(text):
            raise PlayerIdError("لا تضع مسافات داخل الآيدي. أرسله متصلاً.")

        # أرقام عربية/هندية تُحوَّل لأرقام لاتينية لأن أغلب الألعاب لا تقبل غيرها
        text = _normalize_digits(text)

        if not await cls.strict():
            if not _GENERIC.match(text):
                raise PlayerIdError(
                    "الآيدي يحتوي رموزاً غير معتادة. تأكد من نسخه كاملاً بلا إضافات."
                )
            return text

        game = cls._match_game((context or "").casefold())
        if game is None:
            if not _GENERIC.match(text):
                raise PlayerIdError(
                    "الآيدي يحتوي رموزاً غير معتادة. تأكد من نسخه كاملاً بلا إضافات."
                )
            return text

        pattern, hint = _GAME_PATTERNS[game]
        if not pattern.match(text):
            raise PlayerIdError(f"آيدي {game} يجب أن يكون {hint}. ما أرسلته: {text}")
        return text

    @staticmethod
    def mask(player_id: str) -> str:
        """يُظهر أول وآخر رقمين للمراجعة السريعة."""
        if len(player_id) <= 4:
            return player_id
        return f"{player_id[:2]}…{player_id[-2:]}"


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def _normalize_digits(text: str) -> str:
    return text.translate(_ARABIC_DIGITS)
