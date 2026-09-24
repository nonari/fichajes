"""Load explicitly enabled local plugins as ordinary Telegram commands."""

import importlib
import inspect
import re
from collections.abc import Mapping, Sequence

from telegram.ext import Application, CommandHandler


def register_plugins(application: Application, names: Sequence[str]) -> None:
    """Register COMMANDS from configured plugins, or fail before adding any handlers.

    Names are direct package names validated by load_config. Plugins export a
    mapping of command names (without '/') to async (update, context) callbacks.
    """
    used_commands = {
        command
        for handlers in application.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }
    pending = []
    for name in names:
        try:
            plugin = importlib.import_module(f"plugins.{name}")
            if not hasattr(plugin, "__path__"):
                raise ValueError("expected a plugin package containing __init__.py")
            commands = getattr(plugin, "COMMANDS", None)
            if not isinstance(commands, Mapping):
                raise ValueError("COMMANDS must map command names to async callbacks")

            for command, callback in commands.items():
                if not isinstance(command, str) or not re.fullmatch(
                    r"[a-zA-Z0-9_]{1,32}", command
                ):
                    raise ValueError(f"invalid command name: {command!r}")
                command = command.lower()
                if command in used_commands:
                    raise ValueError(f"command /{command} is already registered")
                if not inspect.iscoroutinefunction(callback):
                    raise ValueError(f"callback for /{command} must be an async function")
                pending.append(CommandHandler(command, callback))
                used_commands.add(command)
        except Exception as exc:
            raise ValueError(f"Plugin '{name}': {exc}") from exc

    for handler in pending:
        application.add_handler(handler)
