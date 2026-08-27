"""Outbound Telegram port. Domain types never appear here as vendor objects."""

from __future__ import annotations

from typing import Protocol


class TelegramGateway(Protocol):
    """Send unparsed documents, short texts, and Confirm/Cancel prompts."""

    def send_text(self, chat_id: int, text: str) -> None:
        """Send a plain chat message. No parse_mode."""

    def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        caption: str | None = None,
    ) -> None:
        """Send an unparsed ``.md`` document. Callers must not set parse_mode."""

    def ask_confirm(self, chat_id: int, text: str, run_id: int) -> None:
        """Prompt Confirm/Cancel for ``run_id`` after an execute preview."""
