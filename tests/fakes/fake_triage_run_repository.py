"""In-memory TriageRunRepository for execute tests."""

from __future__ import annotations

from dataclasses import replace

from tg_triage.domain import TriageRun, TriageRunStatus


class FakeTriageRunRepository:
    """Dict-backed run store. Incoming id is assigned on insert."""

    def __init__(self) -> None:
        self._by_id: dict[int, TriageRun] = {}
        self._next_id = 1

    def insert(self, run: TriageRun) -> TriageRun:
        """Persist a run and return it with an assigned id."""
        stored = replace(run, id=self._next_id)
        self._next_id += 1
        self._by_id[stored.id] = stored
        return stored

    def get(self, run_id: int) -> TriageRun | None:
        """Return the run with this id, or ``None``."""
        return self._by_id.get(run_id)

    def save(self, run: TriageRun) -> None:
        """Replace the stored run.

        Raises:
            KeyError: If ``run.id`` is not in the store.
        """
        if run.id not in self._by_id:
            raise KeyError(run.id)
        self._by_id[run.id] = run

    def supersede_open(self) -> None:
        """Set pending and awaiting runs to superseded."""
        for run_id, run in list(self._by_id.items()):
            if run.status in (TriageRunStatus.PENDING, TriageRunStatus.AWAITING_EXECUTE):
                self._by_id[run_id] = replace(run, status=TriageRunStatus.SUPERSEDED)

    def list_by_status(self, *statuses: TriageRunStatus) -> tuple[TriageRun, ...]:
        """Runs in any of ``statuses``, oldest first."""
        wanted = set(statuses)
        found = [run for run in self._by_id.values() if run.status in wanted]
        found.sort(key=lambda run: run.id)
        return tuple(found)
