"""Demo scenarios A then B with every external port faked.

No Telegram, GitHub, live LLM, or database server. Uploaded Markdown is the
execute authority; confirm is required before tracker writes.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

from tg_triage.application.compile import CompileService
from tg_triage.application.execute import ExecutionService
from tg_triage.application.intake import ingest_group_text
from tg_triage.application.orchestrator import ApplicationOrchestrator
from tg_triage.config import DEFAULT_YAML_PATH, load_yaml_config
from tg_triage.domain import ExecutionAction, Problem, ProblemLifecycle, TriageRunStatus
from tg_triage.infrastructure.fixtures import FixtureKnowledgeSource
from tg_triage.infrastructure.sqlite import SqliteDb
from tests.fakes import FakeIssueTracker, FakeLlmJudgment

CANNED = Path(__file__).resolve().parents[1] / "canned_llm"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github"
ALLOWED = load_yaml_config(DEFAULT_YAML_PATH).repositories
SINCE = date(2026, 8, 13)
CREATED = datetime(2026, 8, 13, 10, 0, 0)
CHAT_ID = 100
REPORTER_ID = 7
OWNER_ID = 42

SCENARIO_A = (
    "Экспорт дашборда продаж больше не работает. Крутится загрузка.",
    "На дашборде продаж экспорт зависает на спиннере. Нужен отчёт к планерке.",
    "Синхронизация клиентов задерживается.",
)
SCENARIO_B = (
    "CSV-экспорт дашборда всё ещё висит — как раньше.",
    "Что-то не так с клиентами, не понятно: портал или CRM.",
)
CRM_BODY = "Синхронизация клиентов задерживается. Нужна проверка CRM."


def _canned(name: str) -> str:
    """Load a canned LLM JSON document from the test tree."""
    return (CANNED / name).read_text(encoding="utf-8")


def _remap_problem_ids(payload: str, mapping: dict[int, int]) -> str:
    """Rewrite canned problem ids to the ids assigned by intake."""
    data = json.loads(payload)
    for item in data["items"]:
        item["problem_ids"] = [mapping[pid] for pid in item["problem_ids"]]
    return json.dumps(data, ensure_ascii=False)


def _ingest(db: SqliteDb, texts: tuple[str, ...], *, start_message_id: int) -> tuple[Problem, ...]:
    """Store group texts as ingested Problems and return them in order."""
    stored = []
    for offset, text in enumerate(texts):
        stored.append(
            ingest_group_text(
                db.problems,
                text,
                user_id=REPORTER_ID,
                message_id=start_message_id + offset,
                chat_id=CHAT_ID,
                created_at=CREATED,
            )
        )
    return tuple(stored)


def _orchestrator(
    db: SqliteDb,
    llm: FakeLlmJudgment,
    tracker: FakeIssueTracker,
) -> ApplicationOrchestrator:
    """Wire compile and execute against temp SQLite and fakes."""
    return ApplicationOrchestrator(
        CompileService(
            db.problems,
            db.runs,
            FixtureKnowledgeSource(FIXTURES, ALLOWED),
            llm,
            ALLOWED,
        ),
        ExecutionService(tracker, db.problems, db.runs, ALLOWED),
        db.runs,
    )


def _owner_edit_uncertain_to_crm_create(markdown: str) -> str:
    """Move the Uncertain block into Create on acme/crm and add a Body."""
    prefix, separator, uncertain = markdown.partition("\n## Uncertain\n")
    if not separator:
        raise AssertionError("generated markdown has no Uncertain section")
    block = uncertain.strip("\n").replace("- Repo: unknown", "- Repo: acme/crm", 1)
    if "- Body:" not in block:
        block = f"{block}\n- Body: |\n    {CRM_BODY}"
    skip_at = prefix.find("\n## Skip\n")
    if skip_at < 0:
        return f"{prefix.rstrip()}\n\n{block}\n"
    return f"{prefix[:skip_at].rstrip()}\n\n{block}\n{prefix[skip_at:].rstrip()}\n"


def test_demo_scenario_a_resolves_uncertain_to_create(tmp_path: Path) -> None:
    """A1–A7: cluster two export reports, resolve Uncertain to a CRM create."""
    tracker = FakeIssueTracker()
    llm = FakeLlmJudgment()
    with SqliteDb(tmp_path / "store.sqlite") as db:
        ingested = _ingest(db, SCENARIO_A, start_message_id=1)
        mapping = {canned: stored.id for canned, stored in zip((101, 102, 103), ingested)}
        llm.cluster_replies = [_remap_problem_ids(_canned("cluster_scenario_a.json"), mapping)]
        llm.match_replies = [_remap_problem_ids(_canned("match_scenario_a.json"), mapping)]
        orch = _orchestrator(db, llm, tracker)

        compiled = orch.compile(OWNER_ID, SINCE)
        assert compiled.run is not None
        generated = compiled.run.generated_markdown
        assert generated is not None
        generated_text = generated.decode("utf-8")
        assert "## Legend" in generated_text
        assert "## Create" in generated_text
        assert "## Uncertain" in generated_text
        assert "## Skip" not in generated_text

        upload = _owner_edit_uncertain_to_crm_create(generated_text).encode("utf-8")
        orch.store_upload(compiled.run.id, upload)
        executed = orch.confirm(compiled.run.id)

        assert executed.status == TriageRunStatus.EXECUTED
        assert [call.title for call in tracker.calls] == [
            "Sales dashboard export hangs",
            "Синхронизация клиентов задерживается",
        ]
        assert str(tracker.calls[0].repository) == "acme/sales-dashboard"
        assert str(tracker.calls[1].repository) == "acme/crm"
        assert CRM_BODY in tracker.calls[1].body
        assert executed.execution_result is not None
        actions = [row.action for row in executed.execution_result.items]
        assert actions.count(ExecutionAction.CREATE) == 2
        assert actions.count(ExecutionAction.SKIP) == 0
        for problem in ingested:
            stored_problem = db.problems.get(problem.id)
            assert stored_problem is not None
            assert stored_problem.lifecycle == ProblemLifecycle.LINKED
        assert db.problems.list_ingested_since(datetime.combine(SINCE, time.min)) == ()


def test_demo_scenario_b_skips_existing_and_leaves_uncertain(tmp_path: Path) -> None:
    """B after A: Skip seed issue #81; Uncertain stays ingested; no new creates."""
    tracker = FakeIssueTracker()
    llm = FakeLlmJudgment()
    with SqliteDb(tmp_path / "store.sqlite") as db:
        ingested_a = _ingest(db, SCENARIO_A, start_message_id=1)
        mapping_a = {canned: stored.id for canned, stored in zip((101, 102, 103), ingested_a)}
        llm.cluster_replies = [_remap_problem_ids(_canned("cluster_scenario_a.json"), mapping_a)]
        llm.match_replies = [_remap_problem_ids(_canned("match_scenario_a.json"), mapping_a)]
        orch = _orchestrator(db, llm, tracker)

        compiled_a = orch.compile(OWNER_ID, SINCE)
        assert compiled_a.run is not None
        generated_a = compiled_a.run.generated_markdown
        assert generated_a is not None
        orch.store_upload(
            compiled_a.run.id,
            _owner_edit_uncertain_to_crm_create(generated_a.decode("utf-8")).encode("utf-8"),
        )
        orch.confirm(compiled_a.run.id)
        creates_after_a = list(tracker.calls)
        assert len(creates_after_a) == 2

        ingested_b = _ingest(db, SCENARIO_B, start_message_id=10)
        mapping_b = {canned: stored.id for canned, stored in zip((201, 202), ingested_b)}
        llm.cluster_replies = [_remap_problem_ids(_canned("cluster_scenario_b.json"), mapping_b)]
        llm.match_replies = [_remap_problem_ids(_canned("match_scenario_b.json"), mapping_b)]

        remaining = db.problems.list_ingested_since(datetime.combine(SINCE, time.min))
        assert {row.id for row in remaining} == {ingested_b[0].id, ingested_b[1].id}

        compiled_b = orch.compile(OWNER_ID, SINCE)
        assert compiled_b.superseded_warning is False
        assert compiled_b.run is not None
        generated_b = compiled_b.run.generated_markdown
        assert generated_b is not None
        generated_text = generated_b.decode("utf-8")
        assert "## Skip" in generated_text
        assert "## Uncertain" in generated_text
        assert "acme/sales-dashboard#81" in generated_text

        orch.store_upload(compiled_b.run.id, generated_b)
        plan = orch.execute_preview(compiled_b.run.id)
        outcomes = {item.outcome.value for item in plan.items}
        assert outcomes == {"link_existing", "uncertain"}
        executed = orch.confirm(compiled_b.run.id)

        assert executed.status == TriageRunStatus.EXECUTED
        assert tracker.calls == creates_after_a
        assert executed.execution_result is not None
        actions = [row.action for row in executed.execution_result.items]
        assert actions.count(ExecutionAction.CREATE) == 0
        assert actions.count(ExecutionAction.SKIP) == 1
        skip = next(
            row for row in executed.execution_result.items if row.action == ExecutionAction.SKIP
        )
        assert str(skip.issue) == "acme/sales-dashboard#81"

        skip_report, vague = ingested_b
        linked = db.problems.get(skip_report.id)
        still_open = db.problems.get(vague.id)
        assert linked is not None and linked.lifecycle == ProblemLifecycle.LINKED
        assert still_open is not None and still_open.lifecycle == ProblemLifecycle.INGESTED
        leftover = db.problems.list_ingested_since(datetime.combine(SINCE, time.min))
        assert [row.id for row in leftover] == [vague.id]
