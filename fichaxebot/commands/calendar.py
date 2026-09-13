from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from telegram import Update, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes

from fichaxebot.scrap_functions.view_calendar import CalendarFetchError
from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)


def _build_calendar_url(base_url: str, entries: list[str], mode: str | None = None) -> str:
    payload = quote(json.dumps(entries, ensure_ascii=False, separators=(",", ":")), safe="")
    separator = "&" if "?" in base_url else "?"
    url = f"{base_url}{separator}data={payload}"
    if mode:
        url = f"{url}&mode={quote(mode, safe='')}"
    return url


async def show_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    status_message = await update.message.reply_text("🔄 Obteniendo calendario anual...")
    session = context.application.web_session

    try:
        entries = await asyncio.to_thread(session.fetch_calendar_summary)
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

    url = _build_calendar_url(webapp_url, entries, mode="readonly")

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(
            text="Abrir calendario",
            web_app=WebAppInfo(url=url)
        )]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    await update.message.reply_text(
        "📆 Vista del calendario lista. 👇 Pulsa el botón para abrirla",
        reply_markup=keyboard,
    )
