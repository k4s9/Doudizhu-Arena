"""Global configuration with defaults for timeout values, retry limits, etc.

All values can be overridden via environment variables (with DOUDIZHU_ prefix)
or a .env file placed at the project root or src/backend/.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Load .env before reading any settings
_ENV_FILES = [
    Path(__file__).resolve().parent.parent.parent / ".env",       # src/backend/.env
    Path(__file__).resolve().parent.parent.parent.parent.parent / ".env",  # project root
]
for _ef in _ENV_FILES:
    if _ef.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(_ef)
        except ImportError:
            pass  # python-dotenv not installed, skip silently

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
CONFIG_DIR = Path(__file__).resolve().parent


def _int_env(key: str, default: int) -> int:
    """Read int from env var, return default if unset or invalid."""
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _float_env(key: str, default: float) -> float:
    """Read float from env var, return default if unset or invalid."""
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _bool_env(key: str, default: bool) -> bool:
    """Read bool from env var ('true'/'1' = True)."""
    val = os.getenv(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


@dataclass
class Settings:
    # ── timeout defaults ────────────────────────────────────────────────
    #  All settable via env vars: DOUDIZHU_BIDDING_TIMEOUT, etc.
    bidding_timeout_seconds: float = field(default_factory=lambda: _float_env(
        "DOUDIZHU_BIDDING_TIMEOUT", 60.0))
    individual_play_timeout_seconds: float = field(default_factory=lambda: _float_env(
        "DOUDIZHU_PLAY_TIMEOUT", 360.0))
    team_pool_timeout_seconds: float = field(default_factory=lambda: _float_env(
        "DOUDIZHU_TEAM_POOL_TIMEOUT", 3600.0))
    exhausted_individual_timeout_seconds: float = field(default_factory=lambda: _float_env(
        "DOUDIZHU_EXHAUSTED_TIMEOUT", 60.0))
    max_consecutive_llm_failures: int = field(default_factory=lambda: _int_env(
        "DOUDIZHU_MAX_LLM_FAILURES", 3))
    llm_retry_limit: int = field(default_factory=lambda: _int_env(
        "DOUDIZHU_LLM_RETRY_LIMIT", 3))

    # ── match defaults ──────────────────────────────────────────────────
    total_hands: int = field(default_factory=lambda: _int_env(
        "DOUDIZHU_TOTAL_HANDS", 20))
    ko_enabled: bool = field(default_factory=lambda: _bool_env(
        "DOUDIZHU_KO_ENABLED", True))
    diff_cap: int = field(default_factory=lambda: _int_env(
        "DOUDIZHU_DIFF_CAP", 12))

    # ── agent ───────────────────────────────────────────────────────────
    agents_yaml_path: str = field(default_factory=lambda: os.getenv(
        "AGENTS_YAML_PATH",
        str(CONFIG_DIR / "agents.yaml"),
    ))

    # ── logging ─────────────────────────────────────────────────────────
    log_dir: str = field(default_factory=lambda: os.getenv(
        "LOG_DIR",
        str(PROJECT_ROOT / "src" / "backend" / "logs"),
    ))
    llm_call_log_file: str = "llm_calls.log"
    app_log_file: str = "app.log"
    prompts_log_file: str = "prompts.log"
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_max_bytes: int = 10 * 1024 * 1024  # 10 MB
    log_backup_count: int = 5

    # ── db ──────────────────────────────────────────────────────────────
    database_url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL",
        f"sqlite:///{PROJECT_ROOT}/data/arena.db",
    ))

    # ── server ──────────────────────────────────────────────────────────
    host: str = field(default_factory=lambda: os.getenv("DOUDIZHU_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int_env("DOUDIZHU_PORT", 8000))
    cors_origins: list[str] = field(default_factory=lambda: os.getenv(
        "DOUDIZHU_CORS_ORIGINS", "http://localhost:5173",
    ).split(","))


settings = Settings()
