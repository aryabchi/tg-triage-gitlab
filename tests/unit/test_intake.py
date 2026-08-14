"""Intake stores group text as ingested Problems and is idempotent on message id."""

from __future__ import annotations

from datetime import datetime

from tg_triage.application.intake import ingest_group_text
from tg_triage.domain import ProblemLifecycle
from tests.fakes import FakeProblemRepository

SAMPLE_TEXT = "Экспорт дашборда продаж больше не работает.\nКрутится загрузка."
CREATED_AT = datetime(2026, 8, 13, 10, 15, 0)


def test_ingest_preserves_text_and_ingested_lifecycle() -> None:
    repo = FakeProblemRepository()
    stored = ingest_group_text(
        repo,
        SAMPLE_TEXT,
        user_id=7,
        message_id=501,
        chat_id=100,
        created_at=CREATED_AT,
    )
    assert stored.original_text == SAMPLE_TEXT
    assert stored.lifecycle == ProblemLifecycle.INGESTED
    assert stored.linked_issue is None
    assert stored.user_id == 7
    assert stored.message_id == 501
    assert stored.chat_id == 100
    assert stored.created_at == CREATED_AT


def test_duplicate_message_id_returns_same_row() -> None:
    repo = FakeProblemRepository()
    first = ingest_group_text(
        repo,
        SAMPLE_TEXT,
        user_id=7,
        message_id=501,
        chat_id=100,
        created_at=CREATED_AT,
    )
    second = ingest_group_text(
        repo,
        "другой текст",
        user_id=8,
        message_id=501,
        chat_id=100,
        created_at=datetime(2026, 8, 13, 11, 0, 0),
    )
    assert second.id == first.id
    assert second.original_text == SAMPLE_TEXT
    assert repo.list_ingested_since(datetime(2026, 8, 13)) == (first,)


def test_intake_does_not_filter_command_text() -> None:
    repo = FakeProblemRepository()
    stored = ingest_group_text(
        repo,
        "/compile 2026-08-13",
        user_id=7,
        message_id=502,
        chat_id=100,
        created_at=CREATED_AT,
    )
    assert stored.original_text == "/compile 2026-08-13"
    assert stored.lifecycle == ProblemLifecycle.INGESTED
