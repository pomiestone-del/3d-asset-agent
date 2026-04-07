"""Centralized logging setup with Rich console handler and rotating file handler."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

_CONFIGURED = False

# Default log file location — override via LOG_FILE env var
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
_DEFAULT_LOG_FILE = _DEFAULT_LOG_DIR / "asset_agent.log"


def setup_logging(level: str = "INFO") -> None:
    """Configure the root ``asset_agent`` logger.

    Attaches two handlers:
    - **Console** (Rich) — colorized output to stdout.
    - **File** (RotatingFileHandler) — plain-text log written to
      ``logs/asset_agent.log`` (or the path in the ``LOG_FILE`` env var).
      Rotates at 10 MB, keeps 5 backups.

    Args:
        level: Logging level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    global _CONFIGURED  # noqa: PLW0603
    if _CONFIGURED:
        return

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger = logging.getLogger("asset_agent")
    logger.setLevel(numeric_level)

    # --- Console handler (Rich) ---
    console = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
        markup=True,
    )
    console.setLevel(numeric_level)
    console.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    logger.addHandler(console)

    # --- File handler (rotating) ---
    log_file = Path(os.environ.get("LOG_FILE", str(_DEFAULT_LOG_FILE)))
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(file_handler)
        logger.debug("Log file: %s", log_file)
    except OSError as exc:
        logger.warning("Could not open log file '%s': %s", log_file, exc)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``asset_agent`` namespace.

    Args:
        name: Dot-separated logger name (e.g. ``core.texture_matcher``).
    """
    return logging.getLogger(f"asset_agent.{name}")
