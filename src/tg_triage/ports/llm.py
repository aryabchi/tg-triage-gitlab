"""LLM judgment port. The application validates JSON; the model never writes Markdown."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from tg_triage.domain import EvidencePack, Problem


@dataclass(frozen=True, slots=True)
class ClusterItem:
    """One cluster: a summary and the Problem ids it covers."""

    summary: str
    problem_ids: tuple[int, ...]


class LlmJudgment(Protocol):
    """Two-stage semantic judgments. Returns model JSON text, not a Markdown file."""

    def cluster(self, problems: Sequence[Problem]) -> str:
        """Return JSON text grouping related reports. No fixture identities in this call."""

    def match(self, items: Sequence[ClusterItem], evidence_pack: EvidencePack) -> str:
        """Return JSON text proposing create, skip, or uncertain against the snapshot."""
