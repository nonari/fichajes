from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from fichador import fichar
from logging_config import get_logger

MADRID_TZ: Final[ZoneInfo] = ZoneInfo("Europe/Madrid")

logger = get_logger(__name__)


def ahora_madrid() -> datetime:
    return datetime.now(MADRID_TZ)


async def ejecutar_fichaje_async(
    accion: str, context: ContextTypes.DEFAULT_TYPE
):
    resultado = await asyncio.to_thread(fichar, accion)
    logger.info("Resultado del fichaje de %s: %s", accion, resultado.message)
    return resultado
