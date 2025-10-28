import logging
import os
from typing import Optional

LOG_DIR = "/tmp/fichaxe_app"
LOG_FILE = os.path.join(LOG_DIR, "app.log")


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a configured logger that writes to the application log file."""
    os.makedirs(LOG_DIR, exist_ok=True)

    logger = logging.getLogger(name)

    if not getattr(logger, "_fichaxe_configured", False):
        handler = logging.FileHandler(LOG_FILE)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger._fichaxe_configured = True  # type: ignore[attr-defined]

    return logger
