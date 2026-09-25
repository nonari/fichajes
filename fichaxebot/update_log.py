"""Log every incoming update in one line (command, button, web app action) for diagnosis."""
import json
from typing import Optional

from telegram import Update
from telegram.ext import TypeHandler

from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)


def describe_update(update) -> Optional[str]:
    """Short description; free text is not logged verbatim because it may be personal."""
    query = update.callback_query
    if query is not None:
        return f"button {str(query.data or '').split(':', 1)[0]}"
    message = update.effective_message
    if message is None:
        return None
    if message.web_app_data is not None:
        try:
            kind = json.loads(message.web_app_data.data).get("type")
        except (TypeError, ValueError, AttributeError):
            kind = None
        return f"web app data {kind or '(unreadable)'}"
    if message.text:
        return f"command {message.text[:100]}" if message.text.startswith("/") else "text message"
    if message.document:
        return "document"
    return "message"


async def log_update(update, context) -> None:
    description = describe_update(update)
    if description:
        chat = update.effective_chat
        logger.info("Received %s (chat %s)", description, chat.id if chat else "?")


def register_update_logging(application) -> None:
    # Before the chat restriction (group -2), so rejected chats are logged too.
    application.add_handler(TypeHandler(Update, log_update), group=-3)
