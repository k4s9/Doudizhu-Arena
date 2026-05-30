"""Global configuration with defaults for timeout values, retry limits, etc."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_DIR = Path(__file__).resolve().parent


@dataclass
class Settings:
    # ── timeout defaults ────────────────────────────────────────────────
    bidding_timeout_seconds: float = 60.0
    individual_play_timeout_seconds: float = 360.0
    team_pool_timeout_seconds: float = 3600.0  # 60 min
    exhausted_individual_timeout_seconds: float = 60.0
    max_consecutive_llm_failures: int = 3
    llm_retry_limit: int = 3

    # ── match defaults ──────────────────────────────────────────────────
    total_hands: int = 20
    ko_enabled: bool = True
    diff_cap: int = 12

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
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173"])


settings = Settings()
