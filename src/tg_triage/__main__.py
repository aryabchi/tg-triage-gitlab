"""Long-polling bot entry point: ``python -m tg_triage``.

Wires SQLite, fixture knowledge, HTTP LLM, GitHub create, and Telegram polling.
Default pytest does not import this module and does not need tokens.
"""

from __future__ import annotations

import sys

import httpx
from telegram.ext import Application

from tg_triage.application.compile import CompileService
from tg_triage.application.execute import ExecutionService
from tg_triage.application.orchestrator import ApplicationOrchestrator
from tg_triage.config import REPO_ROOT, MissingSettingsError, load_config
from tg_triage.infrastructure.fixtures import FixtureKnowledgeSource
from tg_triage.infrastructure.github.client import GitHubClient, github_http_client
from tg_triage.infrastructure.github.issue_tracker import GitHubIssueTracker
from tg_triage.infrastructure.sqlite import SqliteDb
from tg_triage.infrastructure.telegram.adapter import (
    TelegramBotGateway,
    configure_application,
    register_handlers,
)
from tg_triage.infrastructure.telegram.handlers import BotHandlers
from tg_triage.llm.http_client import HttpLlmJudgment
from tg_triage.operator.runtime import target_repositories


def main() -> int:
    """Start long polling. Requires Telegram env keys; GitHub token for Confirm."""
    env_file = REPO_ROOT / ".env"
    config = load_config(env_file=env_file if env_file.is_file() else None)
    try:
        config.require(
            "telegram_bot_token",
            "telegram_group_chat_id",
            "telegram_owner_user_ids",
            "github_token",
        )
    except MissingSettingsError as exc:
        print(exc, file=sys.stderr)
        return 1
    settings = config.settings
    token = settings.telegram_bot_token
    github_token = settings.github_token
    group_raw = settings.telegram_group_chat_id
    if token is None or github_token is None or group_raw is None:
        return 1
    group_chat_id = int(group_raw)
    repositories = target_repositories(config)
    db = SqliteDb(settings.sqlite_path)
    llm_http = httpx.Client(base_url=settings.llm_base_url, timeout=120.0)
    github_http = github_http_client(github_token)
    try:
        compiler = CompileService(
            db.problems,
            db.runs,
            FixtureKnowledgeSource(
                REPO_ROOT / "fixtures" / "github",
                repositories,
                k=config.yaml.k,
                readme_max_chars=config.yaml.readme_max_chars,
                issue_body_max_chars=config.yaml.issue_body_max_chars,
            ),
            HttpLlmJudgment(
                llm_http,
                model=settings.llm_model,
                api_key=settings.llm_api_key,
            ),
            repositories,
            debug_root=REPO_ROOT / "debug",
        )
        execution = ExecutionService(
            GitHubIssueTracker(GitHubClient(github_http)),
            db.problems,
            db.runs,
            repositories,
        )
        orchestrator = ApplicationOrchestrator(compiler, execution, db.runs)
        gateway = TelegramBotGateway()
        bot_handlers = BotHandlers(
            gateway=gateway,
            orchestrator=orchestrator,
            problems=db.problems,
            group_chat_id=group_chat_id,
            owner_user_ids=settings.owner_user_ids,
        )
        application = Application.builder().token(token).build()
        configure_application(application, gateway)
        register_handlers(application, bot_handlers)
        application.run_polling()
    finally:
        llm_http.close()
        github_http.close()
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
