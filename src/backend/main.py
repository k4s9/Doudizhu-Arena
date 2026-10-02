"""FastAPI entrypoint for Doudizhu Arena.

On startup: initializes DB, loads agents from YAML, registers routes.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from arena.config.settings import settings
from arena.logging_utils import setup_logging, get_logger
from arena.security.credentials import CredentialError

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Startup
    setup_logging()
    logger.info("Starting Doudizhu Arena backend...")

    # Init DB
    from arena.db.repository import DatabaseRepository
    repo = DatabaseRepository(settings.database_path)
    repo.init()
    app.state.db_repo = repo
    from arena.api.match_lifecycle import (
        close_database_after_cleanup, ensure_task_registry,
        recover_interrupted_matches, shutdown_matches,
    )
    ensure_task_registry(app)
    app.state.shutting_down = False
    logger.info("Database initialized at %s", settings.database_url)

    recovered = recover_interrupted_matches(repo)
    if recovered:
        logger.warning("Recovered %d interrupted matches; completed hand data retained", recovered)

    # Load configs + default players from YAML
    try:
        from arena.agent.loader import load_agents_from_yaml
        with repo.atomic():
            player_ids = load_agents_from_yaml(repo)
        logger.info("Loaded %d configs and %d default players from YAML",
                     repo.conn.execute("SELECT COUNT(*) FROM player_configs").fetchone()[0], len(player_ids))
    except FileNotFoundError:
        logger.warning("agents.yaml not found — no configs loaded")
    except CredentialError:
        logger.warning("YAML configuration sync skipped: credential master key is unavailable "
                       "or invalid; stored matches remain available")

    try:
        yield
    finally:
        pending_cleanup = await shutdown_matches(app)
        close_database_after_cleanup(repo, pending_cleanup)
        logger.info("Doudizhu Arena backend stopped.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Doudizhu Arena",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.exception_handler(CredentialError)
    async def unavailable_credentials(request, exc):
        return JSONResponse(status_code=503, content={"error": {
            "code": "CREDENTIAL_UNAVAILABLE",
            "message": "模型凭据暂时无法解密，请检查服务器 DOUDIZHU_CREDENTIAL_MASTER_KEY 后重试。",
        }})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check
    @app.get("/api/v1/health")
    async def health():
        import time
        return {
            "status": "ok",
            "version": "0.1.0",
            "uptime_seconds": 0,  # placeholder until proper tracking
        }

    # M5: REST API routes
    from arena.api.routes import match, replay, agent, config, player, evaluation
    app.include_router(match.router, prefix="/api/v1")
    app.include_router(replay.router, prefix="/api/v1")
    app.include_router(agent.router, prefix="/api/v1")
    app.include_router(config.router, prefix="/api/v1")
    app.include_router(player.router, prefix="/api/v1")
    app.include_router(evaluation.router, prefix="/api/v1")

    # M5: WebSocket handler
    from arena.api.ws import router as ws_router
    app.include_router(ws_router)

    # M5: Active match registry for WebSocket state access
    app.state.active_matches = {}
    app.state.active_evaluations = {}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)
