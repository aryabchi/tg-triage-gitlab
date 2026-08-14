"""Ports for the local store of Problems and TriageRuns.

Application services depend on these protocols, not on SQLite. Implementations
must keep unique ``(chat_id, message_id)`` and treat duplicate inserts as
idempotent (return the existing row).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tg_triage.domain import Problem, TriageRun, TriageRunStatus


class ProblemRepository(Protocol):
    """Accumulation store for original reports."""

    def insert(self, problem: Problem) -> Problem:
        """Persist a report and return the stored row, including assigned ``id``.

        The incoming ``id`` is ignored. A second insert with the same
        ``chat_id`` and ``message_id`` returns the existing row unchanged.
        """

    def get(self, problem_id: int) -> Problem | None:
        """Return the report with this id, or ``None`` if it does not exist."""

    def list_ingested_since(self, since: datetime) -> tuple[Problem, ...]:
        """Reports with ``created_at >= since`` and lifecycle ``ingested``.

        Linked reports and older rows are omitted. Order is ``created_at``,
        then ``id``.
        """

    def save(self, problem: Problem) -> None:
        """Update lifecycle and linked issue for an already stored report.

        Raises:
            KeyError: If ``problem.id`` is not in the store.
        """


class TriageRunRepository(Protocol):
    """Store for compile runs, Markdown bytes, items JSON, and execute results."""

    def insert(self, run: TriageRun) -> TriageRun:
        """Persist a run and return it with the assigned ``id``. Incoming ``id`` is ignored."""

    def get(self, run_id: int) -> TriageRun | None:
        """Return the run with this id, or ``None`` if it does not exist."""

    def save(self, run: TriageRun) -> None:
        """Replace stored status, Markdown bytes, items, and execution result.

        Raises:
            KeyError: If ``run.id`` is not in the store.
        """

    def supersede_open(self) -> None:
        """Set every ``pending`` and ``awaiting_execute`` run to ``superseded``.

        The status change is one transaction so concurrent readers see all
        open runs move together.
        """

    def list_by_status(self, *statuses: TriageRunStatus) -> tuple[TriageRun, ...]:
        """Runs whose status is one of ``statuses``, oldest first."""
