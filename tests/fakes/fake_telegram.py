"""Recording TelegramGateway. No Bot API calls."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SentDocument:
    """One send_document invocation."""

    chat_id: int
    filename: str
    content: bytes
    caption: str | None


@dataclass(frozen=True, slots=True)
class ConfirmPrompt:
    """One ask_confirm invocation."""

    chat_id: int
    text: str
    run_id: int


class FakeTelegramGateway:
    """Records outbound sends for handler tests."""

    def __init__(self) -> None:
        self.texts: list[tuple[int, str]] = []
        self.documents: list[SentDocument] = []
        self.confirms: list[ConfirmPrompt] = []

    def send_text(self, chat_id: int, text: str) -> None:
        """Record a plain message."""
        self.texts.append((chat_id, text))

    def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        caption: str | None = None,
    ) -> None:
        """Record an unparsed document send."""
        self.documents.append(
            SentDocument(chat_id=chat_id, filename=filename, content=content, caption=caption)
        )

    def ask_confirm(self, chat_id: int, text: str, run_id: int) -> None:
        """Record a Confirm/Cancel prompt."""
        self.confirms.append(ConfirmPrompt(chat_id=chat_id, text=text, run_id=run_id))
