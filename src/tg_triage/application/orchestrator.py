"""Command use cases for compile, upload, preview, confirm, and cancel.

Allowlisting is the adapter's job. This module still does not import Telegram.
Uploaded Markdown is the only execute input; generated Markdown is stale after
upload.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from tg_triage.application.compile import CompileService
from tg_triage.application.execute import ExecutionService
from tg_triage.domain import MarkdownPlan, TriageRun, TriageRunStatus
from tg_triage.ports.repositories import TriageRunRepository

_OPEN = (TriageRunStatus.PENDING, TriageRunStatus.AWAITING_EXECUTE)


class OrchestratorError(Exception):
    """A command cannot proceed; the message is safe to show the owner."""


@dataclass(frozen=True, slots=True)
class OrchestratorCompileResult:
    """Compile outcome plus whether an earlier open run was superseded."""

    run: TriageRun | None
    no_problems: bool
    superseded_warning: bool


class ApplicationOrchestrator:
    """HITL loop: compile, store an edited file, preview, confirm or cancel."""

    def __init__(
        self,
        compiler: CompileService,
        execution: ExecutionService,
        runs: TriageRunRepository,
    ) -> None:
        self._compiler = compiler
        self._execution = execution
        self._runs = runs

    def compile(self, owner_id: int, since_date: date) -> OrchestratorCompileResult:
        """Start a new run. Open pending/awaiting runs become superseded."""
        open_runs = self._runs.list_by_status(*_OPEN)
        self._runs.supersede_open()
        result = self._compiler.compile(owner_user_id=owner_id, since_date=since_date)
        return OrchestratorCompileResult(
            run=result.run,
            no_problems=result.no_problems,
            superseded_warning=bool(open_runs),
        )

    def store_upload(self, run_id: int, markdown: bytes) -> TriageRun:
        """Store owner-edited Markdown on the run and mark it awaiting execute.

        Generated Markdown stays on the row for audit but must not be executed.

        Raises:
            OrchestratorError: If the run is missing, not open, or the bytes
                are empty or not UTF-8.
        """
        run = self._require_open(run_id)
        if not markdown:
            raise OrchestratorError("upload is empty")
        try:
            markdown.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OrchestratorError("upload is not UTF-8 Markdown") from exc
        run = replace(
            run,
            uploaded_markdown=markdown,
            status=TriageRunStatus.AWAITING_EXECUTE,
        )
        self._runs.save(run)
        return run

    def execute_preview(self, run_id: int | None = None) -> MarkdownPlan:
        """Parse the stored upload. Does not call the tracker.

        Raises:
            OrchestratorError: If there is no run, it is not executable, or
                no file has been uploaded.
        """
        run = self._resolve(run_id)
        if run.uploaded_markdown is None:
            raise OrchestratorError("upload a Markdown file before /execute")
        return self._execution.preview(run.uploaded_markdown)

    def confirm(self, run_id: int | None = None) -> TriageRun:
        """Execute the uploaded plan after confirm.

        Raises:
            OrchestratorError: If the run is superseded or has no upload.
        """
        run = self._resolve(run_id)
        return self._execution.execute_confirmed(run)

    def cancel(self, run_id: int | None = None) -> TriageRun:
        """Cancel the run with no tracker writes."""
        run = self._resolve(run_id)
        return self._execution.cancel(run)

    def _resolve(self, run_id: int | None) -> TriageRun:
        """Load ``run_id`` or the latest open run.

        Raises:
            OrchestratorError: If the run is missing or not pending/awaiting.
        """
        if run_id is None:
            open_runs = self._runs.list_by_status(*_OPEN)
            if not open_runs:
                raise OrchestratorError("no open triage run")
            return self._require_open(open_runs[-1].id)
        return self._require_open(run_id)

    def _require_open(self, run_id: int) -> TriageRun:
        """Load a pending or awaiting run.

        Raises:
            OrchestratorError: If missing or in a terminal status.
        """
        run = self._runs.get(run_id)
        if run is None:
            raise OrchestratorError(f"unknown run: {run_id}")
        if run.status == TriageRunStatus.SUPERSEDED:
            raise OrchestratorError("run is superseded")
        if run.status not in _OPEN:
            raise OrchestratorError(f"run cannot be used: {run.status.value}")
        return run
