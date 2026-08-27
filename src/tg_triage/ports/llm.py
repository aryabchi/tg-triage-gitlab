"""LLM judgment port. The application validates JSON; the model never writes Markdown."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from tg_triage.domain import EvidencePack, Problem


class LlmCallError(Exception):
    """The judgment adapter could not complete a cluster or match call.

    ``retryable`` is True for transient failures such as HTTP 429 or a dropped
    connection. Permanent failures (auth, 4xx other than 408/429) are not.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        """Store whether compile should retry this failure once."""
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ClusterItem:
    """One cluster: a summary and the Problem ids it covers."""

    summary: str
    problem_ids: tuple[int, ...]


class LlmJudgment(Protocol):
    """Two-stage semantic judgments. Returns model JSON text, not a Markdown file."""

    def cluster(self, problems: Sequence[Problem], *, retry: bool = False) -> str:
        """Return JSON text grouping related reports. No fixture identities in this call."""

    def match(
        self,
        items: Sequence[ClusterItem],
        evidence_pack: EvidencePack,
        *,
        retry: bool = False,
    ) -> str:
        """Return JSON text proposing create, skip, or uncertain against the snapshot."""
