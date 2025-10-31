import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from fichador import get_today_records


async def show_records(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    await update.message.reply_text("🔍 Consultando marcajes de hoy...")
    try:
        records = await asyncio.to_thread(get_today_records)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"❌ No se pudo obtener la información: {exc}")
        return

    if not records:
        await update.message.reply_text("ℹ️ No se encontraron marcajes registrados hoy.")
        return

    lines = [f"• Entrada: {item['entrada']} | Salida: {item['salida']}" for item in records]
    await update.message.reply_text("\n".join(lines))
