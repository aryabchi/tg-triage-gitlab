"""Outbound issue-tracker port. The domain never imports a vendor client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tg_triage.domain import IssueRef, RepositoryId


@dataclass(frozen=True, slots=True)
class CreatedIssue:
    """Identity and address returned after a successful create."""

    ref: IssueRef
    url: str


class IssueTracker(Protocol):
    """Create issues after owner confirm. Skip is recorded without calling this."""

    def create(self, repository: RepositoryId, title: str, body: str) -> CreatedIssue:
        """Open a new issue with title and body only. Do not send Priority or labels."""
