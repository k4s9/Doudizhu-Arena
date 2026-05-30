"""Consolidated logging setup for Doudizhu Arena.

Provides:
- setup_logging(): configure all loggers (arena, llm_calls, raw_prompts)
- get_logger(name): convenience function to get a named logger
- log_phase(phase_name): context manager for timing code blocks
"""

from __future__ import annotations

import logging
import logging.handlers
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from arena.config.settings import settings


def setup_logging() -> None:
    """Configure all loggers for the application.

    Sets up:
    - arena logger: console handler + rotating file handler (app.log)
    - llm_calls logger: rotating file handler (llm_calls.log)
    - raw_prompts logger: rotating file handler (prompts.log) at DEBUG level
    """
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    level = _parse_log_level(settings.log_level)

    # Common formatters
    console_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    llm_formatter = logging.Formatter("%(asctime)s %(message)s")

    # ── arena logger: console + rotating file ──────────────────────────
    _configure_logger(
        name="arena",
        level=level,
        handlers=[
            _make_console_handler(console_formatter, level),
            _make_rotating_file_handler(
                log_dir / settings.app_log_file,
                file_formatter,
                max_bytes=settings.log_max_bytes,
                backup_count=settings.log_backup_count,
            ),
        ],
    )

    # ── llm_calls logger: rotating file only ───────────────────────────
    _configure_logger(
        name="llm_calls",
        level=level,
        handlers=[
            _make_rotating_file_handler(
                log_dir / settings.llm_call_log_file,
                llm_formatter,
                max_bytes=settings.log_max_bytes,
                backup_count=settings.log_backup_count,
            ),
        ],
    )

    # ── raw_prompts logger: DEBUG-level rotating file for full prompts ─
    _configure_logger(
        name="raw_prompts",
        level=logging.DEBUG,
        handlers=[
            _make_rotating_file_handler(
                log_dir / settings.prompts_log_file,
                file_formatter,
                max_bytes=settings.log_max_bytes,
                backup_count=settings.log_backup_count,
            ),
        ],
    )

    # Suppress noisy library loggers unless at DEBUG
    if level > logging.DEBUG:
        for lib in ("httpx", "httpcore", "openai", "anthropic", "urllib3"):
            logging.getLogger(lib).setLevel(logging.WARNING)


def _parse_log_level(name: str) -> int:
    """Convert a level name (e.g. 'DEBUG') to a logging level constant."""
    return getattr(logging, name.upper(), logging.INFO)


def _configure_logger(
    name: str,
    level: int,
    handlers: list[logging.Handler],
) -> logging.Logger:
    """Create or reconfigure a logger with the given level and handlers."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    # Prevent propagation to root logger (avoids duplicate output)
    logger.propagate = False
    # Remove existing handlers to allow idempotent calls
    for h in list(logger.handlers):
        logger.removeHandler(h)
    for h in handlers:
        logger.addHandler(h)
    return logger


def _make_console_handler(
    formatter: logging.Formatter,
    level: int,
) -> logging.StreamHandler:
    """Create a console (stderr) handler."""
    h = logging.StreamHandler()
    h.setLevel(level)
    h.setFormatter(formatter)
    return h


def _make_rotating_file_handler(
    path: Path,
    formatter: logging.Formatter,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.handlers.RotatingFileHandler:
    """Create a rotating file handler."""
    h = logging.handlers.RotatingFileHandler(
        str(path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    h.setFormatter(formatter)
    return h


def get_logger(name: str) -> logging.Logger:
    """Convenience function to get a named logger in the 'arena' hierarchy."""
    if not name.startswith("arena"):
        name = f"arena.{name}"
    return logging.getLogger(name)


@contextmanager
def log_phase(phase_name: str) -> Generator[None, None, None]:
    """Context manager that logs entry, exit, and elapsed time for a phase."""
    logger = get_logger("phase")
    logger.info("Phase started: %s", phase_name)
    start = time.monotonic()
    try:
        yield
    except Exception:
        elapsed = time.monotonic() - start
        logger.exception(
            "Phase FAILED: %s (%.2fs)", phase_name, elapsed
        )
        raise
    else:
        elapsed = time.monotonic() - start
        logger.info("Phase completed: %s (%.2fs)", phase_name, elapsed)
