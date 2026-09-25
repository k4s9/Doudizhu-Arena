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
from ..security.credentials import has_usable_credential


class PreflightError(ValueError):
    """Raised when an experiment is not safe or reproducible to run."""


class ProviderParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int = Field(gt=0, le=32768)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    seed: int | None = None


class PriceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    source: str
    effective_date: str
    currency: str = "USD"


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_name: str
    provider: str
    model: str
    base_url: str | None = None
    parameters: ProviderParameters
    system_prompt: str | None = None
    pricing: PriceSpec | None = None


class VariantSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    retry_limit: int = Field(ge=0, le=10)
    rule_feedback: bool
    enable_reflection: bool
    memory_mode: str = Field(pattern=r"^(disabled|train|read_only)$")

    @model_validator(mode="after")
    def validate_controls(self) -> "VariantSpec":
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
    max_tiebreaker_hands: int = Field(default=0, ge=0, le=100)
    max_calls: int = Field(default=1000, gt=0, le=1000000)
    max_input_tokens: int = Field(default=32768, gt=1024)
    task_timeout_seconds: float = Field(default=600, gt=0, le=86400)
    cancellation_deadline_seconds: float = Field(default=5, gt=0, le=30)
    mock_scenario: str = Field(default='initial_error', pattern=r'^(initial_error|valid_first|play_error)$')
    timeout_config: dict[str, float | int]
    pricing_version: str
    budget_limit_usd: float = Field(ge=0, allow_inf_nan=False)
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
    system_prompt: str | None = None
    prompt_templates: dict[str, str] = field(default_factory=dict)
    pricing: dict[str, Any] | None = None


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
    source_sha256: str = ""
    rules_version: str = ""
    frozen_spec: dict[str, Any] = field(default_factory=dict)


def load_experiment_spec(path: str | Path) -> ExperimentSpec:
    with Path(path).open(encoding="utf-8") as handle:
        return ExperimentSpec.model_validate(yaml.safe_load(handle))


def build_run_manifest(spec: ExperimentSpec, root: str | Path, repo=None) -> RunManifest:
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
    templates = {p.name: p.read_text(encoding="utf-8") for p in sorted((root_path / "src/backend/arena/agent/prompts").glob("*.py"))}
    snapshots = []
    for model in spec.models:
        config = repo.get_player_config_by_name(model.config_name) if repo else None
        endpoint = model.base_url or (config.get("base_url") if config else None) or {
            "openai": "https://api.openai.com/v1", "claude": "https://api.anthropic.com", "mock": "mock://local"}.get(model.provider)
        if endpoint:
            from urllib.parse import urlsplit
            url = urlsplit(endpoint)
            if url.username or url.password or url.query or url.fragment:
                raise PreflightError("base_url must not contain credentials, query or fragment")
        override = model.system_prompt if model.system_prompt is not None else (config.get("system_prompt") if config else None)
        snapshots.append(ModelSnapshot(
            config_name=model.config_name, provider=model.provider, model=model.model,
            base_url=endpoint, parameters=model.parameters.model_dump(mode="json"),
            system_prompt=override, prompt_templates=templates,
            pricing=model.pricing.model_dump(mode="json") if model.pricing else None,
            prompt_sha256=_canonical_hash({"templates": templates, "override": override}), revision=revision,
            sdk_version=_sdk_version(model.provider)))
    snapshots = tuple(snapshots)
    from ..engine.projection import RULES_VERSION
    source_hash = source_sha256(root_path)
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
        "source_sha256": source_hash, "rules_version": RULES_VERSION, "frozen_spec": spec_payload,
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
        manifest_sha256=_canonical_hash(raw), source_sha256=source_hash,
        rules_version=RULES_VERSION, frozen_spec=spec_payload,
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


def source_sha256(root):
    paths = sorted((Path(root)/"src/backend/arena").rglob("*.py"))
    return _canonical_hash({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def load_manifest(raw):
    raw = dict(raw)
    raw["models"] = tuple(ModelSnapshot(**m) for m in raw["models"])
    raw["seeds"] = tuple(raw["seeds"])
    raw["variants"] = tuple(raw["variants"])
    return RunManifest(**raw)


def validate_execution(spec, manifest, root, repo, *, mock=False):
    raw = asdict(manifest)
    raw.pop('manifest_sha256')
    if _canonical_hash(raw) != manifest.manifest_sha256:
        raise PreflightError('manifest hash mismatch')
    if _canonical_hash(spec.model_dump(mode="json")) != manifest.spec_sha256:
        raise PreflightError("spec does not match frozen manifest")
    if not manifest.source_sha256 or source_sha256(root) != manifest.source_sha256:
        raise PreflightError("runtime source differs from frozen manifest; create a new run")
    if _dependency_hash(Path(root)) != manifest.dependency_sha256:
        raise PreflightError('runtime dependency specification differs from frozen manifest')
    if any(_sdk_version(m.provider) != m.sdk_version for m in manifest.models):
        raise PreflightError('runtime SDK differs from frozen manifest')
    if len(manifest.models) != 1:
        raise PreflightError("first round requires one model")
    if any(v.enable_reflection or v.memory_mode != "disabled" for v in spec.variants):
        raise PreflightError("first round disables reflection/memory; read_only requires memory_artifact_path and a separate protocol")
    model = manifest.models[0]
    if model.provider != ("mock" if mock else model.provider) or (not mock and model.provider not in ("openai", "claude")):
        raise PreflightError("provider does not match execution mode")
    if not model.pricing or model.pricing.get("currency") != "USD":
        raise PreflightError("explicit USD pricing with source and effective date required")
    if not mock:
        if not model.model or any(x in model.model.lower() for x in ("replace", "placeholder", "tbd")):
            raise PreflightError("actual model required")
        config = repo.get_player_config_by_name(model.config_name)
        if not config or not has_usable_credential(config.get("api_key")):
            raise PreflightError("credential unavailable")
        if model.provider == "claude" and model.parameters.get("seed") is not None:
            raise PreflightError("Claude does not accept a sampling seed")
        if not model.pricing["source"].strip() or not model.pricing["effective_date"].strip():
            raise PreflightError("explicit real model pricing source and effective date required")
        if spec.budget_limit_usd == 0 and any(model.pricing[key] > 0 for key in (
            "input_per_million", "output_per_million"
        )):
            raise PreflightError("zero budget requires zero input and output prices")
