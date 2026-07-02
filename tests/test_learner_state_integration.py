from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from learner_state_core.models.self_study_units import (
    SelfStudyUnitSummary,
    StudentStudyLedger,
)
from learner_state_core.serving.writer import FileSystemStateWriter

from mcp_rest_lab import core
from mcp_rest_lab.api import app
from mcp_rest_lab.auth import make_token

AS_OF = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)


def _unit_summary(
    unit_id: str,
    student_id: str,
    *,
    finished_at: datetime,
) -> SelfStudyUnitSummary:
    return SelfStudyUnitSummary(
        course_id="course-1",
        level_id="level-1",
        unit_id=unit_id,
        student_id=student_id,
        started_at=finished_at - timedelta(hours=1),
        finished_at=finished_at,
        activity_count=3,
        student_completed_tasks=3,
        num_aggregated_tasks=3,
        pct_completed_tasks=100.0,
        student_correct_attempts=2,
        student_total_attempts=3,
        student_total_time_spent_seconds=1800,
        narrative_summary="Completed the unit.",
    )


def _write_self_study_student(
    root: Path,
    student_id: str,
    *,
    finished_at: datetime,
) -> None:
    summary = _unit_summary("unit-1", student_id, finished_at=finished_at)
    ledger = StudentStudyLedger(student_id=student_id)
    ledger.add_unit(summary)
    FileSystemStateWriter(root).write_student(
        student_id,
        study_ledger=ledger,
        unit_summaries=[summary],
    )


def _configure_root(monkeypatch, root: Path) -> None:
    monkeypatch.setenv(core.LEARNER_STATE_ROOT_ENV, str(root))
    core.reset_service_cache()


def test_core_maps_real_learner_state_to_service_dto(tmp_path: Path, monkeypatch) -> None:
    _write_self_study_student(
        tmp_path,
        "hannibal",
        finished_at=AS_OF - timedelta(days=1),
    )
    _configure_root(monkeypatch, tmp_path)

    status = core.get_student_status(
        "hannibal",
        as_of=AS_OF,
        window=core.window_from_days(30, as_of=AS_OF),
    )

    assert status.student_id == "hannibal"
    assert status.status_label == "Active"
    assert status.engagement_mode == "self_study_only"
    assert status.self_study.units_completed == 1
    assert status.self_study.accuracy == 200 / 3
    assert status.online.state == "absent"


def test_rest_allows_teacher_for_claim_scoped_students(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_self_study_student(
        tmp_path,
        "hannibal",
        finished_at=AS_OF - timedelta(days=1),
    )
    _configure_root(monkeypatch, tmp_path)
    token = make_token("teacher_001", "teacher", students=["hannibal"])

    response = TestClient(app).post(
        "/students/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"student_ids": ["hannibal"], "window_days": 30},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["student_id"] == "hannibal"
    assert payload["results"][0]["status_label"] == "Active"


def test_rest_rejects_teacher_out_of_scope_student(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_root(monkeypatch, tmp_path)
    token = make_token("teacher_001", "teacher", students=["hannibal"])

    response = TestClient(app).post(
        "/students/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"student_ids": ["face"], "window_days": 30},
    )

    assert response.status_code == 403


def test_rest_rejects_student_querying_someone_else(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _configure_root(monkeypatch, tmp_path)
    token = make_token("hannibal", "student")

    response = TestClient(app).post(
        "/students/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"student_ids": ["face"], "window_days": 30},
    )

    assert response.status_code == 403
