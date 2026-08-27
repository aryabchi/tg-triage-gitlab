"""Telegram handlers with a fake gateway. No Bot API network."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tg_triage.application.compile import CompileService
from tg_triage.application.execute import ExecutionService
from tg_triage.application.orchestrator import ApplicationOrchestrator
from tg_triage.config import DEFAULT_YAML_PATH, load_yaml_config
from tg_triage.domain import TriageRunStatus
from tg_triage.infrastructure.fixtures import FixtureKnowledgeSource
from tg_triage.infrastructure.sqlite import SqliteDb
from tg_triage.infrastructure.telegram.handlers import (
    BotHandlers,
    IncomingCallback,
    IncomingMessage,
)
from tests.fakes import FakeIssueTracker, FakeLlmJudgment, FakeTelegramGateway

CANNED = Path(__file__).resolve().parents[1] / "canned_llm"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github"
ALLOWED = load_yaml_config(DEFAULT_YAML_PATH).repositories
CREATED = datetime(2026, 8, 13, 10, 0, 0)
GROUP = -100
OWNER_CHAT = 42
OWNER = 42
REPORTER = 7
OTHER_CHAT = 999
TEXTS = (
    "Экспорт дашборда продаж больше не работает. Крутится загрузка.",
    "На дашборде продаж экспорт зависает на спиннере. Нужен отчёт к планерке.",
    "Синхронизация клиентов задерживается.",
    "CSV-экспорт дашборда всё ещё висит — как раньше.",
)
CRM_BODY = "Синхронизация клиентов задерживается. Нужна проверка CRM."


def _canned(name: str) -> str:
    return (CANNED / name).read_text(encoding="utf-8")


def _remap(payload: str, mapping: dict[int, int]) -> str:
    data = json.loads(payload)
    for item in data["items"]:
        item["problem_ids"] = [mapping[pid] for pid in item["problem_ids"]]
    return json.dumps(data, ensure_ascii=False)


def _llm(mapping: dict[int, int]) -> FakeLlmJudgment:
    return FakeLlmJudgment(
        cluster_replies=[_remap(_canned("cluster.json"), mapping)],
        match_replies=[_remap(_canned("match.json"), mapping)],
    )


def _group_text(text: str, message_id: int) -> IncomingMessage:
    return IncomingMessage(
        chat_id=GROUP,
        user_id=REPORTER,
        message_id=message_id,
        created_at=CREATED,
        text=text,
        is_private=False,
    )


def _owner_text(text: str, message_id: int = 50) -> IncomingMessage:
    return IncomingMessage(
        chat_id=OWNER_CHAT,
        user_id=OWNER,
        message_id=message_id,
        created_at=CREATED,
        text=text,
        is_private=True,
    )


def _harness(tmp_path: Path, llm: FakeLlmJudgment | None = None) -> tuple[
    BotHandlers,
    FakeTelegramGateway,
    FakeIssueTracker,
    FakeLlmJudgment,
    SqliteDb,
]:
    gateway = FakeTelegramGateway()
    tracker = FakeIssueTracker()
    llm = llm or FakeLlmJudgment()
    db = SqliteDb(tmp_path / "store.sqlite")
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
    handlers = BotHandlers(
        gateway=gateway,
        orchestrator=orch,
        problems=db.problems,
        group_chat_id=GROUP,
        owner_user_ids=(OWNER,),
    )
    return handlers, gateway, tracker, llm, db


def _ingest_scenario(handlers: BotHandlers, db: SqliteDb) -> dict[int, int]:
    for index, text in enumerate(TEXTS, start=1):
        handlers.on_message(_group_text(text, index))
    problems = db.problems.list_ingested_since(CREATED.replace(hour=0, minute=0, second=0))
    return {canned: stored.id for canned, stored in zip((101, 102, 103, 104), problems)}


def _owner_edit(markdown: str) -> bytes:
    prefix, separator, uncertain = markdown.partition("\n## Uncertain\n")
    if not separator:
        raise AssertionError("generated markdown has no Uncertain section")
    block = uncertain.strip("\n").replace("- Repo: unknown", "- Repo: acme/crm", 1)
    if "- Body:" not in block:
        block = f"{block}\n- Body: |\n    {CRM_BODY}"
    create_end = prefix.index("\n## Skip\n")
    return f"{prefix[:create_end].rstrip()}\n\n{block}\n{prefix[create_end:].rstrip()}\n".encode(
        "utf-8"
    )


def test_group_text_becomes_problem(tmp_path: Path) -> None:
    handlers, gateway, _tracker, llm, db = _harness(tmp_path)
    handlers.on_message(_group_text(TEXTS[0], 1))
    stored = db.problems.list_ingested_since(CREATED.replace(hour=0))
    assert len(stored) == 1
    assert stored[0].original_text == TEXTS[0]
    assert gateway.texts == []
    assert llm.cluster_calls == 0
    db.close()


def test_group_compile_rejects_and_does_not_ingest(tmp_path: Path) -> None:
    handlers, gateway, _tracker, llm, db = _harness(tmp_path)
    handlers.on_message(_group_text("/compile 2026-08-13", 1))
    assert db.problems.list_ingested_since(CREATED.replace(hour=0)) == ()
    assert gateway.texts == [
        (GROUP, "Use a private chat with the bot for /compile and /execute.")
    ]
    assert llm.cluster_calls == 0
    db.close()


def test_group_document_is_not_a_problem(tmp_path: Path) -> None:
    handlers, gateway, _tracker, _llm, db = _harness(tmp_path)
    handlers.on_message(
        IncomingMessage(
            chat_id=GROUP,
            user_id=REPORTER,
            message_id=1,
            created_at=CREATED,
            is_private=False,
            document_bytes=b"# not a problem",
            document_filename="note.md",
        )
    )
    assert db.problems.list_ingested_since(CREATED.replace(hour=0)) == ()
    assert gateway.documents == []
    db.close()


def test_other_chat_text_is_not_a_problem(tmp_path: Path) -> None:
    handlers, _gateway, _tracker, _llm, db = _harness(tmp_path)
    handlers.on_message(
        IncomingMessage(
            chat_id=OTHER_CHAT,
            user_id=REPORTER,
            message_id=1,
            created_at=CREATED,
            text=TEXTS[0],
            is_private=False,
        )
    )
    assert db.problems.list_ingested_since(CREATED.replace(hour=0)) == ()
    db.close()


def test_owner_compile_sends_unparsed_markdown(tmp_path: Path) -> None:
    handlers, gateway, _tracker, llm, db = _harness(tmp_path)
    mapping = _ingest_scenario(handlers, db)
    llm.cluster_replies = [_remap(_canned("cluster.json"), mapping)]
    llm.match_replies = [_remap(_canned("match.json"), mapping)]
    handlers.on_message(_owner_text("/compile 2026-08-13"))
    assert len(gateway.documents) == 1
    sent = gateway.documents[0]
    assert sent.chat_id == OWNER_CHAT
    assert sent.filename.startswith("triage-run-")
    assert sent.filename.endswith(".md")
    assert sent.content.startswith(b"# GitHub Issues")
    db.close()


def test_owner_upload_marks_run_awaiting_execute(tmp_path: Path) -> None:
    handlers, gateway, _tracker, llm, db = _harness(tmp_path)
    mapping = _ingest_scenario(handlers, db)
    llm.cluster_replies = [_remap(_canned("cluster.json"), mapping)]
    llm.match_replies = [_remap(_canned("match.json"), mapping)]
    handlers.on_message(_owner_text("/compile 2026-08-13"))
    generated = gateway.documents[0].content
    handlers.on_message(
        IncomingMessage(
            chat_id=OWNER_CHAT,
            user_id=OWNER,
            message_id=60,
            created_at=CREATED,
            is_private=True,
            document_bytes=_owner_edit(generated.decode("utf-8")),
            document_filename="triage-run-1.md",
        )
    )
    run = db.runs.get(1)
    assert run is not None
    assert run.status == TriageRunStatus.AWAITING_EXECUTE
    assert run.uploaded_markdown is not None
    db.close()


def test_execute_asks_confirm_with_item_counts(tmp_path: Path) -> None:
    handlers, gateway, tracker, llm, db = _harness(tmp_path)
    mapping = _ingest_scenario(handlers, db)
    llm.cluster_replies = [_remap(_canned("cluster.json"), mapping)]
    llm.match_replies = [_remap(_canned("match.json"), mapping)]
    handlers.on_message(_owner_text("/compile 2026-08-13"))
    handlers.on_message(
        IncomingMessage(
            chat_id=OWNER_CHAT,
            user_id=OWNER,
            message_id=60,
            created_at=CREATED,
            is_private=True,
            document_bytes=_owner_edit(gateway.documents[0].content.decode("utf-8")),
            document_filename="edited.md",
        )
    )
    handlers.on_message(_owner_text("/execute", message_id=70))
    assert tracker.calls == []
    assert len(gateway.confirms) == 1
    prompt = gateway.confirms[0]
    assert prompt.chat_id == OWNER_CHAT
    assert "Create: 2" in prompt.text
    assert "Skip: 1" in prompt.text
    assert "Uncertain: 0" in prompt.text
    db.close()


def test_confirm_calls_fake_issue_tracker(tmp_path: Path) -> None:
    handlers, gateway, tracker, llm, db = _harness(tmp_path)
    mapping = _ingest_scenario(handlers, db)
    llm.cluster_replies = [_remap(_canned("cluster.json"), mapping)]
    llm.match_replies = [_remap(_canned("match.json"), mapping)]
    handlers.on_message(_owner_text("/compile 2026-08-13"))
    handlers.on_message(
        IncomingMessage(
            chat_id=OWNER_CHAT,
            user_id=OWNER,
            message_id=60,
            created_at=CREATED,
            is_private=True,
            document_bytes=_owner_edit(gateway.documents[0].content.decode("utf-8")),
            document_filename="edited.md",
        )
    )
    handlers.on_message(_owner_text("/execute", message_id=70))
    run_id = gateway.confirms[0].run_id
    handlers.on_callback(
        IncomingCallback(chat_id=OWNER_CHAT, user_id=OWNER, data=f"confirm:{run_id}")
    )
    assert [call.title for call in tracker.calls] == [
        "Sales dashboard export hangs",
        "Синхронизация клиентов задерживается",
    ]
    assert any("Created" in text for _chat, text in gateway.texts)
    db.close()


def test_non_owner_dm_compile_does_not_compile(tmp_path: Path) -> None:
    handlers, gateway, _tracker, llm, db = _harness(tmp_path)
    handlers.on_message(
        IncomingMessage(
            chat_id=OWNER_CHAT,
            user_id=99,
            message_id=1,
            created_at=CREATED,
            text="/compile 2026-08-13",
            is_private=True,
        )
    )
    assert llm.cluster_calls == 0
    assert gateway.documents == []
    assert gateway.texts == [(OWNER_CHAT, "Not authorized.")]
    db.close()


def test_owner_compile_llm_error_sends_failed_text(tmp_path: Path) -> None:
    handlers, gateway, _tracker, _llm, db = _harness(tmp_path)
    _ingest_scenario(handlers, db)
    handlers.on_message(_owner_text("/compile 2026-08-13"))
    assert gateway.documents == []
    assert (OWNER_CHAT, "Compile failed. No document sent.") in gateway.texts
    run = db.runs.get(1)
    assert run is not None
    assert run.status == TriageRunStatus.FAILED
    assert run.generated_markdown is None
    db.close()


def test_register_handlers_installs_error_handler() -> None:
    from telegram.ext import Application

    from tg_triage.infrastructure.telegram.adapter import register_handlers

    application = Application.builder().token("123:ABC").build()
    register_handlers(application, _UnusedHandlers())
    assert application.error_handlers


def test_build_application_uses_longer_connect_timeout() -> None:
    from tg_triage.infrastructure.telegram.adapter import build_application

    application = build_application("123:ABC")
    timeout = application.bot.request._client_kwargs["timeout"]
    assert timeout.connect == 30.0
    assert timeout.read == 30.0


def test_error_handler_does_not_reply_on_timeout() -> None:
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from telegram import Update
    from telegram.error import TimedOut

    from tg_triage.infrastructure.telegram.adapter import _on_telegram_error

    update = MagicMock(spec=Update)
    update.effective_chat.id = 42
    context = MagicMock()
    context.error = TimedOut("Timed out")
    context.bot.send_message = AsyncMock()
    asyncio.run(_on_telegram_error(update, context))
    context.bot.send_message.assert_not_called()


class _UnusedHandlers:
    """Stub passed only so handler registration can close over an object."""

    def on_message(self, message: object) -> None:
        """Unused. Registration only closes over this object."""
        return None

    def on_callback(self, callback: object) -> None:
        """Unused. Registration only closes over this object."""
        return None
