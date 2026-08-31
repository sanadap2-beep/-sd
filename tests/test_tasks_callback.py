from types import SimpleNamespace

from decimal import Decimal

import pytest

from database.models import TaskType, TaskVerification


@pytest.mark.asyncio
async def test_completed_task_refreshes_home_with_handler_dependencies(monkeypatch):
    from handlers import tasks as tasks_handler

    task = SimpleNamespace(
        id=1,
        is_active=True,
        task_type=TaskType.CUSTOM,
        verification=TaskVerification.NONE,
        emoji="🎯",
        title_ar="مهمة تجريبية",
        description_ar=None,
    )

    class FakeSession:
        async def get(self, _model, _task_id):
            return task

    class FakeMessage:
        async def answer(self, *_args, **_kwargs):
            return None

    class FakeCallback:
        data = "task_do:1"
        bot = object()
        message = FakeMessage()

        async def answer(self, *_args, **_kwargs):
            return None

    async def complete(_session, _user_id, _task, bot=None):
        assert bot is not None
        return {"points": 5, "usd": Decimal("0")}

    refreshed_with = []

    async def refresh_home(callback, session, db_user):
        refreshed_with.append((callback, session, db_user))

    monkeypatch.setattr(tasks_handler.TaskService, "complete", staticmethod(complete))
    monkeypatch.setattr(tasks_handler, "tasks_home", refresh_home)

    callback = FakeCallback()
    session = FakeSession()
    db_user = SimpleNamespace(id=42, language_code="ar")

    await tasks_handler.task_do(callback, object(), session, db_user, callback.bot)

    assert refreshed_with == [(callback, session, db_user)]
