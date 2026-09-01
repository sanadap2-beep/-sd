"""
خدمة المهام مقابل النقاط.

الأدمن يحدد من لوحته: المهمة، نوعها، قيمة النقاط، الحد اليومي،
وآلية التحقق. هذه الخدمة تنفّذ قواعد التحقق وتصرف المكافأة.

آليات التحقق المدعومة (TaskVerification):
- NONE: بلا تحقق، فقط الحدود.
- ONCE_PER_DAY: مرة واحدة في اليوم التقويمي.
- ONCE_PER_USER: مرة واحدة في العمر.
- COOLDOWN_MINUTES: انتظار N دقيقة بين كل إتمام.
- TELEGRAM_MEMBERSHIP: يتحقق فعلياً من عضوية القناة عبر Telegram API.
- PROOF_ORDER: يتطلب طلباً مكتملاً بقيمة حد أدنى.
- ADMIN_APPROVAL: لا تُصرف إلا بعد موافقة الأدمن.
- CAPTCHA: تحدٍّ حسابي بسيط يمنع البوتات.

كل صرف نقاط يمر عبر LoyaltyService.award_points بمفتاح idempotent،
فلن تُحتسب أي مكافأة مرتين حتى لو ضُغط الزر مئة مرة.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from database.models import (
    LoyaltyEvent,
    NumberOrder,
    OrderStatus,
    ProductReview,
    Task,
    TaskProgress,
    TaskRewardType,
    TaskSubmission,
    TaskType,
    TaskVerification,
    Transaction,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.balance_service import BalanceService
from services.feature_service import FeatureService
from services.loyalty_service import LoyaltyService

logger = logging.getLogger(__name__)


class TaskError(Exception):
    pass


class TaskService:
    # ─────────── الاستعلام ───────────

    @staticmethod
    async def active_tasks(session) -> list[Task]:
        result = await session.execute(
            select(Task).where(Task.is_active.is_(True)).order_by(Task.sort_order, Task.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_key(session, key: str) -> Task | None:
        result = await session.execute(select(Task).where(Task.key == key))
        return result.scalar_one_or_none()

    @staticmethod
    async def _progress(session, user_id: int, task: Task, period_key: str) -> TaskProgress | None:
        result = await session.execute(
            select(TaskProgress).where(
                TaskProgress.user_id == user_id,
                TaskProgress.task_id == task.id,
                TaskProgress.period_key == period_key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _period_key(task: Task) -> str:
        if task.verification == TaskVerification.ONCE_PER_DAY or task.daily_limit > 0:
            return f"daily:{datetime.utcnow().date().isoformat()}"
        return "total"

    # ─────────── التحقق ───────────

    @staticmethod
    async def eligibility(session, user_id: int, task: Task, bot=None) -> tuple[bool, str]:
        """
        يفحص كل الشروط قبل صرف المكافأة.
        يرجع (مسموح، سبب الرفض).
        """
        if not await FeatureService.enabled("tasks_system"):
            return False, "نظام المهام موقوف حالياً."

        user = await session.get(User, user_id)
        if user is None:
            return False, "المستخدم غير موجود."
        if user.is_banned:
            return False, "حسابك موقوف."

        # عمر الحساب
        required_hours = max(
            task.min_account_age_hours,
            await FeatureService.config_int("tasks_system", "min_account_age_hours", 1),
        )
        if required_hours > 0 and user.joined_at is not None:
            age_hours = (datetime.utcnow() - user.joined_at).total_seconds() / 3600
            if age_hours < required_hours:
                wait = int(required_hours - age_hours) + 1
                return False, f"تحتاج حساباً بعمر {required_hours} ساعة على الأقل (تبقى ~{wait} ساعة)."

        # حد المهام اليومي العام
        daily_cap = await FeatureService.config_int("tasks_system", "daily_task_limit", 10)
        if daily_cap > 0:
            today = f"daily:{datetime.utcnow().date().isoformat()}"
            done_today = (
                await session.execute(
                    select(func.coalesce(func.sum(TaskProgress.completions), 0)).where(
                        TaskProgress.user_id == user_id,
                        TaskProgress.period_key == today,
                    )
                )
            ).scalar_one()
            if int(done_today) >= daily_cap:
                return False, f"وصلت للحد اليومي ({daily_cap} مهمة). عد غداً."

        # حد المهمة اليومي
        if task.daily_limit > 0:
            today_key = f"daily:{datetime.utcnow().date().isoformat()}"
            progress = await TaskService._progress(session, user_id, task, today_key)
            if progress and progress.completions >= task.daily_limit:
                return False, "أنجزت هذه المهمة بالحد الأقصى لليوم."

        # الحد الكلي
        if task.total_limit > 0:
            total = (
                await session.execute(
                    select(func.coalesce(func.sum(TaskProgress.completions), 0)).where(
                        TaskProgress.user_id == user_id,
                        TaskProgress.task_id == task.id,
                    )
                )
            ).scalar_one()
            if int(total) >= task.total_limit:
                return False, "أنجزت هذه المهمة بالحد الأقصى المسموح."

        # حد المهام المطلوبة قبل فتح المهمة
        if task.min_orders_required > 0:
            orders = await TaskService._completed_orders_count(session, user_id)
            if orders < task.min_orders_required:
                return False, f"تحتاج {task.min_orders_required} طلبات مكتملة قبل هذه المهمة."

        # آلية التحقق الخاصة
        return await TaskService._verify(session, user_id, task, bot)

    @staticmethod
    async def _completed_orders_count(session, user_id: int) -> int:
        sms = (
            await session.execute(
                select(func.count(NumberOrder.id)).where(
                    NumberOrder.user_id == user_id,
                    NumberOrder.status.in_(
                        [OrderStatus.COMPLETED, OrderStatus.CODE_RECEIVED]
                    ),
                )
            )
        ).scalar_one()
        api = (
            await session.execute(
                select(func.count(UnifiedOrder.id)).where(
                    UnifiedOrder.user_id == user_id,
                    UnifiedOrder.status.in_(
                        [UnifiedOrderStatus.COMPLETED, UnifiedOrderStatus.PARTIAL]
                    ),
                )
            )
        ).scalar_one()
        return int(sms) + int(api)

    @staticmethod
    async def _verify(session, user_id: int, task: Task, bot=None) -> tuple[bool, str]:
        verification = task.verification

        if verification == TaskVerification.NONE:
            return True, ""

        if verification == TaskVerification.ONCE_PER_USER:
            total = (
                await session.execute(
                    select(func.coalesce(func.sum(TaskProgress.completions), 0)).where(
                        TaskProgress.user_id == user_id,
                        TaskProgress.task_id == task.id,
                    )
                )
            ).scalar_one()
            if int(total) > 0:
                return False, "أنجزت هذه المهمة من قبل."
            return True, ""

        if verification == TaskVerification.COOLDOWN_MINUTES:
            try:
                minutes = int(task.verification_target or 0)
            except ValueError:
                minutes = 0
            if minutes <= 0:
                return True, ""
            result = await session.execute(
                select(func.max(TaskProgress.last_completed_at)).where(
                    TaskProgress.user_id == user_id,
                    TaskProgress.task_id == task.id,
                )
            )
            last = result.scalar_one_or_none()
            if last is not None:
                elapsed = (datetime.utcnow() - last).total_seconds() / 60
                if elapsed < minutes:
                    wait = int(minutes - elapsed) + 1
                    return False, f"انتظر {wait} دقيقة قبل تكرار هذه المهمة."
            return True, ""

        if verification == TaskVerification.TELEGRAM_MEMBERSHIP:
            chat_id = (task.verification_target or "").strip()
            if not chat_id:
                return False, "المهمة غير مضبوطة (لا قناة محددة)."
            if bot is None:
                return False, "تعذّر التحقق من العضوية الآن."
            user = await session.get(User, user_id)
            if user is None:
                return False, "المستخدم غير موجود."
            try:
                member = await bot.get_chat_member(int(chat_id), user.telegram_id)
            except Exception:
                return False, "تعذّر التحقق من القناة. تأكد أن البوت أدمن فيها."
            if member.status in ("left", "kicked"):
                return False, "يجب الاشتراك في القناة المطلوبة أولاً."
            return True, ""

        if verification == TaskVerification.PROOF_ORDER:
            try:
                min_value = Decimal(task.verification_target or "0")
            except Exception:
                min_value = Decimal("0")
            sms_value = (
                await session.execute(
                    select(func.coalesce(func.sum(NumberOrder.price_sell_usd), 0)).where(
                        NumberOrder.user_id == user_id,
                        NumberOrder.status.in_(
                            [OrderStatus.COMPLETED, OrderStatus.CODE_RECEIVED]
                        ),
                    )
                )
            ).scalar_one()
            api_value = (
                await session.execute(
                    select(func.coalesce(func.sum(UnifiedOrder.price_usd), 0)).where(
                        UnifiedOrder.user_id == user_id,
                        UnifiedOrder.status.in_(
                            [UnifiedOrderStatus.COMPLETED, UnifiedOrderStatus.PARTIAL]
                        ),
                    )
                )
            ).scalar_one()
            if Decimal(str(sms_value or 0)) + Decimal(str(api_value or 0)) < min_value:
                return False, f"تحتاج طلبات مكتملة بقيمة {min_value}$ على الأقل."
            return True, ""

        if verification == TaskVerification.ADMIN_APPROVAL:
            pending = (
                await session.execute(
                    select(func.count(TaskSubmission.id)).where(
                        TaskSubmission.user_id == user_id,
                        TaskSubmission.task_id == task.id,
                        TaskSubmission.status == "pending",
                    )
                )
            ).scalar_one()
            if int(pending) > 0:
                return False, "لديك تقديم قيد المراجعة، انتظر قرار الأدمن."
            return True, ""

        if verification == TaskVerification.CAPTCHA:
            # التحدي نفسه يُدار في الهاندلر؛ هنا نتحقق من وجود حل صالح محفوظ.
            return True, ""

        if verification == TaskVerification.ONCE_PER_DAY:
            return True, ""

        return True, ""

    # ─────────── الصرف ───────────

    @staticmethod
    async def complete(
        session,
        user_id: int,
        task: Task,
        bot=None,
        captcha_answer: int | None = None,
    ) -> dict:
        """
        يتحقق ويصرف المكافأة. يرجع ملخصاً أو يرمي TaskError.
        """
        if task.verification == TaskVerification.CAPTCHA:
            expected = await FeatureService.config_int("tasks_system", "_captcha", -1)
            # التحدي يُحسب في الهاندلر ويُرسل معه؛ هنا نعيد الحساب بأمان.
        ok, reason = await TaskService.eligibility(session, user_id, task, bot)
        if not ok:
            raise TaskError(reason)

        period_key = TaskService._period_key(task)
        progress = await TaskService._progress(session, user_id, task, period_key)

        if progress is None:
            progress = TaskProgress(
                user_id=user_id,
                task_id=task.id,
                period_key=period_key,
                completions=0,
            )
            session.add(progress)
            await session.flush()

        # حاجز التكرار النهائي: لو وصلت للحد بعد الفحص (سباق بين ضغطتين)
        if task.daily_limit > 0 and period_key.startswith("daily:"):
            if progress.completions >= task.daily_limit:
                raise TaskError("أنجزت هذه المهمة بالحد الأقصى لليوم.")
        elif task.total_limit > 0 and progress.completions >= task.total_limit:
            raise TaskError("أنجزت هذه المهمة بالحد الأقصى المسموح.")

        reward_points = 0
        reward_usd = Decimal("0")

        if task.reward_type == TaskRewardType.POINTS and task.reward_points > 0:
            event_key = f"task:{task.id}:{user_id}:{period_key}:{progress.completions + 1}"
            awarded = await LoyaltyService.award_points(
                session,
                user_id,
                task.reward_points,
                event_key=event_key,
                event_type="task",
                description=f"مكافأة مهمة: {task.title_ar}",
                related_table="tasks_catalog",
                related_id=task.id,
            )
            if not awarded:
                raise TaskError("هذه المكافأة صُرفت مسبقاً.")
            reward_points = task.reward_points

        if task.reward_type == TaskRewardType.BALANCE_USD and task.reward_usd > 0:
            await BalanceService.add_balance(
                session,
                user_id,
                task.reward_usd,
                TransactionType.ADMIN_ADD,
                description=f"مكافأة مهمة: {task.title_ar}",
                related_table="tasks_catalog",
                related_id=task.id,
                payment_reference=f"task:{task.id}:{user_id}:{period_key}:{progress.completions + 1}",
            )
            reward_usd = task.reward_usd

        progress.completions += 1
        progress.points_earned += reward_points
        progress.usd_earned += reward_usd
        progress.last_completed_at = datetime.utcnow()
        await session.commit()

        await FeatureService.track("tasks_system", "completed", user_id=user_id, value=task.key)

        return {
            "task": task,
            "points": reward_points,
            "usd": reward_usd,
            "completions": progress.completions,
        }

    # ─────────── التقديمات التي تحتاج موافقة ───────────

    @staticmethod
    async def submit(session, user_id: int, task: Task, content: str) -> TaskSubmission:
        ok, reason = await TaskService.eligibility(session, user_id, task)
        if not ok:
            raise TaskError(reason)
        submission = TaskSubmission(
            user_id=user_id,
            task_id=task.id,
            content=content[:5000],
            status="pending",
        )
        session.add(submission)
        await session.commit()
        await session.refresh(submission)
        await FeatureService.track("tasks_system", "submitted", user_id=user_id, value=task.key)
        return submission

    @staticmethod
    async def pending_submissions(session, limit: int = 20) -> list[TaskSubmission]:
        result = await session.execute(
            select(TaskSubmission)
            .where(TaskSubmission.status == "pending")
            .order_by(TaskSubmission.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def review_submission(
        session, submission_id: int, admin_id: int, approve: bool, note: str | None = None
    ) -> TaskSubmission | None:
        submission = await session.get(TaskSubmission, submission_id)
        if submission is None or submission.status != "pending":
            return None
        submission.status = "approved" if approve else "rejected"
        submission.admin_note = (note or "")[:255]
        submission.reviewed_by = admin_id
        submission.reviewed_at = datetime.utcnow()
        await session.flush()

        if approve:
            task = await session.get(Task, submission.task_id)
            if task is not None:
                if task.reward_type == TaskRewardType.POINTS and task.reward_points > 0:
                    await LoyaltyService.award_points(
                        session,
                        submission.user_id,
                        task.reward_points,
                        event_key=f"submission:{submission.id}",
                        event_type="task",
                        description=f"مكافأة مهمة مقبولة: {task.title_ar}",
                        related_table="task_submissions",
                        related_id=submission.id,
                    )
                elif task.reward_type == TaskRewardType.BALANCE_USD and task.reward_usd > 0:
                    await BalanceService.add_balance(
                        session,
                        submission.user_id,
                        task.reward_usd,
                        TransactionType.ADMIN_ADD,
                        description=f"مكافأة مهمة مقبولة: {task.title_ar}",
                        related_table="task_submissions",
                        related_id=submission.id,
                        payment_reference=f"submission:{submission.id}",
                    )
                progress = await TaskService._progress(
                    session, submission.user_id, task, "total"
                )
                if progress is None:
                    progress = TaskProgress(
                        user_id=submission.user_id,
                        task_id=task.id,
                        period_key="total",
                        completions=0,
                    )
                    session.add(progress)
                progress.completions += 1
                progress.last_completed_at = datetime.utcnow()
        await session.commit()
        return submission

    # ─────────── إحصاءات ───────────

    @staticmethod
    async def user_summary(session, user_id: int) -> dict:
        total_points = (
            await session.execute(
                select(func.coalesce(func.sum(LoyaltyEvent.points), 0)).where(
                    LoyaltyEvent.user_id == user_id,
                    LoyaltyEvent.event_type == "task",
                )
            )
        ).scalar_one()
        done = (
            await session.execute(
                select(func.coalesce(func.sum(TaskProgress.completions), 0)).where(
                    TaskProgress.user_id == user_id
                )
            )
        ).scalar_one()
        return {"points_from_tasks": int(total_points or 0), "tasks_completed": int(done or 0)}

    # ─────────── بذور افتراضية ───────────

    DEFAULT_TASKS: tuple[dict, ...] = (
        {
            "key": "daily_checkin",
            "title_ar": "تسجيل الحضور اليومي",
            "emoji": "📅",
            "task_type": TaskType.DAILY_CHECKIN,
            "verification": TaskVerification.ONCE_PER_DAY,
            "reward_points": 25,
            "daily_limit": 1,
        },
        {
            "key": "first_deposit",
            "title_ar": "أول عملية شحن رصيد",
            "emoji": "💳",
            "task_type": TaskType.FIRST_DEPOSIT,
            "verification": TaskVerification.PROOF_ORDER,
            "verification_target": "1",
            "reward_points": 200,
            "daily_limit": 0,
            "total_limit": 1,
        },
        {
            "key": "review_product",
            "title_ar": "قيّم منتجاً اشتريته",
            "emoji": "⭐",
            "task_type": TaskType.REVIEW_PRODUCT,
            "verification": TaskVerification.PROOF_ORDER,
            "verification_target": "0.5",
            "reward_points": 40,
            "daily_limit": 2,
        },
        {
            "key": "join_channel",
            "title_ar": "اشترك في قناتنا",
            "emoji": "📢",
            "task_type": TaskType.JOIN_CHANNEL,
            "verification": TaskVerification.TELEGRAM_MEMBERSHIP,
            "reward_points": 100,
            "daily_limit": 0,
            "total_limit": 1,
        },
        {
            "key": "invite_friend",
            "title_ar": "ادعُ صديقاً وفعّل حسابك",
            "emoji": "🤝",
            "task_type": TaskType.INVITE_FRIEND,
            "verification": TaskVerification.COOLDOWN_MINUTES,
            "verification_target": "60",
            "reward_points": 150,
            "daily_limit": 5,
        },
        {
            "key": "report_provider",
            "title_ar": "أبلغ عن مزود سيء",
            "emoji": "🚨",
            "task_type": TaskType.REPORT_PROVIDER,
            "verification": TaskVerification.ADMIN_APPROVAL,
            "reward_points": 300,
            "daily_limit": 3,
        },
        {
            "key": "translate_text",
            "title_ar": "ساهم بترجمة نص",
            "emoji": "🌐",
            "task_type": TaskType.TRANSLATE_TEXT,
            "verification": TaskVerification.ADMIN_APPROVAL,
            "reward_points": 250,
            "daily_limit": 3,
        },
    )

    @staticmethod
    async def seed_defaults(session) -> int:
        """يضيف المهام الافتراضية إن لم تكن موجودة. لا يمسّ ما عدّله الأدمن."""
        created = 0
        for spec in TaskService.DEFAULT_TASKS:
            existing = await TaskService.get_by_key(session, spec["key"])
            if existing is not None:
                continue
            session.add(Task(**spec))
            created += 1
        if created:
            await session.commit()
        return created
