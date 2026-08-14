"""Application use cases. Sync, port-driven, no adapter imports."""

from tg_triage.application.intake import ingest_group_text

__all__ = ["ingest_group_text"]
