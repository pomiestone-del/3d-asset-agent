"""Centralized logging setup with Rich console handler."""

from __future__ import annotations

import logging

from rich.logging import RichHandler

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    """Configure the root ``asset_agent`` logger.

    Args:
        level: Logging level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED:
        return

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger("asset_agent")
    logger.setLevel(numeric_level)

    handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
        markup=True,
    )
    handler.setLevel(numeric_level)
    fmt = logging.Formatter("%(message)s", datefmt="[%X]")
    handler.setFormatter(fmt)
    logger.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``asset_agent`` namespace.

    Args:
        name: Dot-separated logger name (e.g. ``core.texture_matcher``).
    """
    return logging.getLogger(f"asset_agent.{name}")
