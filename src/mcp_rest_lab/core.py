"""Application facade over learner-state-core.

This module is the single source of truth behind both MCP and REST. It owns the
serving configuration, calls learner-state-core's query service, and maps the
upstream 0.x models into stable app DTOs.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from learner_state_core.serving.reader import FileSystemStateReader
from learner_state_core.serving.service import LearnerStateQueryService
from learner_state_core.serving.status import StudentStatus
from pydantic import BaseModel, ConfigDict

DEFAULT_LEARNER_STATE_ROOT = "learner_state_data"
LEARNER_STATE_ROOT_ENV = "LEARNER_STATE_ROOT"


class ChannelStatusResponse(BaseModel):
    """Stable per-channel shape for REST/MCP consumers."""

    model_config = ConfigDict(extra="forbid")

    state: str
    last_activity_at: datetime | None = None
    units_completed: int = 0
    units_in_progress: int = 0
    units_dropped: int = 0
    accuracy: float | None = None
    total_time_seconds: int = 0
    tasks_completed: int = 0
    tasks_total: int = 0
    lesson_count: int = 0
    first_lesson_at: datetime | None = None
    last_lesson_at: datetime | None = None
    level: str | None = None
    trend: str | None = None


class StudentStatusResponse(BaseModel):
    """Wire-safe learner status owned by this service."""

    model_config = ConfigDict(extra="forbid")

    student_id: str
    name: str
    status_label: str
    overall_state: str
    engagement_mode: str
    as_of: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None
    last_activity_at: datetime | None = None
    self_study: ChannelStatusResponse
    online: ChannelStatusResponse
    summary: str


def learner_state_root() -> Path:
    """Return the configured learner-state artifact root."""
    return Path(os.environ.get(LEARNER_STATE_ROOT_ENV, DEFAULT_LEARNER_STATE_ROOT))


@lru_cache(maxsize=8)
def _service(root_path: str) -> LearnerStateQueryService:
    reader = FileSystemStateReader(root_path)
    return LearnerStateQueryService(reader)


def reset_service_cache() -> None:
    """Clear cached readers; useful when tests change LEARNER_STATE_ROOT."""
    _service.cache_clear()


def query_service() -> LearnerStateQueryService:
    return _service(str(learner_state_root()))


def list_student_ids() -> list[str]:
    return query_service().reader.list_student_ids()


def window_from_days(
    window_days: int | None,
    *,
    as_of: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Build a UTC recency window from a day count."""
    if window_days is None:
        return None
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    resolved_as_of = as_of or datetime.now(timezone.utc)
    if resolved_as_of.tzinfo is None:
        resolved_as_of = resolved_as_of.replace(tzinfo=timezone.utc)
    return resolved_as_of - timedelta(days=window_days), resolved_as_of


def get_student_status(
    student_id: str,
    *,
    window: tuple[datetime, datetime] | None = None,
    as_of: datetime | None = None,
) -> StudentStatusResponse:
    """Return structured status for one student."""
    status = query_service().student_status(student_id, window=window, as_of=as_of)
    return to_response(status)


def get_group_status(
    student_ids: list[str],
    *,
    window: tuple[datetime, datetime] | None = None,
    as_of: datetime | None = None,
) -> list[StudentStatusResponse]:
    """Return structured statuses for a requested student set."""
    cohort = query_service().cohort_status(student_ids, window=window, as_of=as_of)
    return [to_response(status) for status in cohort.students]


def to_response(status: StudentStatus) -> StudentStatusResponse:
    window_start, window_end = status.window or (None, None)
    return StudentStatusResponse(
        student_id=status.student_id,
        name=status.name,
        status_label=status.status_label,
        overall_state=status.overall_state,
        engagement_mode=status.engagement_mode,
        as_of=status.as_of,
        window_start=window_start,
        window_end=window_end,
        last_activity_at=status.last_activity_at,
        self_study=ChannelStatusResponse(
            state=status.self_study.state,
            last_activity_at=status.self_study.last_activity_at,
            units_completed=status.self_study.units_completed,
            units_in_progress=status.self_study.units_in_progress,
            units_dropped=status.self_study.units_dropped,
            accuracy=status.self_study.accuracy,
            total_time_seconds=status.self_study.total_time_seconds,
            tasks_completed=status.self_study.tasks_completed,
            tasks_total=status.self_study.tasks_total,
        ),
        online=ChannelStatusResponse(
            state=status.online.state,
            lesson_count=status.online.lesson_count,
            first_lesson_at=status.online.first_lesson_at,
            last_lesson_at=status.online.last_lesson_at,
            level=status.online.level,
            trend=status.online.trend,
        ),
        summary=status.status_detail,
    )
