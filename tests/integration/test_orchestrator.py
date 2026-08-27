"""HITL orchestrator: upload is execute authority; supersede; confirm gate."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from tg_triage.application.compile import CompileService
from tg_triage.application.execute import ExecutionService
from tg_triage.application.orchestrator import ApplicationOrchestrator, OrchestratorError
from tg_triage.config import DEFAULT_YAML_PATH, load_yaml_config
from tg_triage.domain import Problem, RepositoryId, TriageOutcome, TriageRunStatus
from tg_triage.infrastructure.fixtures import FixtureKnowledgeSource
from tg_triage.infrastructure.sqlite import SqliteDb
from tg_triage.markdown import parse, render
from tests.fakes import FakeIssueTracker, FakeLlmJudgment

CANNED = Path(__file__).resolve().parents[1] / "canned_llm"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github"
ALLOWED = load_yaml_config(DEFAULT_YAML_PATH).repositories
SINCE = date(2026, 8, 13)
CREATED = datetime(2026, 8, 13, 10, 0, 0)
TEXTS = {
    101: "Экспорт дашборда продаж больше не работает. Крутится загрузка.",
    102: "На дашборде продаж экспорт зависает на спиннере. Нужен отчёт к планерке.",
    103: "Синхронизация клиентов задерживается.",
    104: "CSV-экспорт дашборда всё ещё висит — как раньше.",
}


def _canned(name: str) -> str:
    return (CANNED / name).read_text(encoding="utf-8")


def _llm() -> FakeLlmJudgment:
    cluster = _canned("cluster.json")
    match = _canned("match.json")
    return FakeLlmJudgment(
        cluster_replies=[cluster, cluster],
        match_replies=[match, match],
    )


def _seed(db: SqliteDb) -> None:
    for problem_id, text in TEXTS.items():
        db.problems.insert(
            Problem(
                id=problem_id,
                original_text=text,
                message_id=problem_id,
                chat_id=1,
                user_id=7,
                created_at=CREATED,
            )
        )


def _orchestrator(
    db: SqliteDb,
    tracker: FakeIssueTracker,
    llm: FakeLlmJudgment,
) -> ApplicationOrchestrator:
    compiler = CompileService(
        db.problems,
        db.runs,
        FixtureKnowledgeSource(FIXTURES, ALLOWED),
        llm,
        ALLOWED,
    )
    execution = ExecutionService(tracker, db.problems, db.runs, ALLOWED)
    return ApplicationOrchestrator(compiler, execution, db.runs)


def _edited_upload(generated: bytes, run_id: int, since: date) -> bytes:
    plan = parse(generated.decode("utf-8"))
    edited = []
    for item in plan.items:
        if item.outcome == TriageOutcome.CREATE_NEW:
            edited.append(replace(item, priority="P3"))
        elif item.outcome == TriageOutcome.UNCERTAIN:
            edited.append(
                replace(
                    item,
                    outcome=TriageOutcome.CREATE_NEW,
                    repository=RepositoryId.parse("acme/crm"),
                    body="Синхронизация клиентов задерживается. Нужна проверка CRM.",
                    rationale="",
                )
            )
        else:
            edited.append(item)
    return render(edited, run_id=run_id, since_date=since).encode("utf-8")


def test_upload_preview_confirm_uses_edited_file(tmp_path: Path) -> None:
    tracker = FakeIssueTracker()
    llm = _llm()
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed(db)
        orch = _orchestrator(db, tracker, llm)
        compiled = orch.compile(42, SINCE)
        assert compiled.run is not None
        generated = compiled.run.generated_markdown
        assert generated is not None
        generated_plan = parse(generated.decode("utf-8"))
        export = next(
            item for item in generated_plan.items if item.outcome == TriageOutcome.CREATE_NEW
        )
        assert export.priority == "P1"
        assert export.body.startswith("Sales dashboard export never finishes")

        upload = _edited_upload(generated, compiled.run.id, SINCE)
        orch.store_upload(compiled.run.id, upload)
        preview = orch.execute_preview(compiled.run.id)
        creates = [item for item in preview.items if item.outcome == TriageOutcome.CREATE_NEW]
        titles = {item.title: item for item in creates}
        assert titles["Sales dashboard export hangs"].priority == "P3"
        assert titles["Sales dashboard export hangs"].body == export.body
        crm = titles["Синхронизация клиентов задерживается"]
        assert crm.repository == RepositoryId.parse("acme/crm")
        assert "CRM" in crm.body
        assert all(item.outcome != TriageOutcome.UNCERTAIN for item in preview.items)

        confirmed = orch.confirm(compiled.run.id)
        assert confirmed.status == TriageRunStatus.EXECUTED
        assert [call.title for call in tracker.calls] == [
            "Sales dashboard export hangs",
            "Синхронизация клиентов задерживается",
        ]
        assert "CRM" in tracker.calls[1].body
        assert tracker.calls[0].body.startswith("Sales dashboard export never finishes")


def test_preview_without_upload_does_not_call_tracker(tmp_path: Path) -> None:
    tracker = FakeIssueTracker()
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed(db)
        orch = _orchestrator(db, tracker, _llm())
        compiled = orch.compile(42, SINCE)
        assert compiled.run is not None
        with pytest.raises(OrchestratorError, match="upload"):
            orch.execute_preview(compiled.run.id)
        assert tracker.calls == []


def test_second_compile_supersedes_and_blocks_old_execute(tmp_path: Path) -> None:
    tracker = FakeIssueTracker()
    llm = _llm()
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed(db)
        orch = _orchestrator(db, tracker, llm)
        first = orch.compile(42, SINCE)
        assert first.run is not None
        assert first.superseded_warning is False
        orch.store_upload(
            first.run.id,
            _edited_upload(first.run.generated_markdown or b"", first.run.id, SINCE),
        )
        assert db.runs.get(first.run.id).status == TriageRunStatus.AWAITING_EXECUTE

        second = orch.compile(42, SINCE)
        assert second.superseded_warning is True
        assert db.runs.get(first.run.id).status == TriageRunStatus.SUPERSEDED
        with pytest.raises(OrchestratorError, match="superseded"):
            orch.confirm(first.run.id)
        assert tracker.calls == []


def test_preview_does_not_use_generated_markdown(tmp_path: Path) -> None:
    tracker = FakeIssueTracker()
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed(db)
        orch = _orchestrator(db, tracker, _llm())
        compiled = orch.compile(42, SINCE)
        assert compiled.run is not None
        generated = compiled.run.generated_markdown
        assert generated is not None
        upload = _edited_upload(generated, compiled.run.id, SINCE)
        orch.store_upload(compiled.run.id, upload)
        preview = orch.execute_preview()
        generated_plan = parse(generated.decode("utf-8"))
        assert generated_plan.items != preview.items
        uploaded_plan = parse(upload.decode("utf-8"))
        assert preview.items == uploaded_plan.items
