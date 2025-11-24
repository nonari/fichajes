from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from telegram import Update, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes

from fichaxebot.scrap_functions.view_calendar import CalendarFetchError
from fichaxebot.usc_api import fetch_calendar_summary
from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)


async def show_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    status_message = await update.message.reply_text("🔄 Obteniendo calendario anual...")

    try:
        entries = await asyncio.to_thread(fetch_calendar_summary)
    except CalendarFetchError as exc:
        logger.warning("Calendar fetch failed: %s", exc)
        await status_message.edit_text(f"❌ No se pudo obtener el calendario: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error while fetching the calendar")
        await status_message.edit_text(
            "❌ Error inesperado al obtener el calendario. Inténtalo de nuevo más tarde.",
        )
        return

    if not entries:
        await status_message.edit_text(
            "ℹ️ No hay vacaciones ni días no laborables registrados en el calendario.",
        )
        return

    config = get_config()
    webapp_url = getattr(config, "calendar_webapp_url", "") or ""
    if not webapp_url:
        await status_message.edit_text(
            "⚙️ Configura 'calendar_webapp_url' en config.json para abrir el calendario.",
        )
        return

    payload = quote(json.dumps(entries, ensure_ascii=False, separators=(",", ":")), safe="")
    url = f"{webapp_url}?data={payload}"

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(
            text="Abrir calendario",
            web_app=WebAppInfo(url=url)
        )]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    # send NEW message WITH reply keyboard
    await update.message.reply_text(
        "📆 Calendario listo. 👇 Pulsa el botón para abrirlo",
        reply_markup=keyboard,
    )
