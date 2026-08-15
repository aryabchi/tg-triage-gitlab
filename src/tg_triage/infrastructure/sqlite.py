"""SQLite adapter for Problems and TriageRuns.

One local file is the system of record. Tests pass a temp path; there is no
database server. Column names ``telegram_chat_id`` / ``telegram_message_id``
are the unique source-message key; domain objects still use ``chat_id`` /
``message_id``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import cast

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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS problems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_text TEXT NOT NULL,
    telegram_chat_id INTEGER NOT NULL,
    telegram_message_id INTEGER NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    linked_issue TEXT,
    UNIQUE (telegram_chat_id, telegram_message_id)
);

CREATE TABLE IF NOT EXISTS triage_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    since_date TEXT NOT NULL,
    owner_user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    generated_markdown BLOB,
    uploaded_markdown BLOB,
    items_json TEXT NOT NULL DEFAULT '[]',
    execution_result_json TEXT,
    created_at TEXT
);
"""


def _isoformat(value: datetime) -> str:
    return value.isoformat()


def _parse_datetime(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _blob(value: bytes | None) -> bytes | None:
    return None if value is None else bytes(value)


def _items_to_json(items: tuple[TriageItem, ...]) -> str:
    payload = [
        {
            "problem_ids": list(item.problem_ids),
            "outcome": item.outcome.value,
            "title": item.title,
            "body": item.body,
            "rationale": item.rationale,
            "repository": str(item.repository) if item.repository else None,
            "existing_issue": str(item.existing_issue) if item.existing_issue else None,
            "priority": item.priority,
        }
        for item in items
    ]
    return json.dumps(payload, ensure_ascii=False)


def _items_from_json(raw: str) -> tuple[TriageItem, ...]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("items_json must be a list")
    return tuple(_item_from_mapping(item) for item in data)


def _item_from_mapping(raw: object) -> TriageItem:
    if not isinstance(raw, dict):
        raise ValueError("triage item JSON must be an object")
    item = cast(Mapping[str, object], raw)
    repository_raw = item.get("repository")
    existing_raw = item.get("existing_issue")
    problem_ids_raw = item.get("problem_ids", [])
    if not isinstance(problem_ids_raw, list):
        raise ValueError("problem_ids must be a list")
    return TriageItem(
        problem_ids=tuple(int(pid) for pid in problem_ids_raw),
        outcome=TriageOutcome(str(item["outcome"])),
        title=str(item.get("title", "")),
        body=str(item.get("body", "")),
        rationale=str(item.get("rationale", "")),
        repository=RepositoryId.parse(str(repository_raw)) if repository_raw else None,
        existing_issue=IssueRef.parse(str(existing_raw)) if existing_raw else None,
        priority=str(item["priority"]) if item.get("priority") is not None else None,
    )


def _execution_to_json(result: ExecutionResult | None) -> str | None:
    if result is None:
        return None
    payload = {
        "items": [
            {
                "action": row.action.value,
                "issue": str(row.issue),
                "problem_ids": list(row.problem_ids),
                "url": row.url,
            }
            for row in result.items
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


def _execution_from_json(raw: str | None) -> ExecutionResult | None:
    if raw is None:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("execution_result_json must be an object")
    items_raw = data.get("items", [])
    if not isinstance(items_raw, list):
        raise ValueError("execution items must be a list")
    items: list[ExecutionItemResult] = []
    for row in items_raw:
        if not isinstance(row, dict):
            raise ValueError("execution item JSON must be an object")
        mapping = cast(Mapping[str, object], row)
        problem_ids_raw = mapping.get("problem_ids", [])
        if not isinstance(problem_ids_raw, list):
            raise ValueError("problem_ids must be a list")
        url_raw = mapping.get("url")
        items.append(
            ExecutionItemResult(
                action=ExecutionAction(str(mapping["action"])),
                issue=IssueRef.parse(str(mapping["issue"])),
                problem_ids=tuple(int(pid) for pid in problem_ids_raw),
                url=str(url_raw) if url_raw is not None else None,
            )
        )
    return ExecutionResult(items=tuple(items))


def _problem_from_row(row: sqlite3.Row) -> Problem:
    linked = row["linked_issue"]
    return Problem(
        id=int(row["id"]),
        original_text=str(row["original_text"]),
        chat_id=int(row["telegram_chat_id"]),
        message_id=int(row["telegram_message_id"]),
        user_id=int(row["telegram_user_id"]),
        created_at=_parse_datetime(str(row["created_at"])),
        lifecycle=ProblemLifecycle(str(row["lifecycle"])),
        linked_issue=IssueRef.parse(str(linked)) if linked else None,
    )


def _run_from_row(row: sqlite3.Row) -> TriageRun:
    created_raw = row["created_at"]
    return TriageRun(
        id=int(row["id"]),
        since_date=date.fromisoformat(str(row["since_date"])),
        owner_user_id=int(row["owner_user_id"]),
        status=TriageRunStatus(str(row["status"])),
        generated_markdown=_blob(row["generated_markdown"]),
        uploaded_markdown=_blob(row["uploaded_markdown"]),
        items=_items_from_json(str(row["items_json"])),
        execution_result=_execution_from_json(row["execution_result_json"]),
        created_at=_parse_datetime(str(created_raw)) if created_raw else None,
    )


class SqliteProblemRepository:
    """Problem store backed by a shared SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def insert(self, problem: Problem) -> Problem:
        """Persist a report; duplicate chat+message returns the existing row.

        When ``problem.id`` is greater than 0, that id is stored so tests can
        use known Problem numbers. Otherwise SQLite assigns the id.
        """
        columns = """
            original_text, telegram_chat_id, telegram_message_id,
            telegram_user_id, created_at, lifecycle, linked_issue
        """
        values = (
            problem.original_text,
            problem.chat_id,
            problem.message_id,
            problem.user_id,
            _isoformat(problem.created_at),
            problem.lifecycle.value,
            str(problem.linked_issue) if problem.linked_issue else None,
        )
        with self._conn:
            if problem.id > 0:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO problems (id, {columns}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (problem.id, *values),
                )
            else:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO problems ({columns}) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
        stored = self._get_by_message(problem.chat_id, problem.message_id)
        if stored is None:
            raise RuntimeError("insert did not persist a problem row")
        return stored

    def get(self, problem_id: int) -> Problem | None:
        """Return the report with this id, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM problems WHERE id = ?", (problem_id,)
        ).fetchone()
        return _problem_from_row(row) if row else None

    def list_ingested_since(self, since: datetime) -> tuple[Problem, ...]:
        """Ingested reports at or after ``since``, oldest first."""
        rows = self._conn.execute(
            """
            SELECT * FROM problems
            WHERE lifecycle = ? AND created_at >= ?
            ORDER BY created_at, id
            """,
            (ProblemLifecycle.INGESTED.value, _isoformat(since)),
        ).fetchall()
        return tuple(_problem_from_row(row) for row in rows)

    def save(self, problem: Problem) -> None:
        """Update lifecycle and linked issue for an existing report."""
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE problems
                SET lifecycle = ?, linked_issue = ?
                WHERE id = ?
                """,
                (
                    problem.lifecycle.value,
                    str(problem.linked_issue) if problem.linked_issue else None,
                    problem.id,
                ),
            )
        if cursor.rowcount == 0:
            raise KeyError(problem.id)

    def _get_by_message(self, chat_id: int, message_id: int) -> Problem | None:
        row = self._conn.execute(
            """
            SELECT * FROM problems
            WHERE telegram_chat_id = ? AND telegram_message_id = ?
            """,
            (chat_id, message_id),
        ).fetchone()
        return _problem_from_row(row) if row else None


class SqliteTriageRunRepository:
    """Triage-run store backed by a shared SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def insert(self, run: TriageRun) -> TriageRun:
        """Persist a run and return it with the assigned id."""
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO triage_runs (
                    since_date, owner_user_id, status, generated_markdown,
                    uploaded_markdown, items_json, execution_result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.since_date.isoformat(),
                    run.owner_user_id,
                    run.status.value,
                    run.generated_markdown,
                    run.uploaded_markdown,
                    _items_to_json(run.items),
                    _execution_to_json(run.execution_result),
                    _isoformat(run.created_at) if run.created_at else None,
                ),
            )
        stored = self.get(int(cursor.lastrowid))
        if stored is None:
            raise RuntimeError("insert did not persist a triage run")
        return stored

    def get(self, run_id: int) -> TriageRun | None:
        """Return the run with this id, or ``None``."""
        row = self._conn.execute(
            "SELECT * FROM triage_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return _run_from_row(row) if row else None

    def save(self, run: TriageRun) -> None:
        """Replace mutable run fields. Markdown bytes are stored as BLOBs."""
        with self._conn:
            cursor = self._conn.execute(
                """
                UPDATE triage_runs
                SET status = ?, generated_markdown = ?, uploaded_markdown = ?,
                    items_json = ?, execution_result_json = ?
                WHERE id = ?
                """,
                (
                    run.status.value,
                    run.generated_markdown,
                    run.uploaded_markdown,
                    _items_to_json(run.items),
                    _execution_to_json(run.execution_result),
                    run.id,
                ),
            )
        if cursor.rowcount == 0:
            raise KeyError(run.id)

    def supersede_open(self) -> None:
        """Mark pending and awaiting runs superseded in one transaction."""
        with self._conn:
            self._conn.execute(
                """
                UPDATE triage_runs
                SET status = ?
                WHERE status IN (?, ?)
                """,
                (
                    TriageRunStatus.SUPERSEDED.value,
                    TriageRunStatus.PENDING.value,
                    TriageRunStatus.AWAITING_EXECUTE.value,
                ),
            )

    def list_by_status(self, *statuses: TriageRunStatus) -> tuple[TriageRun, ...]:
        """Runs in any of ``statuses``, oldest first."""
        if not statuses:
            return ()
        placeholders = ",".join("?" for _ in statuses)
        rows = self._conn.execute(
            f"SELECT * FROM triage_runs WHERE status IN ({placeholders}) ORDER BY id",
            tuple(status.value for status in statuses),
        ).fetchall()
        return tuple(_run_from_row(row) for row in rows)


class SqliteDb:
    """One SQLite file exposing both repositories on a shared connection."""

    def __init__(self, path: Path | str) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self.problems = SqliteProblemRepository(self._conn)
        self.runs = SqliteTriageRunRepository(self._conn)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> SqliteDb:
        """Return this store for ``with SqliteDb(...) as db``."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close on leaving the context, including when an error is raised."""
        self.close()
