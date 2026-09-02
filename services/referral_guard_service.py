"""
حماية الإحالة من البوتات.

أي مستخدم جديد يدخل عبر رابط ``ref_<telegram_id>`` يبقى ``referral_check_pending``
حتى يجتاز اختباراً بشرياً بسيطاً (اختيار زر الرقم المطلوب). عندها فقط يُفعَّل
حسابه وتُدفع مكافأة المحيل.

إذا تكرر الفشل حتى ``max_fails`` يعتبر المستخدم روبوتاً:
- يُحظر الحساب الآلي (إن كان التكوين يسمح).
- يُحظر صاحب رابط الإحالة (إن كان التكوين يسمح) لأنه جرّ بوتاً بغرض الإحالات
  الوهمية، مع إشعار فوري للأدمن وسجل في abuse_events.
"""

from __future__ import annotations

import logging

from database.models import AbuseEvent, User
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)

FEATURE_KEY = "referral_bot_guard"


class ReferralGuardService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY, default=False)

    @staticmethod
    async def max_fails() -> int:
        try:
            value = int(await FeatureService.config(FEATURE_KEY, "max_fails", 3))
            if value <= 0:
                raise ValueError
        except (TypeError, ValueError):
            value = 3
        return value

    @staticmethod
    async def requires_check(user: User) -> bool:
        """هل على هذا المستخدم اجتياز اختبار البشر قبل التفعيل؟"""
        if user is None or not user.referral_check_pending:
            return False
        if not await ReferralGuardService.enabled():
            return False
        return True

    @staticmethod
    def challenge_text(answer: int, fails: int = 0, max_fails: int = 3) -> str:
        remaining = max(0, max_fails - fails)
        lines = [
            "🛡 <b>التحقق البشري</b>\n",
            "عذراً، وصلت عبر رابط إحالة، ولحماية النظام من البوتات "
            "اضغط على الزر الذي يحمل الرقم:",
            f"\n<b>{answer}</b>\n",
        ]
        if fails > 0:
            lines.append(f"⚠️ إجابة خاطئة ({fails}/{max_fails}).\n")
        lines.append(
            "🧠 المحاولات المتبقية: <b>%d</b>" % remaining
            + "\n\nلا تُدفع مكافأة المحيل ولا يُفعَّل حسابك إلا بعد نجاح التحقق."
        )
        return "\n".join(lines)

    @staticmethod
    async def complete(session, user: User) -> None:
        """نجح المستخدم في التحقق — يُترك التفعيل ودفع المكافأة للمتصل."""
        user.referral_check_pending = False
        user.referral_check_fails = 0
        await session.commit()

    @staticmethod
    async def record_failure(session, user: User) -> int:
        user.referral_check_fails = (user.referral_check_fails or 0) + 1
        await session.commit()
        return user.referral_check_fails

    @staticmethod
    async def apply_penalty(session, user: User, bot=None) -> dict:
        """يعاقب روبوت الإحالة ومحيله. يُستدعى بعد بلوغ الحد الأقصى للفشل.

        القرارات (قابلة للتعديل من مركز التحكم بالإضافات):
        - ban_joiner_on_bot: حظر الحساب الآلي.
        - ban_referrer_on_bot: حظر صاحب رابط الإحالة.
        نُبقي referrer_id فارغاً ونمنع أي مكافأة لاحقة عن هذا الحساب.
        """
        outcome = {
            "joiner_banned": False,
            "referrer_banned": False,
            "referrer_id": None,
        }
        referrer_id = user.referrer_id
        outcome["referrer_id"] = referrer_id

        ban_joiner = await FeatureService.config_bool(FEATURE_KEY, "ban_joiner_on_bot", True)
        ban_referrer = await FeatureService.config_bool(
            FEATURE_KEY, "ban_referrer_on_bot", True
        )

        if ban_joiner:
            user.is_banned = True
            outcome["joiner_banned"] = True

        # لا مكافأة مستقبلية عن هذا الحساب حتى لو أُلغيت العقوبة لاحقاً.
        user.referrer_id = None
        user.referral_bonus_paid = True
        user.referral_check_pending = False

        referrer: User | None = None
        if referrer_id is not None:
            from sqlalchemy import select

            referrer = (
                await session.execute(select(User).where(User.id == referrer_id))
            ).scalar_one_or_none()

        if referrer is not None and ban_referrer and not referrer.is_admin:
            referrer.is_banned = True
            outcome["referrer_banned"] = True

        session.add(
            AbuseEvent(
                user_id=user.id,
                event_type="referral_bot_suspected",
                score=max(1, user.referral_check_fails or 1),
                details=(
                    f"روبوت مشتبه عبر رابط إحالة #{referrer_id or 0}"
                    f" — tg={user.telegram_id}"
                ),
            )
        )
        await session.commit()

        # إشعار فوري للأدمن (لا يكسر تدفق الاختبار إن فشل الإرسال).
        if bot is not None:
            try:
                from services.notification_service import NotificationService

                lines = [
                    "🤖 <b>اشتباه بروبوت عبر رابط إحالة</b>\n",
                    f"🆔 الحساب المشتبه: <code>{user.telegram_id}</code>",
                    f"👤 المحيل (صاحب الرابط): "
                    f"<code>{referrer.telegram_id if referrer else '—'}</code>",
                    f"📊 عدد المحاولات الفاشلة: <b>{user.referral_check_fails}</b>",
                    f"🚫 حظر الحساب المشتبه: "
                    f"{'نعم' if outcome['joiner_banned'] else 'لا'}",
                    f"🚫 حظر المحيل: "
                    f"{'نعم' if outcome['referrer_banned'] else 'لا'}",
                    "\nيمكن للأدمن رفع الحظر من إدارة المستخدمين.",
                ]
                notifier = NotificationService(bot)
                await notifier.notify_admin("\n".join(lines))
            except Exception:
                logger.exception("فشل إشعار الأدمن باشتباه روبوت إحالة")

        return outcome
