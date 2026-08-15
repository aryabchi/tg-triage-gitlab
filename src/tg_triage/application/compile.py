"""Two-stage compile: cluster, match, validate, render, persist.

The LLM never authors the Markdown file. Invalid JSON is retried once, then the
run is failed with no document. Missing fixtures fail before any LLM call.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from pathlib import Path
from typing import TypeVar

from tg_triage.domain import (
    EvidencePack,
    Problem,
    RepositoryId,
    TriageItem,
    TriageRun,
    TriageRunStatus,
)
from tg_triage.llm.debug_writer import write_debug
from tg_triage.llm.validate import LlmValidationError, parse_json, validate_cluster, validate_match
from tg_triage.markdown import render
from tg_triage.ports.knowledge import KnowledgeSource, MissingFixtureError
from tg_triage.ports.llm import ClusterItem, LlmJudgment
from tg_triage.ports.repositories import ProblemRepository, TriageRunRepository

DebugWrite = Callable[[Path, str, object], None]
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CompileResult:
    """Outcome of one compile. ``run`` is None when the period has no Problems."""

    run: TriageRun | None
    no_problems: bool = False


class CompileService:
    """Load ingested Problems and fixtures, then cluster and match."""

    def __init__(
        self,
        problems: ProblemRepository,
        runs: TriageRunRepository,
        knowledge: KnowledgeSource,
        llm: LlmJudgment,
        allowed_repos: Collection[RepositoryId],
        *,
        debug_root: Path | None = None,
        debug_write: DebugWrite = write_debug,
    ) -> None:
        self._problems = problems
        self._runs = runs
        self._knowledge = knowledge
        self._llm = llm
        self._allowed_repos = allowed_repos
        self._debug_root = debug_root
        self._debug_write = debug_write

    def compile(self, *, owner_user_id: int, since_date: date) -> CompileResult:
        """Run cluster then match for ingested Problems on or after ``since_date``.

        Empty set: no LLM, no run. Missing fixtures or two invalid LLM replies:
        a ``failed`` run with no generated Markdown.
        """
        since = datetime.combine(since_date, time.min)
        problems = self._problems.list_ingested_since(since)
        if not problems:
            return CompileResult(run=None, no_problems=True)
        run = self._runs.insert(
            TriageRun(
                id=0,
                since_date=since_date,
                owner_user_id=owner_user_id,
                status=TriageRunStatus.PENDING,
            )
        )
        debug_dir = self._debug_dir(run.id)
        try:
            pack = self._knowledge.collect()
        except MissingFixtureError:
            return self._fail(run)
        problem_ids = tuple(problem.id for problem in problems)
        clustered = self._cluster(problems, problem_ids, debug_dir)
        if clustered is None:
            return self._fail(run)
        matched = self._match(clustered, pack, problem_ids, debug_dir)
        if matched is None:
            return self._fail(run)
        markdown = render(matched, run_id=run.id, since_date=since_date)
        run = replace(
            run,
            status=TriageRunStatus.PENDING,
            generated_markdown=markdown.encode("utf-8"),
            items=matched,
        )
        self._runs.save(run)
        return CompileResult(run=run)

    def _cluster(
        self,
        problems: Sequence[Problem],
        problem_ids: tuple[int, ...],
        debug_dir: Path | None,
    ) -> tuple[ClusterItem, ...] | None:
        """Cluster with one retry. Returns None when both attempts are invalid."""
        request = [{"id": problem.id, "original_text": problem.original_text} for problem in problems]
        return self._llm_stage(
            request=request,
            call=lambda retry: self._llm.cluster(problems, retry=retry),
            parse=lambda data: validate_cluster(data, problem_ids),
            debug_dir=debug_dir,
            stem="cluster",
        )

    def _match(
        self,
        clustered: Sequence[ClusterItem],
        pack: EvidencePack,
        problem_ids: tuple[int, ...],
        debug_dir: Path | None,
    ) -> tuple[TriageItem, ...] | None:
        """Match with one retry against the evidence pack."""
        request = {
            "items": [
                {"summary": item.summary, "problem_ids": list(item.problem_ids)}
                for item in clustered
            ]
        }
        return self._llm_stage(
            request=request,
            call=lambda retry: self._llm.match(clustered, pack, retry=retry),
            parse=lambda data: validate_match(
                data,
                allowed_repos=self._allowed_repos,
                pack=pack,
                problem_ids=problem_ids,
            ),
            debug_dir=debug_dir,
            stem="match",
        )

    def _llm_stage(
        self,
        *,
        request: object,
        call: Callable[[bool], str],
        parse: Callable[[object], T],
        debug_dir: Path | None,
        stem: str,
    ) -> T | None:
        """Call the model, validate, retry once, dump debug JSON best-effort."""
        raw = call(False)
        self._dump(debug_dir, f"{stem}.request.json", request)
        self._dump(debug_dir, f"{stem}.response.json", raw)
        try:
            return parse(parse_json(raw))
        except LlmValidationError:
            raw = call(True)
            self._dump(debug_dir, f"{stem}.retry.request.json", request)
            self._dump(debug_dir, f"{stem}.retry.response.json", raw)
            try:
                return parse(parse_json(raw))
            except LlmValidationError:
                return None

    def _fail(self, run: TriageRun) -> CompileResult:
        """Persist a failed run with no generated document."""
        run = replace(run, status=TriageRunStatus.FAILED, generated_markdown=None, items=())
        self._runs.save(run)
        return CompileResult(run=run)

    def _debug_dir(self, run_id: int) -> Path | None:
        """Per-run debug directory, or None when debug is disabled."""
        if self._debug_root is None:
            return None
        return self._debug_root / str(run_id)

    def _dump(self, directory: Path | None, filename: str, payload: object) -> None:
        """Write debug JSON; never raises."""
        if directory is None:
            return
        self._debug_write(directory, filename, payload)
