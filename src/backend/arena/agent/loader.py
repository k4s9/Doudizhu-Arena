"""Agent config loader — reads agents.yaml and creates DB records.

Called at backend startup. Resolves ${ENV_VAR} placeholders in api_key fields.
Creates DB agent records for any YAML entries that don't already exist in DB.
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
    """Load agent definitions from agents.yaml and create DB records.

    Reads the YAML file, resolves env var references in api_key fields,
    and creates DB agent records for any that don't already exist (matched by name).

    Args:
        repo: An initialized DatabaseRepository.
        yaml_path: Path to agents.yaml. If None, uses default from config.

    Returns:
        List of agent IDs loaded/created.
    """
    if yaml_path is None:
        from ...config.settings import settings
        yaml_path = settings.agents_yaml_path

    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"Agent config not found: {yaml_path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw or "agent_definitions" not in raw:
        raise ValueError(f"Invalid agents.yaml: missing 'agent_definitions' key in {yaml_path}")

    agent_ids: list[str] = []
    for entry in raw["agent_definitions"]:
        name = entry["name"]
        provider = entry["provider"]
        model = entry["model"]
        api_key = _resolve_env(entry.get("api_key", ""))
        system_prompt_override = entry.get("system_prompt_override")

        existing = repo.get_agent_by_name(name)
        if existing:
            agent_ids.append(existing["id"])
        else:
            agent_id = repo.create_agent(
                name=name,
                provider=provider,
                model=model,
                api_key=api_key,
                system_prompt_override=system_prompt_override,
            )
            agent_ids.append(agent_id)

    return agent_ids
