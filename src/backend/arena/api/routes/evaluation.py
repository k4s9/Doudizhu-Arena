"""Experiment planning and resumable execution API."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ...evaluation.runner import EvaluationRunner
from ...evaluation.spec import ExperimentSpec, PreflightError, RunManifest, build_run_manifest

router = APIRouter(prefix="/evaluations", tags=["evaluations"])
ROOT = Path(__file__).resolve().parents[5]


def _repo(request: Request):
    return request.app.state.db_repo


def _decode(row: dict) -> dict:
    result = dict(row)
    for key in ("manifest_json", "spec_json"):
        if key in result and isinstance(result[key], str):
            result[key[:-5] if key.endswith("_json") else key] = json.loads(result[key])
    return result


def _preflight(repo, spec: ExperimentSpec):
    manifest = build_run_manifest(spec, ROOT)
    errors = []
    for model in spec.models:
        config = repo.get_player_config_by_name(model.config_name)
        if not config:
            errors.append(f"模型配置不存在: {model.config_name}")
        elif model.provider != "random" and not config.get("api_key"):
            errors.append(f"模型配置缺少 API 密钥: {model.config_name}")
    if len(spec.models) != 1:
        errors.append("阶段 4 首版消融实验要求恰好选择一个模型配置")
    if any(variant.memory_mode == "read_only" for variant in spec.variants) and not spec.memory_artifact_path:
        errors.append("只读记忆变体必须指定冻结的 memory_artifact_path")
    return manifest, errors


@router.post("/preflight")
async def preflight(request: Request):
    try:
        spec = ExperimentSpec.model_validate(await request.json())
        manifest, errors = _preflight(_repo(request), spec)
    except (ValueError, PreflightError) as exc:
        raise HTTPException(400, detail={"error": {"code": "PREFLIGHT_FAILED", "message": str(exc)}})
    return {"ok": not errors, "errors": errors, "task_count": len(manifest.seeds) * len(manifest.variants) * spec.seat_rotations, "manifest_sha256": manifest.manifest_sha256, "seed_count": len(manifest.seeds)}


@router.post("", status_code=201)
async def create_evaluation(request: Request):
    try:
        spec = ExperimentSpec.model_validate(await request.json())
        manifest, errors = _preflight(_repo(request), spec)
    except (ValueError, PreflightError) as exc:
        raise HTTPException(400, detail={"error": {"code": "PREFLIGHT_FAILED", "message": str(exc)}})
    if errors:
        raise HTTPException(400, detail={"error": {"code": "PREFLIGHT_FAILED", "message": "; ".join(errors)}})
    run_id = EvaluationRunner(_repo(request), str(ROOT)).create_run(spec, manifest)
    return {"run_id": run_id, "status": "planned", "task_count": len(_repo(request).get_evaluation_tasks(run_id))}


@router.get("")
async def list_evaluations(request: Request):
    return {"runs": [_decode(row) for row in _repo(request).list_evaluation_runs()]}


@router.get("/{run_id}")
async def get_evaluation(run_id: str, request: Request):
    run = _repo(request).get_evaluation_run(run_id)
    if not run:
        raise HTTPException(404, detail={"error": {"code": "RUN_NOT_FOUND", "message": "实验运行不存在"}})
    tasks = _repo(request).get_evaluation_tasks(run_id)
    counts = {status: sum(t["status"] == status for t in tasks) for status in ("planned", "running", "finished", "failed", "cancelled")}
    return {"run": _decode(run), "tasks": tasks, "counts": counts}


def _load_spec(repo, run_id: str):
    run = repo.get_evaluation_run(run_id)
    if not run:
        raise HTTPException(404, detail={"error": {"code": "RUN_NOT_FOUND", "message": "实验运行不存在"}})
    experiment = repo.get_experiment(run["experiment_id"])
    spec = ExperimentSpec.model_validate_json(experiment["spec_json"])
    stored = json.loads(run["manifest_json"])
    stored["seeds"] = tuple(stored["seeds"])
    # Rehydrate through the dataclass so start uses the immutable stored snapshot.
    from ...evaluation.spec import ModelSnapshot
    stored["models"] = tuple(ModelSnapshot(**model) for model in json.loads(run["manifest_json"])["models"])
    stored["variants"] = tuple(stored["variants"])
    manifest = RunManifest(**stored)
    return run, spec, manifest


@router.post("/{run_id}/start")
async def start_evaluation(run_id: str, request: Request):
    body = await request.json()
    if body.get("confirm_real_models") is not True:
        raise HTTPException(400, detail={"error": {"code": "CONFIRMATION_REQUIRED", "message": "必须明确确认真实模型调用与成本"}})
    run, spec, manifest = _load_spec(_repo(request), run_id)
    if run["status"] == "running":
        return {"run_id": run_id, "status": "running"}
    runner = EvaluationRunner(_repo(request), str(ROOT))
    request.app.state.active_evaluations[run_id] = runner

    async def execute():
        try:
            await runner.run(run_id, spec, manifest, real_models=True)
        finally:
            request.app.state.active_evaluations.pop(run_id, None)

    asyncio.create_task(execute())
    return {"run_id": run_id, "status": "running"}


@router.post("/{run_id}/cancel")
async def cancel_evaluation(run_id: str, request: Request):
    runner = request.app.state.active_evaluations.get(run_id)
    if runner:
        runner.cancel()
    else:
        _repo(request).update_evaluation_run_status(run_id, "cancelled")
    return {"run_id": run_id, "status": "cancelling" if runner else "cancelled"}


@router.post("/{run_id}/resume")
async def resume_evaluation(run_id: str, request: Request):
    return await start_evaluation(run_id, request)
