"""Application use cases. Sync, port-driven, no adapter imports."""

from tg_triage.application.compile import CompileResult, CompileService
from tg_triage.application.execute import ExecutionService
from tg_triage.application.intake import ingest_group_text

__all__ = ["CompileResult", "CompileService", "ExecutionService", "ingest_group_text"]
