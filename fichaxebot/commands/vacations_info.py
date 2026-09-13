from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from telegram import Update, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes

from fichaxebot.config import get_config
from fichaxebot.logging_config import get_logger
from fichaxebot.scrap_functions.vacations_info import VacationsInfoError

logger = get_logger(__name__)


def _build_vacations_info_url(base_url: str, payload: dict) -> str:
    encoded = quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), safe="")
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}data={encoded}"


async def show_vacations_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    status_message = await update.message.reply_text("🔄 Obteniendo resumen de vacaciones...")
    session = context.application.web_session

    try:
        headers, row_names, rows = await asyncio.to_thread(session.retrieve_vacations_info)
    except VacationsInfoError as exc:
        logger.warning("Vacation info fetch failed: %s", exc)
        await status_message.edit_text(f"❌ No se pudo obtener la información: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error while fetching vacation info")
        await status_message.edit_text(
            "❌ Error inesperado al obtener la información de vacaciones. Inténtalo más tarde.",
        )
        return

    config = get_config()
    webapp_url = getattr(config, "vacations_info_webapp_url", "") or ""
    if not webapp_url:
        await status_message.edit_text(
            "⚙️ Configura 'vacations_info_webapp_url' en config.json para abrir el resumen.",
        )
        return

    payload = {
        "columns": headers,
        "rowNames": row_names,
        "rows": rows,
    }

    url = _build_vacations_info_url(webapp_url, payload)

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton(
            text="Abrir info de vacaciones",
            web_app=WebAppInfo(url=url)
        )]],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    await update.message.reply_text(
        "ℹ️ Información de vacaciones lista. 👇 Pulsa el botón para verla",
        reply_markup=keyboard,
    )
    await status_message.delete()

