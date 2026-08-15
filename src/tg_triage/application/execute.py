"""Execute a confirmed Markdown plan against an IssueTracker.

Preview parses and validates with zero tracker calls. Confirm creates or records
skips. Uncertain and missing (excluded) blocks do nothing. Created URLs are
saved on the run before the next item so a retry does not duplicate creates.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import replace

from tg_triage.domain import (
    ExecutionAction,
    ExecutionItemResult,
    ExecutionResult,
    IssueRef,
    MarkdownPlan,
    ProblemLifecycle,
    RepositoryId,
    TriageItem,
    TriageOutcome,
    TriageRun,
    TriageRunStatus,
)
from tg_triage.markdown import parse_and_validate
from tg_triage.ports.issue_tracker import IssueTracker
from tg_triage.ports.repositories import ProblemRepository, TriageRunRepository


class ExecutionService:
    """Validate-all-then-write. Confirm is required; preview never writes."""

    def __init__(
        self,
        tracker: IssueTracker,
        problems: ProblemRepository,
        runs: TriageRunRepository,
        allowed_repos: Collection[RepositoryId],
    ) -> None:
        self._tracker = tracker
        self._problems = problems
        self._runs = runs
        self._allowed_repos = allowed_repos

    def preview(self, uploaded_markdown: bytes | str) -> MarkdownPlan:
        """Parse and validate the uploaded contract. Does not call the tracker."""
        return parse_and_validate(_as_text(uploaded_markdown), self._allowed_repos)

    def execute_confirmed(self, run: TriageRun) -> TriageRun:
        """Apply the uploaded plan after confirm.

        Validates the whole file before any tracker write. Create uses title and
        body only. Skip records Existing without a tracker call. Raises after
        persisting any creates that already succeeded.

        Raises:
            KeyError: If ``run.id`` is not in the run store, or a Problem id
                in the plan is missing.
            PlanValidationError: If the upload is not a valid plan. No writes.
            ValueError: If the run has no uploaded Markdown.
        """
        current = self._require_run(run.id)
        if current.uploaded_markdown is None:
            raise ValueError("run has no uploaded markdown")
        plan = self.preview(current.uploaded_markdown)
        try:
            for item in plan.items:
                current = self._apply_item(current, item)
        except Exception:
            self._runs.save(current)
            raise
        current = replace(current, status=TriageRunStatus.EXECUTED)
        self._runs.save(current)
        return current

    def cancel(self, run: TriageRun) -> TriageRun:
        """Mark the run cancelled and make no tracker writes."""
        current = self._require_run(run.id)
        current = replace(current, status=TriageRunStatus.CANCELLED)
        self._runs.save(current)
        return current

    def _apply_item(self, run: TriageRun, item: TriageItem) -> TriageRun:
        """Execute one plan item, persisting create/skip results immediately."""
        if item.outcome == TriageOutcome.UNCERTAIN:
            return run
        if _already_recorded(run, item.problem_ids):
            return run
        if item.outcome == TriageOutcome.CREATE_NEW:
            return self._create(run, item)
        if item.outcome == TriageOutcome.LINK_EXISTING:
            return self._skip(run, item)
        return run

    def _create(self, run: TriageRun, item: TriageItem) -> TriageRun:
        """Call the tracker, store the URL, and mark Problems linked."""
        if item.repository is None:
            raise ValueError("create item has no repository")
        created = self._tracker.create(item.repository, item.title, item.body)
        run = _append_result(
            run,
            ExecutionItemResult(
                action=ExecutionAction.CREATE,
                issue=created.ref,
                problem_ids=item.problem_ids,
                url=created.url,
            ),
        )
        self._runs.save(run)
        self._mark_linked(item.problem_ids, created.ref)
        return run

    def _skip(self, run: TriageRun, item: TriageItem) -> TriageRun:
        """Record Existing and mark Problems linked. No tracker write."""
        if item.existing_issue is None:
            raise ValueError("skip item has no Existing")
        run = _append_result(
            run,
            ExecutionItemResult(
                action=ExecutionAction.SKIP,
                issue=item.existing_issue,
                problem_ids=item.problem_ids,
                url=None,
            ),
        )
        self._runs.save(run)
        self._mark_linked(item.problem_ids, item.existing_issue)
        return run

    def _mark_linked(self, problem_ids: Sequence[int], issue: IssueRef) -> None:
        """Set lifecycle to linked for every Problem on the item."""
        for problem_id in problem_ids:
            problem = self._problems.get(problem_id)
            if problem is None:
                raise KeyError(problem_id)
            self._problems.save(
                replace(
                    problem,
                    lifecycle=ProblemLifecycle.LINKED,
                    linked_issue=issue,
                )
            )

    def _require_run(self, run_id: int) -> TriageRun:
        """Load the stored run.

        Raises:
            KeyError: If the run was never persisted.
        """
        stored = self._runs.get(run_id)
        if stored is None:
            raise KeyError(run_id)
        return stored


def _as_text(uploaded_markdown: bytes | str) -> str:
    """Decode stored upload bytes as UTF-8."""
    if isinstance(uploaded_markdown, bytes):
        return uploaded_markdown.decode("utf-8")
    return uploaded_markdown


def _already_recorded(run: TriageRun, problem_ids: tuple[int, ...]) -> bool:
    """True when this item already has a create URL or skip record on the run."""
    result = run.execution_result
    if result is None:
        return False
    target = frozenset(problem_ids)
    return any(frozenset(row.problem_ids) == target for row in result.items)


def _append_result(run: TriageRun, row: ExecutionItemResult) -> TriageRun:
    """Copy the run with one more execution row."""
    existing = run.execution_result.items if run.execution_result else ()
    return replace(run, execution_result=ExecutionResult(items=(*existing, row)))
