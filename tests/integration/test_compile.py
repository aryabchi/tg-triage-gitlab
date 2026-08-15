"""Compile orchestrator: canned LLM, temp SQLite, fixture files, debug dumps."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from tg_triage.application.compile import CompileService
from tg_triage.config import DEFAULT_YAML_PATH, load_yaml_config
from tg_triage.domain import Problem, TriageOutcome, TriageRunStatus, assert_coverage
from tg_triage.infrastructure.fixtures import FixtureKnowledgeSource
from tg_triage.infrastructure.sqlite import SqliteDb
from tg_triage.markdown import parse
from tg_triage.ports.llm import LlmCallError, LlmJudgment
from tests.fakes import FakeLlmJudgment

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


def _read(name: str) -> str:
    return (CANNED / name).read_text(encoding="utf-8")


def _seed_problems(db: SqliteDb) -> None:
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


def _service(
    db: SqliteDb,
    llm: LlmJudgment,
    *,
    fixture_root: Path = FIXTURES,
    debug_root: Path | None = None,
) -> CompileService:
    return CompileService(
        db.problems,
        db.runs,
        FixtureKnowledgeSource(fixture_root, ALLOWED),
        llm,
        ALLOWED,
        debug_root=debug_root,
    )


def test_compile_success_two_llm_calls(tmp_path: Path) -> None:
    llm = FakeLlmJudgment(
        cluster_replies=[_read("cluster.json")],
        match_replies=[_read("match.json")],
    )
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed_problems(db)
        result = _service(db, llm, debug_root=tmp_path / "debug").compile(
            owner_user_id=42, since_date=SINCE
        )
    assert llm.cluster_calls == 1
    assert llm.match_calls == 1
    assert result.run is not None
    assert result.run.status == TriageRunStatus.PENDING
    assert result.run.generated_markdown is not None
    plan = parse(result.run.generated_markdown.decode("utf-8"))
    outcomes = {item.outcome: item for item in plan.items}
    assert outcomes[TriageOutcome.CREATE_NEW].problem_ids == (101, 102)
    assert outcomes[TriageOutcome.CREATE_NEW].repository is not None
    assert str(outcomes[TriageOutcome.CREATE_NEW].repository) == "acme/sales-dashboard"
    assert outcomes[TriageOutcome.LINK_EXISTING].problem_ids == (104,)
    assert outcomes[TriageOutcome.UNCERTAIN].problem_ids == (103,)
    assert_coverage({101, 102, 103, 104}, plan.items)
    debug_dir = tmp_path / "debug" / str(result.run.id)
    assert (debug_dir / "cluster.response.json").is_file()
    assert (debug_dir / "match.response.json").is_file()


def test_empty_period_makes_zero_llm_calls(tmp_path: Path) -> None:
    llm = FakeLlmJudgment(cluster_replies=[_read("cluster.json")])
    with SqliteDb(tmp_path / "store.sqlite") as db:
        result = _service(db, llm).compile(owner_user_id=42, since_date=SINCE)
    assert result.no_problems is True
    assert result.run is None
    assert llm.cluster_calls == 0
    assert llm.match_calls == 0


def test_missing_fixtures_fails_without_llm(tmp_path: Path) -> None:
    llm = FakeLlmJudgment(cluster_replies=[_read("cluster.json")])
    empty = tmp_path / "github"
    empty.mkdir()
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed_problems(db)
        result = _service(db, llm, fixture_root=empty).compile(
            owner_user_id=42, since_date=SINCE
        )
    assert llm.cluster_calls == 0
    assert result.run is not None
    assert result.run.status == TriageRunStatus.FAILED
    assert result.run.generated_markdown is None


def test_bad_cluster_then_good_retries_once(tmp_path: Path) -> None:
    llm = FakeLlmJudgment(
        cluster_replies=[_read("cluster_bad.json"), _read("cluster.json")],
        match_replies=[_read("match.json")],
    )
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed_problems(db)
        result = _service(db, llm).compile(owner_user_id=42, since_date=SINCE)
    assert llm.cluster_calls == 2
    assert llm.match_calls == 1
    assert result.run is not None
    assert result.run.status == TriageRunStatus.PENDING
    assert result.run.generated_markdown is not None


def test_invented_repo_fails_with_no_document(tmp_path: Path) -> None:
    llm = FakeLlmJudgment(
        cluster_replies=[_read("cluster.json")],
        match_replies=[_read("match_invented.json"), _read("match_invented.json")],
    )
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed_problems(db)
        result = _service(db, llm).compile(owner_user_id=42, since_date=SINCE)
    assert llm.cluster_calls == 1
    assert llm.match_calls == 2
    assert result.run is not None
    assert result.run.status == TriageRunStatus.FAILED
    assert result.run.generated_markdown is None


class _FailingLlm:
    """Raises ``error`` on every cluster/match call."""

    def __init__(self, error: LlmCallError) -> None:
        self.error = error
        self.cluster_calls = 0
        self.match_calls = 0

    def cluster(self, problems: object, *, retry: bool = False) -> str:
        """Always raise the configured transport error."""
        self.cluster_calls += 1
        raise self.error

    def match(self, items: object, evidence_pack: object, *, retry: bool = False) -> str:
        """Always raise the configured transport error."""
        self.match_calls += 1
        raise self.error


class _RetryThenOkLlm:
    """Raises one retryable cluster error, then returns canned JSON."""

    def __init__(self) -> None:
        self.cluster_calls = 0
        self.match_calls = 0
        self._failed_once = False

    def cluster(self, problems: object, *, retry: bool = False) -> str:
        """Fail once with 429, then return canned cluster JSON."""
        self.cluster_calls += 1
        if not self._failed_once:
            self._failed_once = True
            raise LlmCallError("LLM HTTP 429", retryable=True)
        return _read("cluster.json")

    def match(self, items: object, evidence_pack: object, *, retry: bool = False) -> str:
        """Return canned match JSON."""
        self.match_calls += 1
        return _read("match.json")


def test_retryable_llm_error_retries_then_succeeds(tmp_path: Path) -> None:
    llm = _RetryThenOkLlm()
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed_problems(db)
        result = _service(db, llm).compile(owner_user_id=42, since_date=SINCE)
    assert llm.cluster_calls == 2
    assert llm.match_calls == 1
    assert result.run is not None
    assert result.run.status == TriageRunStatus.PENDING
    assert result.run.generated_markdown is not None


def test_retryable_llm_error_fails_run_after_retry(tmp_path: Path) -> None:
    llm = _FailingLlm(LlmCallError("LLM HTTP 429", retryable=True))
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed_problems(db)
        result = _service(db, llm).compile(owner_user_id=42, since_date=SINCE)
    assert llm.cluster_calls == 2
    assert result.run is not None
    assert result.run.status == TriageRunStatus.FAILED
    assert result.run.generated_markdown is None
    assert result.run.items == ()


def test_non_retryable_llm_error_fails_without_retry(tmp_path: Path) -> None:
    llm = _FailingLlm(LlmCallError("LLM HTTP 401", retryable=False))
    with SqliteDb(tmp_path / "store.sqlite") as db:
        _seed_problems(db)
        result = _service(db, llm).compile(owner_user_id=42, since_date=SINCE)
    assert llm.cluster_calls == 1
    assert result.run is not None
    assert result.run.status == TriageRunStatus.FAILED
    assert result.run.generated_markdown is None
