import asyncio
import signal
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from fichaxebot.commands import (
    show_records,
    show_calendar,
    show_vacations,
    show_vacations_info,
    start,
)
from fichaxebot.commands.absences import show_absences
from fichaxebot.commands.help import publish_command_menu, show_help
from fichaxebot.webapp_controller.absences import register_absences
from fichaxebot.config import get_config
from fichaxebot.access_control import restrict_to_chat
from fichaxebot.plugins import plugin_warning, register_plugins
from fichaxebot.update_log import register_update_logging
from fichaxebot.usc_api import UscWebSession
from fichaxebot.logging_config import get_logger
from fichaxebot.scheduler import TaskScheduler
from fichaxebot.webapp_controller.router import dispatch_webapp_reply
from fichaxebot.webapp_controller.vacation_confirmation import (
    register_vacation_confirmation, stop_vacation_confirmation,
)

logger = get_logger(__name__)

config = get_config()
TOKEN = config.telegram_token
CHAT_ID = config.telegram_chat_id


async def _run_bot() -> None:
    appconfig = get_config()
    scheduler = TaskScheduler(appconfig.telegram_chat_id)
    app = ApplicationBuilder().token(TOKEN).build()
    register_update_logging(app)
    restrict_to_chat(app, appconfig.telegram_chat_id)
    register_vacation_confirmation(app)
    register_absences(app)
    app.scheduler = scheduler

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", show_help))
    app.add_handler(CommandHandler("marcajes", show_records))
    app.add_handler(CommandHandler("calendario", show_calendar))
    app.add_handler(CommandHandler("vacaciones", show_vacations))
    app.add_handler(CommandHandler("ausencias", show_absences))
    app.add_handler(CommandHandler("vacaciones_info", show_vacations_info))
    plugin_failures = register_plugins(app, appconfig.plugins)

    web_session = UscWebSession()
    app.web_session = web_session

    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, dispatch_webapp_reply))

    stop_event = asyncio.Event()

    def handle_stop(*_):
        logger.info("🛑 Stopping bot...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, handle_stop)

    await app.initialize()
    await app.start()

    warning = plugin_warning(plugin_failures)
    if warning:
        await app.bot.send_message(chat_id=CHAT_ID, text=warning)

    try:
        await publish_command_menu(app)
    except Exception:  # noqa: BLE001 - the menu is a convenience; the bot works without it
        logger.exception("Could not publish the Telegram command menu")

    report = scheduler.start(app)
    startup_message = scheduler.startup_message(report)
    if startup_message:
        await app.bot.send_message(chat_id=CHAT_ID, text=startup_message)
    logger.info("🤖 Bot started.")

    try:
        records = await asyncio.to_thread(web_session.get_today_records)
    except Exception as exc:  # noqa: BLE001
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=f"❌ No se pudieron consultar los marcajes actuales: {exc}",
        )
    else:
        if records:
            resumen = "\n".join(
                f"• Entrada: {item['entrada']} | Salida: {item['salida']}" for item in records
            )
        else:
            resumen = "ℹ️ No hay marcajes registrados hoy."
        await app.bot.send_message(chat_id=CHAT_ID, text=resumen)

    await app.updater.start_polling()
    logger.info("🤖 Bot running. Press Ctrl+C to stop.")

    await stop_event.wait()

    await app.updater.stop()
    await stop_vacation_confirmation(app)
    await app.stop()
    await app.shutdown()

    web_session.close()

def main() -> None:
    asyncio.run(_run_bot())


if __name__ == "__main__":
    main()
