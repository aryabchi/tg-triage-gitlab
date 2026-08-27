"""Execute confirmed Markdown against a fake tracker: preview, create, skip, retry."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from tg_triage.application.execute import ExecutionService
from tg_triage.config import DEFAULT_YAML_PATH, load_yaml_config
from tg_triage.domain import (
    IssueRef,
    Problem,
    ProblemLifecycle,
    TriageRun,
    TriageRunStatus,
)
from tg_triage.markdown import PlanValidationError
from tests.fakes import FakeIssueTracker, FakeProblemRepository, FakeTriageRunRepository

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "contract_example.md"
ALLOWED = load_yaml_config(DEFAULT_YAML_PATH).repositories
CREATED_AT = datetime(2026, 8, 13, 10, 0, 0)


def _seed_problems(repo: FakeProblemRepository) -> None:
    for problem_id in (101, 102, 103, 104):
        repo.add(
            Problem(
                id=problem_id,
                original_text=f"problem {problem_id}",
                message_id=problem_id,
                chat_id=1,
                user_id=7,
                created_at=CREATED_AT,
            )
        )


def _service(
    tracker: FakeIssueTracker | None = None,
) -> tuple[ExecutionService, FakeIssueTracker, FakeProblemRepository, FakeTriageRunRepository]:
    problems = FakeProblemRepository()
    _seed_problems(problems)
    runs = FakeTriageRunRepository()
    tracker = tracker or FakeIssueTracker()
    service = ExecutionService(tracker, problems, runs, ALLOWED)
    return service, tracker, problems, runs


def _awaiting_run(runs: FakeTriageRunRepository, markdown: bytes) -> TriageRun:
    return runs.insert(
        TriageRun(
            id=0,
            since_date=date(2026, 8, 13),
            owner_user_id=42,
            status=TriageRunStatus.AWAITING_EXECUTE,
            uploaded_markdown=markdown,
        )
    )


def test_preview_makes_zero_tracker_calls() -> None:
    service, tracker, _problems, _runs = _service()
    service.preview(GOLDEN.read_bytes())
    assert tracker.calls == []


def test_confirm_creates_once_skips_and_leaves_uncertain() -> None:
    service, tracker, problems, runs = _service()
    run = _awaiting_run(runs, GOLDEN.read_bytes())
    executed = service.execute_confirmed(run)
    assert len(tracker.calls) == 1
    call = tracker.calls[0]
    assert call.title == "Sales dashboard export hangs"
    assert "2 users" in call.body
    assert executed.execution_result is not None
    assert len(executed.execution_result.items) == 2
    assert problems.get(101).lifecycle == ProblemLifecycle.LINKED
    assert problems.get(102).lifecycle == ProblemLifecycle.LINKED
    assert problems.get(104).lifecycle == ProblemLifecycle.LINKED
    assert problems.get(104).linked_issue == IssueRef.parse("acme/sales-dashboard#81")
    assert problems.get(103).lifecycle == ProblemLifecycle.INGESTED
    assert problems.get(103).linked_issue is None

    service.execute_confirmed(run)
    assert len(tracker.calls) == 1


def test_invalid_markdown_creates_nothing() -> None:
    service, tracker, _problems, runs = _service()
    run = _awaiting_run(
        runs,
        GOLDEN.read_text(encoding="utf-8").replace("## Skip", "## Foo").encode("utf-8"),
    )
    with pytest.raises(PlanValidationError):
        service.execute_confirmed(run)
    assert tracker.calls == []


def test_cancel_does_not_write() -> None:
    service, tracker, problems, runs = _service()
    run = _awaiting_run(runs, GOLDEN.read_bytes())
    cancelled = service.cancel(run)
    assert cancelled.status == TriageRunStatus.CANCELLED
    assert tracker.calls == []
    assert problems.get(101).lifecycle == ProblemLifecycle.INGESTED


def test_partial_failure_keeps_succeeded_url() -> None:
    markdown = """\
# GitHub Issues
# run: 1
# since: 2026-08-13

## Legend

x

## Create

### First
- Repo: acme/crm
- Problems: #101
- Body: |
    first

### Second
- Repo: acme/crm
- Problems: #102
- Body: |
    second
"""
    tracker = FakeIssueTracker(fail_after=1)
    service, tracker, problems, runs = _service(tracker)
    run = _awaiting_run(runs, markdown.encode("utf-8"))
    with pytest.raises(RuntimeError, match="tracker create failed"):
        service.execute_confirmed(run)
    assert len(tracker.calls) == 1
    stored = runs.get(run.id)
    assert stored is not None
    assert stored.execution_result is not None
    assert len(stored.execution_result.items) == 1
    assert stored.execution_result.items[0].url is not None
    assert problems.get(101).lifecycle == ProblemLifecycle.LINKED
    assert problems.get(102).lifecycle == ProblemLifecycle.INGESTED

    tracker.fail_after = None
    retried = service.execute_confirmed(run)
    assert len(tracker.calls) == 2
    assert tracker.calls[1].title == "Second"
    assert problems.get(102).lifecycle == ProblemLifecycle.LINKED
    assert retried.status == TriageRunStatus.EXECUTED
    assert retried.execution_result is not None
    assert len(retried.execution_result.items) == 2
