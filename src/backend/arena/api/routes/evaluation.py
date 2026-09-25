"""Experiment planning and resumable execution API."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request

from ...evaluation.runner import EvaluationRunner
from ...evaluation.spec import ExperimentSpec, PreflightError, RunManifest, build_run_manifest
from ...security.credentials import has_usable_credential
from ...config.paths import PROJECT_ROOT as ROOT, EVALUATION_DIR

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


def _repo(request: Request):
    return request.app.state.db_repo


def _decode(row: dict) -> dict:
    result = dict(row)
    for key in ("manifest_json", "spec_json"):
        if key in result and isinstance(result[key], str):
            result[key[:-5] if key.endswith("_json") else key] = json.loads(result[key])
    return result


def _preflight(repo, spec: ExperimentSpec):
    manifest = build_run_manifest(spec, ROOT, repo)
    errors = []
    for model in spec.models:
        config = repo.get_player_config_by_name(model.config_name)
        if not config and model.provider != "mock":
            errors.append(f"模型配置不存在: {model.config_name}")
        elif config and model.provider not in ("random", "mock") and not has_usable_credential(config.get("api_key")):
            errors.append(f"模型配置缺少 API 密钥: {model.config_name}")
    if len(spec.models) != 1:
        errors.append("阶段 4 首版消融实验要求恰好选择一个模型配置")
    if any(variant.memory_mode == "read_only" for variant in spec.variants) and not spec.memory_artifact_path:
        errors.append("只读记忆变体必须指定冻结的 memory_artifact_path")
    from ...evaluation.spec import validate_execution
    if not errors:
        try:
            validate_execution(spec, manifest, ROOT, repo, mock=manifest.models[0].provider == 'mock')
        except PreflightError as exc:
            errors.append(str(exc))
    return manifest, errors


@router.get('/template')
async def get_template(mode: str = 'mock'):
    from ...evaluation.spec import load_experiment_spec
    if mode not in ('mock', 'real'):
        raise HTTPException(400, detail='mode must be mock or real')
    spec = load_experiment_spec(EVALUATION_DIR / 'experiments/mock-v2.yaml').model_dump(mode='json')
    spec['mock_scenario'] = 'valid_first'
    if mode == 'real':
        from dataclasses import asdict
        from ...engine.timeout import TimeoutConfig
        spec.update(
            experiment_id='reliability-smoke-v2',
            seed_set_path='src/backend/evaluation/seed_sets/smoke-v1.json',
            # Real decisions use the engine's normal limits, not mock latency.
            timeout_config=asdict(TimeoutConfig()),
            task_timeout_seconds=3600,
            pricing_version='user-supplied-v1',
            budget_limit_usd=None,
        )
        spec['models'] = []  # The user supplies the actual model and verified pricing.
    return spec


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
    from ...evaluation.spec import load_manifest
    stored = json.loads(run["manifest_json"])
    if not stored.get("frozen_spec"):
        raise HTTPException(409, detail="Legacy run must be replanned with frozen inputs")
    spec = ExperimentSpec.model_validate(stored["frozen_spec"])
    manifest = load_manifest(stored)
    return run, spec, manifest


@router.post("/{run_id}/start")
async def start_evaluation(run_id: str, request: Request):
    body = await request.json()
    run, spec, manifest = _load_spec(_repo(request), run_id)
    mock = manifest.models[0].provider == "mock"
    if not mock and body.get("confirm_real_models") is not True:
        raise HTTPException(400, detail={"error": {"code": "CONFIRMATION_REQUIRED", "message": "必须明确确认真实模型调用与成本"}})
    if run_id in request.app.state.active_evaluations:
        return {"run_id": run_id, "status": "running"}
    from ...evaluation.spec import validate_execution
    try:
        validate_execution(spec, manifest, ROOT, _repo(request), mock=mock)
    except PreflightError as exc:
        raise HTTPException(400, detail=str(exc))
    runner = EvaluationRunner(_repo(request), str(ROOT), request.app.state.active_matches)
    request.app.state.active_evaluations[run_id] = runner

    async def execute():
        try:
            await runner.run(run_id, spec, manifest, real_models=not mock, mock=mock)
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


@router.get("/{run_id}/report")
async def get_report(run_id: str, request: Request):
    repo = _repo(request)
    _load_spec(repo, run_id)
    from ...evaluation.report import build_report
    manifest, integrity, summary, metrics, seeds, failures = build_report(repo, run_id)
    return {"integrity": integrity, "summary": summary, "seed_level": seeds, "failures": failures}


@router.get("/{run_id}/artifacts/{filename}")
async def get_artifact(run_id: str, filename: str, request: Request):
    from fastapi.responses import FileResponse
    if filename not in {"manifest.json","integrity.json","summary.json","metrics.csv","seed_level.csv","report.md"}:
        raise HTTPException(404,detail="unknown artifact")
    _load_spec(_repo(request),run_id)
    from ...evaluation.report import export_report
    out=ROOT/"data/evaluations"/run_id
    export_report(_repo(request),run_id,out)
    return FileResponse(out/filename,filename=filename)
