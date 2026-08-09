"""Versioned experiment specifications and dry-run safety validation.

This module deliberately does not start matches or make provider calls.  It makes
the inputs to a later evaluation run explicit and hashes the resulting manifest.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PreflightError(ValueError):
    """Raised when an experiment is not safe or reproducible to run."""


class ProviderParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int = Field(gt=0, le=32768)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    seed: int | None = None


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_name: str
    provider: str
    model: str
    base_url: str | None = None
    parameters: ProviderParameters


class VariantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    retry_limit: int = Field(ge=0, le=10)
    rule_feedback: bool
    enable_reflection: bool
    memory_mode: str = Field(pattern=r"^(disabled|train|read_only)$")

    @model_validator(mode="after")
    def validate_controls(self) -> "VariantSpec":
        if not self.rule_feedback and self.retry_limit:
            raise ValueError("rule_feedback=false requires retry_limit=0")
        if self.memory_mode != "disabled" and not self.enable_reflection:
            raise ValueError("memory_mode requires enable_reflection=true")
        return self


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    seed_set_path: str
    training_seed_set_path: str | None = None
    memory_training_variant_id: str | None = None
    training_total_hands: int = Field(default=5, gt=0, le=200)
    memory_artifact_path: str | None = None
    total_hands: int = Field(gt=0, le=200)
    ko_enabled: bool = False
    seat_rotations: int = Field(default=1, ge=1, le=8)
    timeout_config: dict[str, float | int]
    pricing_version: str
    budget_limit_usd: float = Field(gt=0)
    models: list[ModelSpec] = Field(min_length=1)
    variants: list[VariantSpec] = Field(min_length=1)

    @field_validator("models")
    @classmethod
    def unique_models(cls, models: list[ModelSpec]) -> list[ModelSpec]:
        names = [model.config_name for model in models]
        if len(names) != len(set(names)):
            raise ValueError("model config_name values must be unique")
        return models

    @field_validator("variants")
    @classmethod
    def unique_variants(cls, variants: list[VariantSpec]) -> list[VariantSpec]:
        ids = [variant.variant_id for variant in variants]
        if len(ids) != len(set(ids)):
            raise ValueError("variant_id values must be unique")
        return variants

    @model_validator(mode="after")
    def validate_memory_training(self) -> "ExperimentSpec":
        if self.training_seed_set_path and not self.memory_training_variant_id:
            raise ValueError("training_seed_set_path requires memory_training_variant_id")
        if self.memory_training_variant_id and self.memory_training_variant_id not in {
            variant.variant_id for variant in self.variants
        }:
            raise ValueError("memory_training_variant_id must reference a configured variant")
        return self


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """Links runtime observations back to the immutable evaluation run."""

    experiment_id: str = ""
    run_id: str = ""
    variant_id: str = ""


@dataclass(frozen=True, slots=True)
class ModelSnapshot:
    config_name: str
    provider: str
    model: str
    base_url: str | None
    parameters: dict[str, Any]
    prompt_sha256: str
    revision: str
    sdk_version: str


@dataclass(frozen=True, slots=True)
class RunManifest:
    experiment_id: str
    seed_set_id: str
    seed_set_sha256: str
    seeds: tuple[str, ...]
    training_seed_set_sha256: str | None
    memory_artifact_sha256: str | None
    models: tuple[ModelSnapshot, ...]
    variants: tuple[dict[str, Any], ...]
    source_revision: str
    source_dirty: bool
    dependency_sha256: str
    spec_sha256: str
    manifest_sha256: str
    sampling_reproducibility: str


def load_experiment_spec(path: str | Path) -> ExperimentSpec:
    with Path(path).open(encoding="utf-8") as handle:
        return ExperimentSpec.model_validate(yaml.safe_load(handle))


def build_run_manifest(spec: ExperimentSpec, root: str | Path) -> RunManifest:
    """Validate fixed inputs and construct a deterministic, serializable manifest."""
    root_path = Path(root)
    seed_set_id, seeds, seed_hash = _load_seed_set(root_path / spec.seed_set_path)
    training_hash = None
    if spec.training_seed_set_path:
        _, training_seeds, training_hash = _load_seed_set(root_path / spec.training_seed_set_path)
        overlap = set(seeds) & set(training_seeds)
        if overlap:
            raise PreflightError("training and evaluation seed sets overlap")
    memory_hash = None
    if spec.memory_artifact_path:
        _, memory_hash = load_memory_artifact(root_path / spec.memory_artifact_path)

    prompt_hash = _prompt_hash(root_path)
    revision = _git_revision(root_path)
    source_dirty = _git_dirty(root_path)
    dependency_hash = _dependency_hash(root_path)
    snapshots = tuple(
        ModelSnapshot(
            config_name=model.config_name, provider=model.provider, model=model.model,
            base_url=model.base_url, parameters=model.parameters.model_dump(mode="json"),
            prompt_sha256=prompt_hash, revision=revision,
            sdk_version=_sdk_version(model.provider),
        )
        for model in spec.models
    )
    spec_payload = spec.model_dump(mode="json")
    spec_hash = _canonical_hash(spec_payload)
    raw = {
        "experiment_id": spec.experiment_id, "seed_set_id": seed_set_id,
        "seed_set_sha256": seed_hash, "seeds": seeds,
        "training_seed_set_sha256": training_hash,
        "memory_artifact_sha256": memory_hash,
        "models": [asdict(snapshot) for snapshot in snapshots],
        "variants": [variant.model_dump(mode="json") for variant in spec.variants],
        "source_revision": revision, "spec_sha256": spec_hash,
        "source_dirty": source_dirty, "dependency_sha256": dependency_hash,
        "sampling_reproducibility": "provider-dependent; all controllable parameters fixed",
    }
    return RunManifest(
        experiment_id=spec.experiment_id,
        seed_set_id=seed_set_id,
        seed_set_sha256=seed_hash,
        seeds=seeds,
        training_seed_set_sha256=training_hash,
        memory_artifact_sha256=memory_hash,
        models=snapshots,
        variants=tuple(variant.model_dump(mode="json") for variant in spec.variants),
        source_revision=revision,
        source_dirty=source_dirty,
        dependency_sha256=dependency_hash,
        spec_sha256=spec_hash,
        sampling_reproducibility="provider-dependent; all controllable parameters fixed",
        manifest_sha256=_canonical_hash(raw),
    )


def load_memory_artifact(path: str | Path) -> tuple[dict[str, str], str]:
    """Load a frozen long-term-memory artifact keyed by stable player slot."""
    artifact_path = Path(path)
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError(f"invalid memory artifact {artifact_path}: {exc}") from exc
    recorded_hash = payload.pop("artifact_sha256", None)
    artifact_id = payload.get("memory_artifact_id")
    memories = payload.get("memories")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise PreflightError("memory artifact requires memory_artifact_id")
    if not isinstance(memories, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in memories.items()
    ):
        raise PreflightError("memory artifact memories must be a string-to-string object")
    artifact_hash = _canonical_hash(payload)
    if recorded_hash is not None and recorded_hash != artifact_hash:
        raise PreflightError("memory artifact sha256 does not match its contents")
    return dict(memories), artifact_hash


def load_seed_set(path: str | Path) -> tuple[str, tuple[str, ...], str]:
    return _load_seed_set(Path(path))


def canonical_hash(value: Any) -> str:
    return _canonical_hash(value)


def _load_seed_set(path: Path) -> tuple[str, tuple[str, ...], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError(f"invalid seed set {path}: {exc}") from exc
    seed_set_id = payload.get("seed_set_id")
    seeds = payload.get("seeds")
    if not isinstance(seed_set_id, str) or not seed_set_id:
        raise PreflightError("seed set requires seed_set_id")
    if not isinstance(seeds, list) or not seeds or not all(isinstance(seed, str) and seed for seed in seeds):
        raise PreflightError("seed set requires non-empty string seeds")
    if len(seeds) != len(set(seeds)):
        raise PreflightError("seed set contains duplicate seeds")
    canonical = {"seed_set_id": seed_set_id, "seeds": seeds}
    return seed_set_id, tuple(seeds), _canonical_hash(canonical)


def _prompt_hash(root: Path) -> str:
    prompt_dir = root / "src/backend/arena/agent/prompts"
    digest = hashlib.sha256()
    for path in sorted(prompt_dir.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _git_revision(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _git_dirty(root: Path) -> bool:
    try:
        output = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=root, text=True,
            stderr=subprocess.DEVNULL,
        )
        return bool(output.strip())
    except (OSError, subprocess.CalledProcessError):
        return True


def _dependency_hash(root: Path) -> str:
    digest = hashlib.sha256()
    paths = [
        root / "src/backend/pyproject.toml",
        root / "src/backend/requirements.txt",
        root / "src/frontend/package-lock.json",
    ]
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes() if path.exists() else b"missing")
    return digest.hexdigest()


def _sdk_version(provider: str) -> str:
    package = "anthropic" if provider == "claude" else "openai"
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _canonical_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()
