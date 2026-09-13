from __future__ import annotations

import asyncio

from telegram import Update, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes

from fichaxebot.commands.calendar import _build_calendar_url
from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.view_calendar import CalendarFetchError

logger = get_logger(__name__)


async def show_vacations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    status_message = await update.message.reply_text("🔄 Obteniendo calendario de vacaciones...")
    session = context.application.web_session

    try:
        entries = await asyncio.to_thread(session.fetch_calendar_summary)
    except CalendarFetchError as exc:
        logger.warning("Calendar fetch failed: %s", exc)
        await status_message.edit_text(f"❌ No se pudo obtener el calendario: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error while fetching the vacations calendar")
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
    webapp_url = getattr(config, "vacations_webapp_url", "") or getattr(config, "calendar_webapp_url", "") or ""
    if not webapp_url:
        await status_message.edit_text(
            "⚙️ Configura 'vacations_webapp_url' en config.json para abrir el calendario de vacaciones.",
        )
        return

    url = _build_calendar_url(webapp_url, entries, mode="vacations")

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(
            text="Abrir calendario de vacaciones",
            web_app=WebAppInfo(url=url)
        )]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    await update.message.reply_text(
        "📆 Calendario listo. 👇 Pulsa el botón para abrirlo",
        reply_markup=keyboard,
    )
