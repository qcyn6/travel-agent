"""Serialize short job admission transactions across API processes."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
from threading import RLock

from sqlalchemy import func, select, text

from backend.database.tables import ConversationRow, PlanningJobRow


_local_lock = RLock()


class PlanningCapacityError(RuntimeError):
    def __init__(self, message: str, retry_after: int = 10):
        super().__init__(message)
        self.retry_after = retry_after


@contextmanager
def admission_lock(session):
    engine = session.get_bind()
    if engine.dialect.name != "mysql":
        with _local_lock:
            yield
        return
    # Dedicated connection: a session commit must not return the named-lock
    # owner to the pool before RELEASE_LOCK executes on that same connection.
    name = "travel:admission:" + hashlib.sha256((engine.url.database or "").encode()).hexdigest()[:24]
    with engine.connect() as connection:
        if connection.scalar(text("SELECT GET_LOCK(:name, 3)"), {"name": name}) != 1:
            raise PlanningCapacityError("规划服务繁忙，请稍后再试")
        try:
            yield
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})


def check_admission(session, settings, owner_id):
    active = PlanningJobRow.status.in_(["queued", "running"])
    total = session.scalar(select(func.count()).select_from(PlanningJobRow).where(active))
    if total >= getattr(settings, "planning_max_active_jobs", 4):
        raise PlanningCapacityError("当前规划任务较多，请稍后再试")
    recent_start = datetime.now() - timedelta(days=1)
    daily_total = session.scalar(select(func.count()).select_from(PlanningJobRow).where(
        PlanningJobRow.created_at >= recent_start))
    if daily_total >= getattr(settings, "planning_jobs_per_day", 50):
        raise PlanningCapacityError("平台最近24小时的规划额度已达上限，请稍后再试", retry_after=3600)
    owned = select(func.count()).select_from(PlanningJobRow).join(
        ConversationRow, PlanningJobRow.conversation_id == ConversationRow.id
    ).where(ConversationRow.user_id == owner_id)
    count = session.scalar(owned.where(active))
    if count >= getattr(settings, "planning_max_active_jobs_per_user", 1):
        raise PlanningCapacityError("你已有规划任务正在执行，请完成后再启动新任务")
    recent = session.scalar(owned.where(PlanningJobRow.created_at >= recent_start))
    if recent >= getattr(settings, "planning_jobs_per_user_per_day", 10):
        raise PlanningCapacityError("最近24小时的规划次数已达上限，请稍后再试", retry_after=3600)
