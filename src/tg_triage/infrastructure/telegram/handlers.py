"""Sync Telegram command handlers. Allowlisting lives here, not in the domain.

This module does not import ``python-telegram-bot``. Tests call these methods
with a fake gateway and synthetic messages.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime

from tg_triage.application.intake import ingest_group_text
from tg_triage.application.orchestrator import ApplicationOrchestrator, OrchestratorError
from tg_triage.domain import ExecutionAction, TriageOutcome, TriageRunStatus
from tg_triage.markdown import PlanValidationError
from tg_triage.ports.repositories import ProblemRepository
from tg_triage.ports.telegram import TelegramGateway

_COMMAND = re.compile(r"^/([A-Za-z]+)(?:@\S+)?(?:\s+(.*))?$", re.DOTALL)
_RUN_ID = re.compile(r"(\d+)")


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    """One user message after the PTB adapter has stripped vendor types."""

    chat_id: int
    user_id: int
    message_id: int
    created_at: datetime
    text: str | None = None
    is_private: bool = False
    document_bytes: bytes | None = None
    document_filename: str | None = None
    caption: str | None = None


@dataclass(frozen=True, slots=True)
class IncomingCallback:
    """Confirm or Cancel tap. ``data`` is ``confirm:<run_id>`` or ``cancel:<run_id>``."""

    chat_id: int
    user_id: int
    data: str


class BotHandlers:
    """Map group text, owner DMs, uploads, and callbacks onto application use cases."""

    def __init__(
        self,
        *,
        gateway: TelegramGateway,
        orchestrator: ApplicationOrchestrator,
        problems: ProblemRepository,
        group_chat_id: int,
        owner_user_ids: Collection[int],
    ) -> None:
        self._gateway = gateway
        self._orchestrator = orchestrator
        self._problems = problems
        self._group_chat_id = group_chat_id
        self._owners = frozenset(owner_user_ids)

    def on_message(self, message: IncomingMessage) -> None:
        """Dispatch one message. Commands and documents never become Problems."""
        if message.document_bytes is not None:
            self._on_document(message)
            return
        text = (message.text or "").strip()
        if not text:
            return
        parsed = _parse_command(text)
        if parsed is not None:
            self._on_command(message, parsed[0], parsed[1])
            return
        if message.chat_id == self._group_chat_id and not message.is_private:
            ingest_group_text(
                self._problems,
                text,
                user_id=message.user_id,
                message_id=message.message_id,
                chat_id=message.chat_id,
                created_at=message.created_at,
            )

    def on_callback(self, callback: IncomingCallback) -> None:
        """Confirm or cancel after preview. Non-owners are refused."""
        if callback.user_id not in self._owners:
            self._gateway.send_text(callback.chat_id, "Not authorized.")
            return
        action, _, raw_id = callback.data.partition(":")
        try:
            run_id = int(raw_id)
        except ValueError:
            self._gateway.send_text(callback.chat_id, "Unknown action.")
            return
        if action == "confirm":
            self._confirm(callback.chat_id, run_id)
        elif action == "cancel":
            self._cancel(callback.chat_id, run_id)

    def _on_command(self, message: IncomingMessage, name: str, argument: str) -> None:
        """Handle /compile and /execute. Other commands are ignored."""
        if name not in {"compile", "execute"}:
            return
        if not message.is_private:
            if message.chat_id == self._group_chat_id:
                self._gateway.send_text(
                    message.chat_id,
                    "Use a private chat with the bot for /compile and /execute.",
                )
            return
        if message.user_id not in self._owners:
            self._gateway.send_text(message.chat_id, "Not authorized.")
            return
        if name == "compile":
            self._compile(message.chat_id, message.user_id, argument)
            return
        self._execute(message.chat_id, argument)

    def _on_document(self, message: IncomingMessage) -> None:
        """Store an owner DM ``.md`` upload. Group documents are ignored."""
        if not message.is_private:
            return
        if message.user_id not in self._owners:
            self._gateway.send_text(message.chat_id, "Not authorized.")
            return
        filename = message.document_filename or ""
        if not filename.lower().endswith(".md"):
            self._gateway.send_text(message.chat_id, "Upload a .md file.")
            return
        run_id = _run_id_from_caption(message.caption)
        try:
            if run_id is None:
                run_id = self._orchestrator.latest_open_run_id()
            stored = self._orchestrator.store_upload(run_id, message.document_bytes or b"")
        except OrchestratorError as exc:
            self._gateway.send_text(message.chat_id, str(exc))
            return
        self._gateway.send_text(
            message.chat_id,
            f"Upload stored for run {stored.id}. Send /execute to preview.",
        )

    def _compile(self, chat_id: int, owner_id: int, argument: str) -> None:
        """Run compile and send the unparsed Markdown document."""
        if not argument:
            self._gateway.send_text(chat_id, "Usage: /compile YYYY-MM-DD")
            return
        try:
            since = date.fromisoformat(argument.split()[0])
        except ValueError:
            self._gateway.send_text(chat_id, "Usage: /compile YYYY-MM-DD")
            return
        result = self._orchestrator.compile(owner_id, since)
        if result.superseded_warning:
            self._gateway.send_text(chat_id, "Previous pending run was superseded.")
        if result.no_problems:
            self._gateway.send_text(chat_id, "No ingested problems in that period.")
            return
        run = result.run
        if run is None or run.status == TriageRunStatus.FAILED or run.generated_markdown is None:
            self._gateway.send_text(chat_id, "Compile failed. No document sent.")
            return
        self._gateway.send_document(
            chat_id,
            f"triage-run-{run.id}.md",
            run.generated_markdown,
            caption=f"run: {run.id}",
        )

    def _execute(self, chat_id: int, argument: str) -> None:
        """Preview the stored upload and ask Confirm/Cancel."""
        run_id = int(argument) if argument else None
        try:
            plan = self._orchestrator.execute_preview(run_id)
        except (OrchestratorError, PlanValidationError, ValueError) as exc:
            self._gateway.send_text(chat_id, str(exc))
            return
        resolved = run_id if run_id is not None else plan.run_id
        if resolved is None:
            self._gateway.send_text(chat_id, "unknown run")
            return
        creates = sum(1 for item in plan.items if item.outcome == TriageOutcome.CREATE_NEW)
        skips = sum(1 for item in plan.items if item.outcome == TriageOutcome.LINK_EXISTING)
        uncertains = sum(1 for item in plan.items if item.outcome == TriageOutcome.UNCERTAIN)
        self._gateway.ask_confirm(
            chat_id,
            f"Create: {creates}. Skip: {skips}. Uncertain: {uncertains}.",
            resolved,
        )

    def _confirm(self, chat_id: int, run_id: int) -> None:
        """Execute the uploaded plan and reply with create/skip results."""
        try:
            run = self._orchestrator.confirm(run_id)
        except (OrchestratorError, PlanValidationError, ValueError, KeyError) as exc:
            self._gateway.send_text(chat_id, str(exc))
            return
        lines = ["Executed."]
        result = run.execution_result
        if result is not None:
            for row in result.items:
                if row.action == ExecutionAction.CREATE:
                    lines.append(f"Created {row.issue}: {row.url}")
                else:
                    lines.append(f"Skipped {row.issue}")
        self._gateway.send_text(chat_id, "\n".join(lines))

    def _cancel(self, chat_id: int, run_id: int) -> None:
        """Cancel with no tracker writes."""
        try:
            self._orchestrator.cancel(run_id)
        except OrchestratorError as exc:
            self._gateway.send_text(chat_id, str(exc))
            return
        self._gateway.send_text(chat_id, "Cancelled. No GitHub writes.")


def _parse_command(text: str) -> tuple[str, str] | None:
    """Return ``(name, argument)`` when ``text`` is a slash command."""
    match = _COMMAND.match(text.strip())
    if match is None:
        return None
    argument = match.group(2) or ""
    return match.group(1).lower(), argument.strip()


def _run_id_from_caption(caption: str | None) -> int | None:
    """Read a run id from an upload caption when the owner included one."""
    if not caption:
        return None
    match = _RUN_ID.search(caption)
    if match is None:
        return None
    return int(match.group(1))
