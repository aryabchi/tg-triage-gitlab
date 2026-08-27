"""python-telegram-bot wiring: Update objects in, gateway sends out.

Handlers stay sync. This module downloads document bytes and runs handlers
in a worker thread so application code does not become async.
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Awaitable
from datetime import datetime, timezone

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ChatType
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from tg_triage.infrastructure.telegram.handlers import (
    BotHandlers,
    IncomingCallback,
    IncomingMessage,
)

_FLUSH_TIMEOUT = 60.0
_BOT_CONNECT_TIMEOUT = 30.0
_BOT_READ_TIMEOUT = 30.0
logger = logging.getLogger(__name__)


class TelegramBotGateway:
    """Outbound Bot API calls. Bound after the Application event loop starts."""

    def __init__(self) -> None:
        self._bot: Bot | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, bot: Bot, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the running bot. Required before any send."""
        self._bot = bot
        self._loop = loop

    def send_text(self, chat_id: int, text: str) -> None:
        """Send a plain message with no parse_mode."""
        logger.info("Telegram: sending text to chat %s", chat_id)
        self._await(self._require_bot().send_message(chat_id=chat_id, text=text))

    def send_document(
        self,
        chat_id: int,
        filename: str,
        content: bytes,
        caption: str | None = None,
    ) -> None:
        """Send an unparsed document (parse_mode left unset)."""
        logger.info("Telegram: sending document %s", filename)
        document = InputFile(io.BytesIO(content), filename=filename)
        self._await(
            self._require_bot().send_document(
                chat_id=chat_id,
                document=document,
                caption=caption,
            )
        )

    def ask_confirm(self, chat_id: int, text: str, run_id: int) -> None:
        """Send Confirm/Cancel inline buttons for ``run_id``."""
        logger.info("Telegram: asking confirm for run %s", run_id)
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Confirm", callback_data=f"confirm:{run_id}"),
                    InlineKeyboardButton("Cancel", callback_data=f"cancel:{run_id}"),
                ]
            ]
        )
        self._await(
            self._require_bot().send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=markup,
            )
        )

    def _require_bot(self) -> Bot:
        """Return the bound bot.

        Raises:
            RuntimeError: If ``bind`` has not run.
        """
        if self._bot is None:
            raise RuntimeError("Telegram gateway is not bound")
        return self._bot

    def _await(self, coro: Awaitable[object]) -> None:
        """Run a Bot coroutine on the polling loop from a worker thread."""
        if self._loop is None:
            raise RuntimeError("Telegram gateway is not bound")
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=_FLUSH_TIMEOUT)
        except (NetworkError, TimeoutError) as exc:
            logger.warning("Telegram outbound call timed out: %s", exc)
            raise


def incoming_from_update(
    update: Update,
    *,
    document_bytes: bytes | None = None,
) -> IncomingMessage:
    """Map a PTB Update onto the sync handler's message shape.

    Raises:
        ValueError: If chat, user, or message is missing.
    """
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        raise ValueError("update missing chat, user, or message")
    created = message.date if message.date is not None else datetime.now(timezone.utc)
    if created.tzinfo is not None:
        created = created.replace(tzinfo=None)
    document = message.document
    filename = document.file_name if document is not None else None
    text = None if document_bytes is not None else (message.text or message.caption)
    return IncomingMessage(
        chat_id=chat.id,
        user_id=user.id,
        message_id=message.message_id,
        created_at=created,
        text=text,
        is_private=chat.type == ChatType.PRIVATE,
        document_bytes=document_bytes,
        document_filename=filename,
        caption=message.caption,
    )


def register_handlers(application: Application, bot_handlers: BotHandlers) -> None:
    """Attach compile, execute, text, document, callback, and error handlers."""

    async def on_text(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        await asyncio.to_thread(bot_handlers.on_message, incoming_from_update(update))

    async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or message.document is None:
            return
        logger.info("Telegram: downloading uploaded document")
        try:
            file = await message.document.get_file()
            payload = bytes(await file.download_as_bytearray())
        except NetworkError as exc:
            logger.warning("Telegram: document download timed out: %s", exc)
            await _notify_retry(update, context, "Upload failed. Send the file again.")
            return
        incoming = incoming_from_update(update, document_bytes=payload)
        await asyncio.to_thread(bot_handlers.on_message, incoming)

    async def on_callback(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.from_user is None or query.message is None:
            return
        await query.answer()
        callback = IncomingCallback(
            chat_id=query.message.chat_id,
            user_id=query.from_user.id,
            data=query.data or "",
        )
        await asyncio.to_thread(bot_handlers.on_callback, callback)

    application.add_handler(CommandHandler("compile", on_text))
    application.add_handler(CommandHandler("execute", on_text))
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_error_handler(_on_telegram_error)


async def _on_telegram_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log unhandled PTB errors. Do not send when Telegram itself timed out."""
    logger.error("Telegram handler failed", exc_info=context.error)
    if isinstance(context.error, NetworkError):
        return
    if not isinstance(update, Update) or update.effective_chat is None:
        return
    await _notify_retry(update, context, "Something went wrong. Try again.")


async def _notify_retry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    """Best-effort DM after a handler failure. Swallows a second timeout."""
    chat = update.effective_chat
    if chat is None:
        return
    try:
        await context.bot.send_message(chat_id=chat.id, text=text)
    except NetworkError as exc:
        logger.warning("Telegram: could not send retry notice: %s", exc)


def build_application(token: str) -> Application:
    """Build a polling app with a longer Bot API connect timeout.

    ``getUpdates`` keeps PTB's default long-poll client. Only ``sendMessage``,
    ``getFile``, and other bot methods use this request object.
    """
    request = HTTPXRequest(
        connect_timeout=_BOT_CONNECT_TIMEOUT,
        read_timeout=_BOT_READ_TIMEOUT,
        write_timeout=_BOT_READ_TIMEOUT,
        pool_timeout=5.0,
        media_write_timeout=60.0,
    )
    return Application.builder().token(token).request(request).build()


def configure_application(
    application: Application,
    gateway: TelegramBotGateway,
) -> None:
    """Bind the gateway to the bot after the polling loop starts."""

    async def post_init(app: Application) -> None:
        gateway.bind(app.bot, asyncio.get_running_loop())

    application.post_init = post_init
