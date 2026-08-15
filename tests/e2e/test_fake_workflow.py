"""Architecture proof: intake through confirm with every external port faked.

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
from tg_triage.domain import ExecutionAction, ProblemLifecycle, TriageRunStatus
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
SKIP_MATCH = "CSV-экспорт дашборда всё ещё висит — как раньше."
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


def _owner_edit_uncertain_to_crm_create(markdown: str) -> str:
    """Simulate an owner edit: Uncertain becomes Create on acme/crm with a Body."""
    prefix, separator, uncertain = markdown.partition("\n## Uncertain\n")
    if not separator:
        raise AssertionError("generated markdown has no Uncertain section")
    block = uncertain.strip("\n").replace("- Repo: unknown", "- Repo: acme/crm", 1)
    if "- Body:" not in block:
        block = f"{block}\n- Body: |\n    {CRM_BODY}"
    create_end = prefix.index("\n## Skip\n")
    return f"{prefix[:create_end].rstrip()}\n\n{block}\n{prefix[create_end:].rstrip()}\n"


def test_fake_workflow_from_ingest_to_confirm(tmp_path: Path) -> None:
    texts = (*SCENARIO_A, SKIP_MATCH)
    tracker = FakeIssueTracker()
    with SqliteDb(tmp_path / "store.sqlite") as db:
        ingested = [
            ingest_group_text(
                db.problems,
                text,
                user_id=REPORTER_ID,
                message_id=index,
                chat_id=CHAT_ID,
                created_at=CREATED,
            )
            for index, text in enumerate(texts, start=1)
        ]
        mapping = {canned: stored.id for canned, stored in zip((101, 102, 103, 104), ingested)}
        llm = FakeLlmJudgment(
            cluster_replies=[_remap_problem_ids(_canned("cluster.json"), mapping)],
            match_replies=[_remap_problem_ids(_canned("match.json"), mapping)],
        )
        orch = ApplicationOrchestrator(
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

        compiled = orch.compile(OWNER_ID, SINCE)
        assert compiled.run is not None
        generated = compiled.run.generated_markdown
        assert generated is not None
        generated_text = generated.decode("utf-8")
        assert "## Create" in generated_text
        assert "## Uncertain" in generated_text
        assert "## Skip" in generated_text

        upload = _owner_edit_uncertain_to_crm_create(generated_text).encode("utf-8")
        stored = orch.store_upload(compiled.run.id, upload)
        assert stored.status == TriageRunStatus.AWAITING_EXECUTE
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
        assert actions.count(ExecutionAction.SKIP) == 1
        skip = next(
            row for row in executed.execution_result.items if row.action == ExecutionAction.SKIP
        )
        assert str(skip.issue) == "acme/sales-dashboard#81"

        export_a, export_b, sync, skip_report = ingested
        for problem in (export_a, export_b, sync, skip_report):
            stored_problem = db.problems.get(problem.id)
            assert stored_problem is not None
            assert stored_problem.lifecycle == ProblemLifecycle.LINKED
        assert db.problems.list_ingested_since(datetime.combine(SINCE, time.min)) == ()
