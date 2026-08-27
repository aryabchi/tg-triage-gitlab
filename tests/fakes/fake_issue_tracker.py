"""In-memory IssueTracker. Records create calls; never talks to a network."""

from __future__ import annotations

from dataclasses import dataclass

from tg_triage.domain import IssueRef, RepositoryId
from tg_triage.ports.issue_tracker import CreatedIssue


@dataclass(frozen=True, slots=True)
class TrackerCreateCall:
    """One create invocation: title and body only."""

    repository: RepositoryId
    title: str
    body: str


class FakeIssueTracker:
    """Scriptable tracker for execute tests."""

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls: list[TrackerCreateCall] = []
        self.fail_after = fail_after
        self._next_number = 1

    def create(self, repository: RepositoryId, title: str, body: str) -> CreatedIssue:
        """Record the call and return a synthetic ref and URL.

        ``fail_after`` is the number of successful creates before the next
        call raises. Priority must not be passed in; this method has no such
        parameter.

        Raises:
            RuntimeError: When ``fail_after`` successful creates have already
                been recorded.
        """
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("tracker create failed")
        self.calls.append(TrackerCreateCall(repository=repository, title=title, body=body))
        number = self._next_number
        self._next_number += 1
        ref = IssueRef(repository, number)
        return CreatedIssue(ref=ref, url=f"created://{repository}#{number}")
