"""HTTP LLM client against MockTransport. No live provider."""

from __future__ import annotations

import json
from datetime import datetime

import httpx
import pytest

from tg_triage.domain import EvidencePack, Problem
from tg_triage.llm.http_client import HttpLlmJudgment
from tg_triage.ports.llm import ClusterItem, LlmCallError

CREATED_AT = datetime(2026, 8, 13, 10, 0, 0)


def _problems() -> tuple[Problem, ...]:
    return (
        Problem(
            id=101,
            original_text="Экспорт дашборда продаж больше не работает.",
            message_id=1,
            chat_id=1,
            user_id=7,
            created_at=CREATED_AT,
        ),
    )


def test_http_mock_returns_parsed_json_content() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"items": []}'}}]},
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    llm = HttpLlmJudgment(http, model="openai/gpt-oss-20b:free", api_key="test-key")
    content = llm.cluster(_problems())
    assert json.loads(content) == {"items": []}
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    messages = body["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"


def test_match_prompt_includes_pack_identity_rule() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    llm = HttpLlmJudgment(http, model="openai/gpt-oss-20b:free")
    llm.match((ClusterItem(summary="export", problem_ids=(101,)),), EvidencePack())
    body = captured["body"]
    assert isinstance(body, dict)
    system = body["messages"][0]["content"]
    assert "only identities present in the pack" in system.lower()
    assert "existing: null" in system.lower()
    assert 'repository must be "unknown"' in system.lower()


def test_http_500_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="unavailable")

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    llm = HttpLlmJudgment(http, model="openai/gpt-oss-20b:free")
    with pytest.raises(LlmCallError) as caught:
        llm.cluster(_problems())
    assert caught.value.retryable is True


def test_http_429_is_retryable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    llm = HttpLlmJudgment(http, model="openai/gpt-oss-20b:free")
    with pytest.raises(LlmCallError) as caught:
        llm.cluster(_problems())
    assert caught.value.retryable is True


def test_http_401_is_not_retryable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    llm = HttpLlmJudgment(http, model="openai/gpt-oss-20b:free")
    with pytest.raises(LlmCallError) as caught:
        llm.cluster(_problems())
    assert caught.value.retryable is False


def test_read_timeout_is_not_retryable() -> None:
    """A hung match read fails closed instead of starting a second equally long wait."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    llm = HttpLlmJudgment(http, model="openai/gpt-oss-20b:free")
    with pytest.raises(LlmCallError) as caught:
        llm.cluster(_problems())
    assert caught.value.retryable is False
    assert "ReadTimeout" in str(caught.value)


def test_connect_error_is_retryable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("getaddrinfo failed")

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1",
    )
    llm = HttpLlmJudgment(http, model="openai/gpt-oss-20b:free")
    with pytest.raises(LlmCallError) as caught:
        llm.cluster(_problems())
    assert caught.value.retryable is True
    assert "ConnectError" in str(caught.value)
