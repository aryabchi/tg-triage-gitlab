"""Persistence and tracker ports. Adapters implement these; application code depends only on them."""

from tg_triage.ports.issue_tracker import CreatedIssue, IssueTracker
from tg_triage.ports.repositories import ProblemRepository, TriageRunRepository

__all__ = [
    "CreatedIssue",
    "IssueTracker",
    "ProblemRepository",
    "TriageRunRepository",
]
