"""Config + player loader — reads agents.yaml and syncs DB records.

Called at backend startup and via the /configs/reload API endpoint.
Resolves ${ENV_VAR} placeholders in api_key fields.

Two-phase load:
  1. Sync player_configs from agent_definitions in YAML.
  2. Create default players from default_players section (idempotent).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from ..db.repository import DatabaseRepository

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _resolve_env(value: str) -> str:
    """Replace ${VAR} with environment variable values."""
    def replacer(m: re.Match) -> str:
        var_name = m.group(1)
        return os.environ.get(var_name, m.group(0))
    return _ENV_VAR_RE.sub(replacer, value)


def load_agents_from_yaml(
    repo: DatabaseRepository,
    yaml_path: str | None = None,
) -> list[str]:
    """Load configs + players from agents.yaml and sync DB records.

    Phase 1 — configs: Reads agent_definitions, syncs to player_configs table.
    Phase 2 — players: Reads default_players, creates players if they don't exist.

    Args:
        repo: An initialized DatabaseRepository.
        yaml_path: Path to agents.yaml. If None, uses default from config.

    Returns:
        List of player IDs created/loaded (from default_players).
    """
    if yaml_path is None:
        from ..config.settings import settings
        yaml_path = settings.agents_yaml_path

    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"Agent config not found: {yaml_path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw:
        return []

    # ── Phase 1: sync player_configs ──────────────────────────────────────────
    if "agent_definitions" in raw:
        for entry in raw["agent_definitions"]:
            name = entry["name"]
            provider = entry["provider"]
            model = entry["model"]
            api_key = _resolve_env(entry.get("api_key", ""))
            base_url = entry.get("base_url")
            # Accept both system_prompt (new) and system_prompt_override (old)
            system_prompt = entry.get("system_prompt") or entry.get("system_prompt_override")

            existing = repo.get_player_config_by_name(name)
            if existing:
                changed = (
                    existing["provider"] != provider
                    or existing["model"] != model
                    or existing.get("api_key", "") != api_key
                    or existing.get("base_url") != base_url
                    or existing.get("system_prompt") != system_prompt
                )
                if changed:
                    repo.update_player_config(
                        existing["id"],
                        name=name,
                        provider=provider,
                        model=model,
                        api_key=api_key,
                        base_url=base_url,
                        system_prompt=system_prompt,
                    )
            else:
                repo.create_player_config(
                    name=name,
                    provider=provider,
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    system_prompt=system_prompt,
                )

    # ── Phase 2: create default players ───────────────────────────────────────
    player_ids: list[str] = []
    if "default_players" in raw:
        for dp in raw["default_players"]:
            config_name = dp["config"]
            display_name = dp["display_name"]

            config = repo.get_player_config_by_name(config_name)
            if config is None:
                continue  # skip if config doesn't exist

            # Check if a player with this display_name already exists for this config
            existing_players = repo.list_players(config_id=config["id"])
            found = any(p["display_name"] == display_name for p in existing_players)
            if found:
                continue

            player_id = repo.create_player(
                config_id=config["id"],
                display_name=display_name,
            )
            player_ids.append(player_id)

    return player_ids
