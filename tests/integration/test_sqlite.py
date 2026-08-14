"""SQLite persistence against a temp file: uniqueness, filters, bytes, status."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from tg_triage.domain import (
    ExecutionAction,
    ExecutionItemResult,
    ExecutionResult,
    IssueRef,
    Problem,
    ProblemLifecycle,
    RepositoryId,
    TriageItem,
    TriageOutcome,
    TriageRun,
    TriageRunStatus,
)
from tg_triage.infrastructure.sqlite import SqliteDb


def _problem(
    *,
    message_id: int,
    created_at: datetime,
    text: str = "Экспорт дашборда продаж больше не работает.",
    chat_id: int = 100,
    lifecycle: ProblemLifecycle = ProblemLifecycle.INGESTED,
    linked_issue: IssueRef | None = None,
) -> Problem:
    return Problem(
        id=0,
        original_text=text,
        message_id=message_id,
        chat_id=chat_id,
        user_id=7,
        created_at=created_at,
        lifecycle=lifecycle,
        linked_issue=linked_issue,
    )


def _run(*, status: TriageRunStatus = TriageRunStatus.PENDING) -> TriageRun:
    return TriageRun(
        id=0,
        since_date=date(2026, 8, 13),
        owner_user_id=42,
        status=status,
        created_at=datetime(2026, 8, 14, 9, 0, 0),
    )


def test_insert_problem(tmp_path: Path) -> None:
    with SqliteDb(tmp_path / "store.sqlite") as db:
        created = datetime(2026, 8, 13, 10, 0, 0)
        stored = db.problems.insert(_problem(message_id=11, created_at=created))
        loaded = db.problems.get(stored.id)
        assert loaded is not None
        assert loaded.id == stored.id
        assert loaded.original_text == "Экспорт дашборда продаж больше не работает."
        assert loaded.lifecycle == ProblemLifecycle.INGESTED
        assert loaded.linked_issue is None
        assert loaded.message_id == 11
        assert loaded.chat_id == 100


def test_duplicate_message_id_is_idempotent(tmp_path: Path) -> None:
    with SqliteDb(tmp_path / "store.sqlite") as db:
        first = db.problems.insert(
            _problem(message_id=11, created_at=datetime(2026, 8, 13, 10, 0, 0), text="first")
        )
        second = db.problems.insert(
            _problem(message_id=11, created_at=datetime(2026, 8, 13, 11, 0, 0), text="second")
        )
        assert second.id == first.id
        assert second.original_text == "first"
        assert db.problems.get(first.id) == first


def test_ingested_since_excludes_linked_and_too_old(tmp_path: Path) -> None:
    with SqliteDb(tmp_path / "store.sqlite") as db:
        since = datetime(2026, 8, 13, 0, 0, 0)
        old = db.problems.insert(
            _problem(message_id=1, created_at=datetime(2026, 8, 12, 23, 0, 0), text="old")
        )
        ingested = db.problems.insert(
            _problem(message_id=2, created_at=datetime(2026, 8, 13, 8, 0, 0), text="in")
        )
        linked = db.problems.insert(
            _problem(
                message_id=3,
                created_at=datetime(2026, 8, 13, 9, 0, 0),
                text="linked",
                lifecycle=ProblemLifecycle.LINKED,
                linked_issue=IssueRef.parse("acme/crm#1"),
            )
        )
        found = db.problems.list_ingested_since(since)
        assert [row.id for row in found] == [ingested.id]
        assert old.id not in {row.id for row in found}
        assert linked.id not in {row.id for row in found}


def test_upload_bytes_round_trip(tmp_path: Path) -> None:
    with SqliteDb(tmp_path / "store.sqlite") as db:
        stored = db.runs.insert(_run())
        payload = "# GitHub Issues\n# run: 7\n".encode("utf-8")
        db.runs.save(replace(stored, uploaded_markdown=payload))
        loaded = db.runs.get(stored.id)
        assert loaded is not None
        assert loaded.uploaded_markdown == payload
        assert loaded.generated_markdown is None


def test_supersede_open_is_transactional(tmp_path: Path) -> None:
    with SqliteDb(tmp_path / "store.sqlite") as db:
        pending = db.runs.insert(_run(status=TriageRunStatus.PENDING))
        awaiting = db.runs.insert(_run(status=TriageRunStatus.AWAITING_EXECUTE))
        executed = db.runs.insert(_run(status=TriageRunStatus.EXECUTED))
        db.runs.supersede_open()
        assert db.runs.get(pending.id).status == TriageRunStatus.SUPERSEDED
        assert db.runs.get(awaiting.id).status == TriageRunStatus.SUPERSEDED
        assert db.runs.get(executed.id).status == TriageRunStatus.EXECUTED
        still_open = db.runs.list_by_status(
            TriageRunStatus.PENDING, TriageRunStatus.AWAITING_EXECUTE
        )
        assert still_open == ()


def test_execution_result_json_round_trip(tmp_path: Path) -> None:
    with SqliteDb(tmp_path / "store.sqlite") as db:
        stored = db.runs.insert(_run())
        item = TriageItem(
            problem_ids=(101, 102),
            outcome=TriageOutcome.CREATE_NEW,
            title="Sales dashboard export hangs",
            repository=RepositoryId.parse("acme/sales-dashboard"),
        )
        result = ExecutionResult(
            items=(
                ExecutionItemResult(
                    action=ExecutionAction.CREATE,
                    issue=IssueRef.parse("acme/sales-dashboard#12"),
                    problem_ids=(101, 102),
                    url="https://example.test/issues/12",
                ),
            )
        )
        db.runs.save(replace(stored, items=(item,), execution_result=result))
        loaded = db.runs.get(stored.id)
        assert loaded is not None
        assert loaded.items == (item,)
        assert loaded.execution_result == result
