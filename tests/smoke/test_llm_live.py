"""Live LLM compile smoke. Opt-in; skipped in default pytest.

Uses real ``HttpLlmJudgment`` and checked-in fixture files. Does not send
Telegram or create GitHub issues. Coding agents must not invent API keys.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from tg_triage.application.compile import CompileService
from tg_triage.config import (
    LLM_CONNECT_TIMEOUT,
    LLM_READ_TIMEOUT,
    REPO_ROOT,
)
from tg_triage.domain import Problem, TriageRunStatus, assert_coverage
from tg_triage.infrastructure.fixtures import FixtureKnowledgeSource
from tg_triage.infrastructure.sqlite import SqliteDb
from tg_triage.llm.http_client import HttpLlmJudgment
from tg_triage.markdown import parse
from tg_triage.operator.runtime import load_operator_config

pytestmark = pytest.mark.smoke

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github"
DEBUG_ROOT = REPO_ROOT / "debug" / "triage-runs" / "smoke"
SINCE = date(2026, 8, 13)
CREATED = datetime(2026, 8, 13, 10, 0, 0)
TEXTS = (
    "Экспорт дашборда продаж больше не работает. Крутится загрузка.",
    "На дашборде продаж экспорт зависает на спиннере. Нужен отчёт к планерке.",
    "Синхронизация клиентов задерживается.",
    "CSV-экспорт дашборда всё ещё висит — как раньше.",
)


@pytest.fixture
def live_llm_http() -> httpx.Client:
    """Opt-in HTTP client for the configured OpenAI-compatible LLM.

    Skips when the flag is unset or the server does not accept a connection.
    """
    if os.environ.get("RUN_LLM_SMOKE") != "1":
        pytest.skip("set RUN_LLM_SMOKE=1 to run live LLM smoke")
    config = load_operator_config()
    settings = config.settings
    timeout = httpx.Timeout(
        connect=LLM_CONNECT_TIMEOUT,
        read=LLM_READ_TIMEOUT,
        write=30.0,
        pool=10.0,
    )
    headers: dict[str, str] = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    http = httpx.Client(
        base_url=settings.llm_base_url,
        timeout=timeout,
        headers=headers,
    )
    try:
        http.get("/models", timeout=5.0)
    except httpx.RequestError as exc:
        http.close()
        pytest.skip(f"LLM unreachable at {settings.llm_base_url}: {exc}")
    yield http
    http.close()


def test_live_compile_returns_schema_valid_markdown(
    tmp_path: Path,
    live_llm_http: httpx.Client,
) -> None:
    """Cluster and match four Russian reports; clustering shape may vary."""
    config = load_operator_config()
    allowed = config.repositories
    llm = HttpLlmJudgment(
        live_llm_http,
        model=config.settings.llm_model,
        api_key=config.settings.llm_api_key,
    )
    db_path = tmp_path / "smoke.sqlite"
    with SqliteDb(db_path) as db:
        for index, text in enumerate(TEXTS, start=1):
            db.problems.insert(
                Problem(
                    id=index,
                    original_text=text,
                    message_id=index,
                    chat_id=1,
                    user_id=7,
                    created_at=CREATED,
                )
            )
        result = CompileService(
            db.problems,
            db.runs,
            FixtureKnowledgeSource(FIXTURES, allowed),
            llm,
            allowed,
            debug_root=DEBUG_ROOT,
        ).compile(owner_user_id=42, since_date=SINCE)
    run = result.run
    assert run is not None
    assert run.status == TriageRunStatus.PENDING
    assert run.generated_markdown is not None
    problem_ids = {index for index in range(1, len(TEXTS) + 1)}
    assert_coverage(problem_ids, run.items)
    for item in run.items:
        if item.repository is not None:
            assert item.repository in allowed
    plan = parse(run.generated_markdown.decode("utf-8"))
    assert plan.items
    assert_coverage(problem_ids, plan.items)
    dump_dir = DEBUG_ROOT / str(run.id)
    assert (dump_dir / "cluster.response.json").is_file()
    assert (dump_dir / "match.response.json").is_file() or (
        dump_dir / "match.retry.response.json"
    ).is_file()
