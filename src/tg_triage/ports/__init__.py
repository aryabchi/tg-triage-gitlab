"""Persistence ports. Adapters implement these; application code depends only on them."""

from tg_triage.ports.repositories import ProblemRepository, TriageRunRepository

__all__ = ["ProblemRepository", "TriageRunRepository"]
