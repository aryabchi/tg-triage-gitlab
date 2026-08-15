"""OpenAI-compatible chat.completions client for cluster and match.

Builds messages from versioned prompt files. Does not send a tracker token.
Temperature is 0. JSON ``response_format`` is requested when the API allows it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import httpx

from tg_triage.domain import EvidencePack, EvidenceSnippet, Problem
from tg_triage.ports.llm import ClusterItem

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

    def cluster(self, problems: Sequence[Problem]) -> str:
        """Ask the model to cluster reports. Returns JSON text, not yet validated."""
        payload = json.dumps(
            [{"id": problem.id, "original_text": problem.original_text} for problem in problems],
            ensure_ascii=False,
        )
        return self._complete("cluster.md", payload)

    def match(self, items: Sequence[ClusterItem], evidence_pack: EvidencePack) -> str:
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
        return self._complete("match.md", payload)

    def _complete(self, prompt_name: str, payload: str) -> str:
        """POST chat.completions and return the assistant message content."""
        system, user = _render_prompt(self._prompts_dir / prompt_name, payload)
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
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
        body = response.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("LLM response missing choices[0].message.content") from exc
        if not isinstance(content, str):
            raise ValueError("LLM message content must be a string")
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
