"""Queued LLM JSON replies for tests. No network."""

from __future__ import annotations

from collections.abc import Sequence

from tg_triage.domain import EvidencePack, Problem
from tg_triage.ports.llm import ClusterItem


class FakeLlmJudgment:
    """Returns scripted cluster/match JSON strings in order.

    Queue a non-JSON string then a valid document to exercise retry.
    """

    def __init__(
        self,
        *,
        cluster_replies: Sequence[str] | None = None,
        match_replies: Sequence[str] | None = None,
    ) -> None:
        self.cluster_replies = list(cluster_replies or [])
        self.match_replies = list(match_replies or [])
        self.cluster_calls = 0
        self.match_calls = 0

    def cluster(self, problems: Sequence[Problem]) -> str:
        """Pop the next cluster JSON string."""
        self.cluster_calls += 1
        if not self.cluster_replies:
            raise IndexError("FakeLlmJudgment has no cluster replies left")
        return self.cluster_replies.pop(0)

    def match(self, items: Sequence[ClusterItem], evidence_pack: EvidencePack) -> str:
        """Pop the next match JSON string."""
        self.match_calls += 1
        if not self.match_replies:
            raise IndexError("FakeLlmJudgment has no match replies left")
        return self.match_replies.pop(0)
