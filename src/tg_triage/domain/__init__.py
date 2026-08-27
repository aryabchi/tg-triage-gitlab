"""Tracker-neutral value objects for reports, triage runs, and tracker identities.

This package has no infrastructure imports. Identities use ``owner/repo`` and
``owner/repo#n`` forms; adapters map those onto a concrete tracker.
"""

from tg_triage.domain.coverage import CoverageError, assert_coverage
from tg_triage.domain.models import (
    EvidencePack,
    EvidenceSnippet,
    ExecutionAction,
    ExecutionItemResult,
    ExecutionResult,
    IssueRef,
    MarkdownPlan,
    Problem,
    ProblemLifecycle,
    RepositoryId,
    SnippetKind,
    TriageItem,
    TriageOutcome,
    TriageRun,
    TriageRunStatus,
)

__all__ = [
    "CoverageError",
    "EvidencePack",
    "EvidenceSnippet",
    "ExecutionAction",
    "ExecutionItemResult",
    "ExecutionResult",
    "IssueRef",
    "MarkdownPlan",
    "Problem",
    "ProblemLifecycle",
    "RepositoryId",
    "SnippetKind",
    "TriageItem",
    "TriageOutcome",
    "TriageRun",
    "TriageRunStatus",
    "assert_coverage",
]
