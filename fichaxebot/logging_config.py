import logging
import os
from pathlib import Path
from typing import Optional

DEFAULT_LOG_DIR = Path("/tmp/fichaxe_app")
LOG_DIR_ENV_VAR = "FICHAXE_LOG_DIR"
LOG_FILE_NAME = "app.log"


def get_log_directory() -> Path:
    """Return the directory where log files should be stored."""

    custom_dir = os.environ.get(LOG_DIR_ENV_VAR)
    if custom_dir:
        return Path(custom_dir).expanduser()
    return DEFAULT_LOG_DIR


def get_log_file_path() -> Path:
    """Return the full path to the application log file."""

    return get_log_directory() / LOG_FILE_NAME


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a configured logger that writes to the application log file."""
    log_file = get_log_file_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)

    if not getattr(logger, "_fichaxe_configured", False):
        handler = logging.FileHandler(log_file)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger._fichaxe_configured = True  # type: ignore[attr-defined]

    return logger


# Backwards compatibility constants for external imports
LOG_DIR = str(get_log_directory())
LOG_FILE = str(get_log_file_path())
