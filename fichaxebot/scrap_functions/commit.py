from __future__ import annotations

from fichaxebot.logging_config import get_logger

logger = get_logger(__name__)


class ReadOnlyStop(PermissionError):
    """Read-only mode reached the final USC write; nothing was sent."""

    def __init__(self, message: str = "Modo de solo lectura: se llegó al paso final, pero no se envió nada a USC."):
        super().__init__(message)


def commit_click(session, element) -> None:
    """Click the element that makes a USC write final, unless read-only mode is on.

    Every flow must route its final submit through here so read-only mode runs
    the whole procedure except the write itself.
    """
    if session.config.read_only:
        logger.info("Read-only mode: final USC action skipped")
        raise ReadOnlyStop()
    element.click()
