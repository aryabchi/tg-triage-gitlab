"""In-memory ProblemRepository. Same uniqueness and filter rules as SQLite."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from tg_triage.domain import Problem, ProblemLifecycle


class FakeProblemRepository:
    """Dict-backed store used by intake tests. No database."""

    def __init__(self) -> None:
        self._by_id: dict[int, Problem] = {}
        self._next_id = 1

    def insert(self, problem: Problem) -> Problem:
        """Persist a report; duplicate chat+message returns the existing row."""
        for existing in self._by_id.values():
            if existing.chat_id == problem.chat_id and existing.message_id == problem.message_id:
                return existing
        stored = replace(problem, id=self._next_id)
        self._next_id += 1
        self._by_id[stored.id] = stored
        return stored

    def get(self, problem_id: int) -> Problem | None:
        """Return the report with this id, or ``None``."""
        return self._by_id.get(problem_id)

    def list_ingested_since(self, since: datetime) -> tuple[Problem, ...]:
        """Ingested reports at or after ``since``, oldest first."""
        found = [
            problem
            for problem in self._by_id.values()
            if problem.lifecycle == ProblemLifecycle.INGESTED and problem.created_at >= since
        ]
        found.sort(key=lambda problem: (problem.created_at, problem.id))
        return tuple(found)

    def save(self, problem: Problem) -> None:
        """Replace the stored row with the same id.

        Raises:
            KeyError: If ``problem.id`` is not in the store.
        """
        if problem.id not in self._by_id:
            raise KeyError(problem.id)
        self._by_id[problem.id] = problem

    def add(self, problem: Problem) -> Problem:
        """Store a problem under its given id so tests can use known Problem ids."""
        self._by_id[problem.id] = problem
        self._next_id = max(self._next_id, problem.id + 1)
        return problem
