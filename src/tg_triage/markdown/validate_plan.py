"""Fail-closed checks for a parsed Markdown execution plan.

Priority is never a reason to reject a plan. Unknown sections are rejected by
the parser before this module runs.
"""

from __future__ import annotations

from collections.abc import Collection

from tg_triage.domain import MarkdownPlan, RepositoryId, TriageItem, TriageOutcome
from tg_triage.markdown.parser import PlanValidationError


def validate_plan(plan: MarkdownPlan, allowed_repos: Collection[RepositoryId]) -> None:
    """Require Create/Skip/Uncertain fields and that repos are in the closed list.

    Uncertain may use ``unknown`` (no repository). Create must not.

    Raises:
        PlanValidationError: On the first item that cannot be executed.
    """
    allowed = {str(repo) for repo in allowed_repos}
    for item in plan.items:
        if item.outcome == TriageOutcome.CREATE_NEW:
            _validate_create(item, allowed)
        elif item.outcome == TriageOutcome.LINK_EXISTING:
            _validate_skip(item, allowed)
        elif item.outcome == TriageOutcome.UNCERTAIN:
            _validate_uncertain(item, allowed)


def _validate_create(item: TriageItem, allowed: set[str]) -> None:
    """Create needs a configured repo, problem ids, and a body."""
    if item.repository is None:
        raise PlanValidationError("create requires Repo (not unknown)")
    _require_known_repo(item.repository, allowed)
    _require_problems(item)
    if not item.body.strip():
        raise PlanValidationError("create requires Body")


def _validate_skip(item: TriageItem, allowed: set[str]) -> None:
    """Skip needs a configured repo, Existing ``owner/repo#n``, and problem ids."""
    if item.repository is None:
        raise PlanValidationError("skip requires Repo (not unknown)")
    _require_known_repo(item.repository, allowed)
    if item.existing_issue is None:
        raise PlanValidationError("skip requires Existing")
    _require_problems(item)


def _validate_uncertain(item: TriageItem, allowed: set[str]) -> None:
    """Uncertain needs problem ids and Reason. Repo may be unknown or configured."""
    _require_problems(item)
    if not item.rationale.strip():
        raise PlanValidationError("uncertain requires Reason")
    if item.repository is not None:
        _require_known_repo(item.repository, allowed)


def _require_problems(item: TriageItem) -> None:
    """Every executable block must list at least one Problem id."""
    if not item.problem_ids:
        raise PlanValidationError("item requires Problems")


def _require_known_repo(repository: RepositoryId, allowed: set[str]) -> None:
    """Reject invented owner/repo values."""
    if str(repository) not in allowed:
        raise PlanValidationError(f"repository not in config: {repository}")
