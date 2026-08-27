"""OpenAI-compatible chat.completions client for cluster and match.

Builds messages from versioned prompt files. Does not send a tracker token.
Temperature is 0. JSON ``response_format`` is requested when the API allows it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

import httpx

from tg_triage.domain import EvidencePack, EvidenceSnippet, Problem
from tg_triage.ports.llm import ClusterItem, LlmCallError

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class HttpLlmJudgment:
    """LLM adapter over one OpenAI-compatible HTTP client."""

    def __init__(
        self,
        http: httpx.Client,
        *,
        model: str,
        api_key: str | None = None,
        prompts_dir: Path | None = None,
    ) -> None:
        self._http = http
        self._model = model
        self._api_key = api_key
        self._prompts_dir = prompts_dir or PROMPTS_DIR

    def cluster(self, problems: Sequence[Problem], *, retry: bool = False) -> str:
        """Ask the model to cluster reports. Returns JSON text, not yet validated."""
        payload = json.dumps(
            [{"id": problem.id, "original_text": problem.original_text} for problem in problems],
            ensure_ascii=False,
        )
        return self._complete("cluster.md", payload, retry=retry)

    def match(
        self,
        items: Sequence[ClusterItem],
        evidence_pack: EvidencePack,
        *,
        retry: bool = False,
    ) -> str:
        """Ask the model to match clusters to the snapshot. Returns JSON text."""
        payload = json.dumps(
            {
                "items": [
                    {"summary": item.summary, "problem_ids": list(item.problem_ids)}
                    for item in items
                ],
                "evidence": [_snippet_payload(snippet) for snippet in evidence_pack.snippets],
            },
            ensure_ascii=False,
        )
        return self._complete("match.md", payload, retry=retry)

    def _complete(self, prompt_name: str, payload: str, *, retry: bool = False) -> str:
        """POST chat.completions and return the assistant message content."""
        system, user = _render_prompt(self._prompts_dir / prompt_name, payload)
        if retry:
            user = f"{user}\nReturn valid JSON matching schema."
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        logger.info("LLM %s request starting (retry=%s)", prompt_name, retry)
        try:
            response = self._http.post(
                "/chat/completions",
                headers=headers,
                json={
                    "model": self._model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise LlmCallError(
                f"LLM HTTP {code}",
                retryable=code in _RETRYABLE_STATUS,
            ) from exc
        except httpx.RequestError as exc:
            retryable = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            raise LlmCallError(
                f"LLM request failed: {type(exc).__name__}: {exc}",
                retryable=retryable,
            ) from exc
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmCallError(
                "LLM response missing choices[0].message.content",
                retryable=False,
            ) from exc
        if not isinstance(content, str):
            raise LlmCallError("LLM message content must be a string", retryable=False)
        return content


def _render_prompt(path: Path, payload: str) -> tuple[str, str]:
    """Split a prompt file into system and user text, substituting ``{payload}``."""
    text = path.read_text(encoding="utf-8")
    system = _section(text, "system")
    user = _section(text, "user").replace("{payload}", payload)
    return system, user


def _section(text: str, name: str) -> str:
    """Return the markdown section headed ``## name``."""
    marker = f"## {name}"
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"prompt missing ## {name} section")
    rest = text[start + len(marker) :]
    next_heading = rest.find("\n## ")
    body = rest if next_heading < 0 else rest[:next_heading]
    return body.strip()


def _snippet_payload(snippet: EvidenceSnippet) -> dict[str, object]:
    """JSON view of one evidence snippet for the match prompt."""
    return {
        "kind": snippet.kind.value,
        "repository": str(snippet.locator),
        "issue_number": snippet.issue_number,
        "title": snippet.title,
        "text": snippet.text,
    }
