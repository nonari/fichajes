"""Load explicitly enabled local plugins; a broken plugin is skipped and reported, never fatal."""

import importlib
import inspect
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Callable, Optional

from telegram.ext import Application, CommandHandler

from fichaxebot.logging_config import get_logger
from fichaxebot.webapp_controller.router import WEBAPP_CONTROLLERS

logger = get_logger(__name__)

# bot_data key holding plugin command descriptions, shown by /ayuda and the Telegram command menu.
COMMAND_DESCRIPTIONS_KEY = "command_descriptions"


@dataclass(frozen=True)
class PluginFailure:
    name: str
    message: str
    error: BaseException


@dataclass
class _LoadedPlugin:
    commands: set = field(default_factory=set)
    handlers: list = field(default_factory=list)
    descriptions: dict = field(default_factory=dict)
    controllers: dict = field(default_factory=dict)
    setup: Optional[Callable] = None


def _load(name: str, used_commands: set, used_types: set) -> _LoadedPlugin:
    """Import and validate one plugin without registering anything."""
    plugin = importlib.import_module(f"plugins.{name}")
    if not hasattr(plugin, "__path__"):
        raise ValueError("expected a plugin package containing __init__.py")
    commands = getattr(plugin, "COMMANDS", None)
    if not isinstance(commands, Mapping):
        raise ValueError("COMMANDS must map command names to async callbacks")

    loaded = _LoadedPlugin()
    for command, callback in commands.items():
        if not isinstance(command, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,32}", command):
            raise ValueError(f"invalid command name: {command!r}")
        command = command.lower()
        if command in used_commands or command in loaded.commands:
            raise ValueError(f"command /{command} is already registered")
        if not inspect.iscoroutinefunction(callback):
            raise ValueError(f"callback for /{command} must be an async function")
        loaded.handlers.append(CommandHandler(command, callback))
        loaded.commands.add(command)

    descriptions = getattr(plugin, "COMMAND_DESCRIPTIONS", {})
    if not isinstance(descriptions, Mapping):
        raise ValueError("COMMAND_DESCRIPTIONS must map command names to descriptions")
    for command, description in descriptions.items():
        if not isinstance(command, str) or command.lower() not in loaded.commands:
            raise ValueError(f"description for unknown command: {command!r}")
        if not isinstance(description, str) or not description.strip() or len(description) > 256:
            raise ValueError(f"description for /{command.lower()} must be text of 1-256 characters")
        loaded.descriptions[command.lower()] = description.strip()

    controllers = getattr(plugin, "WEBAPP_CONTROLLERS", {})
    if not isinstance(controllers, Mapping):
        raise ValueError("WEBAPP_CONTROLLERS must map web app data types to async handlers")
    for data_type, handler in controllers.items():
        if not isinstance(data_type, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", data_type):
            raise ValueError(f"invalid web app data type: {data_type!r}")
        if data_type in used_types:
            raise ValueError(f"web app data type '{data_type}' is already registered")
        if not inspect.iscoroutinefunction(handler):
            raise ValueError(f"handler for web app data type '{data_type}' must be an async function")
        loaded.controllers[data_type] = handler

    setup = getattr(plugin, "setup", None)
    if setup is not None and not callable(setup):
        raise ValueError("setup must be a function taking the application")
    loaded.setup = setup
    return loaded


def register_plugins(application: Application, names: Sequence[str]) -> list[PluginFailure]:
    """Register each configured plugin on its own; return the plugins that could not be loaded.

    Names are direct package names validated by load_config. Plugins export COMMANDS
    (command name without '/' -> async (update, context) callback) and may export
    COMMAND_DESCRIPTIONS (command -> one-line description for /ayuda), WEBAPP_CONTROLLERS
    (web app data type -> async (update, context, data) handler) and setup(application),
    called after the plugin's commands are registered. A plugin that fails any check or
    whose setup raises is skipped: its commands, descriptions, web app handlers and the
    handlers its setup added are removed. Scheduler work registered by a failing setup
    cannot be undone, so setup must validate its configuration before registering anything.
    """
    used_commands = {
        command
        for handlers in application.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }
    descriptions = application.bot_data.setdefault(COMMAND_DESCRIPTIONS_KEY, {})
    failures = []
    for name in names:
        try:
            loaded = _load(name, used_commands, set(WEBAPP_CONTROLLERS))
        except Exception as exc:  # noqa: BLE001 - any import or validation problem skips the plugin
            failures.append(PluginFailure(name, str(exc) or type(exc).__name__, exc))
            logger.error("Plugin '%s' skipped: %s", name, exc, exc_info=exc)
            continue

        before = {group: list(handlers) for group, handlers in application.handlers.items()}
        for handler in loaded.handlers:
            application.add_handler(handler)
        WEBAPP_CONTROLLERS.update(loaded.controllers)
        descriptions.update(loaded.descriptions)
        if loaded.setup is not None:
            try:
                loaded.setup(application)
            except Exception as exc:  # noqa: BLE001 - a broken plugin must not stop the bot
                for group, handlers in list(application.handlers.items()):
                    for handler in [h for h in handlers if h not in before.get(group, [])]:
                        application.remove_handler(handler, group)
                for data_type in loaded.controllers:
                    WEBAPP_CONTROLLERS.pop(data_type, None)
                for command in loaded.descriptions:
                    descriptions.pop(command, None)
                failures.append(PluginFailure(name, f"setup failed: {exc}", exc))
                logger.error("Plugin '%s' skipped: setup failed: %s", name, exc, exc_info=exc)
                continue
        used_commands |= loaded.commands
    return failures


def plugin_warning(failures: Sequence[PluginFailure]) -> Optional[str]:
    """Telegram text telling the user which plugins did not load, or None."""
    if not failures:
        return None
    lines = "\n".join(f"• {failure.name}: {failure.message}" for failure in failures)
    return ("⚠️ Estos plugins no se cargaron; el resto del bot funciona. Corrige config.json y reinicia el bot.\n"
            + lines)
