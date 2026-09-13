from __future__ import annotations

import asyncio
import json
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

from telegram import Update, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes

from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)
VACATION_SELECTION_KEY = "vacation_selection"


def _build_vacations_url(base_url: str, payload: dict) -> str:
    # A fragment keeps balances out of the static host's query/access logs.
    parts = urlsplit(base_url)
    data = quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), safe="")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, f"data={data}"))


async def show_vacations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = get_config()
    if not update.message or str(update.effective_chat.id) != str(config.telegram_chat_id):
        return
    webapp_url = config.vacations_webapp_url
    if not webapp_url:
        await update.message.reply_text("Configura 'vacations_webapp_url' para abrir la selección.")
        return

    status_message = await update.message.reply_text("🔄 Consultando años, saldos y calendario en USC...")
    try:
        payload = await asyncio.to_thread(context.application.web_session.fetch_vacation_selection_data)
    except Exception:
        logger.exception("Could not load vacation selection data")
        await status_message.edit_text("❌ No se pudieron consultar los saldos y el calendario. Inténtalo de nuevo.")
        return
    if not payload["years"]:
        await status_message.edit_text("ℹ️ USC no ofrece años de saldo disponibles para esta solicitud.")
        return
    payload["requestId"] = uuid4().hex
    context.user_data[VACATION_SELECTION_KEY] = payload
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Seleccionar vacaciones", web_app=WebAppInfo(url=_build_vacations_url(webapp_url, payload)))]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "📆 Elige el año del saldo, el tipo de vacaciones y después las fechas.", reply_markup=keyboard,
    )
    await status_message.delete()
