"""Reject updates outside the configured Telegram chat before dispatch."""

from telegram import Update
from telegram.ext import ApplicationHandlerStop, TypeHandler


def restrict_to_chat(application, chat_id):
    allowed_chat_id = int(chat_id)

    async def check_chat(update, context):
        chat = update.effective_chat
        if chat is None or chat.id != allowed_chat_id:
            raise ApplicationHandlerStop

    application.add_handler(TypeHandler(Update, check_chat, block=True), group=-2)
