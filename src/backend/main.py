"""FastAPI entrypoint for Doudizhu Arena.

On startup: initializes DB, loads agents from YAML, registers routes.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from arena.config.settings import settings
from arena.logging_utils import setup_logging, get_logger

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    # Startup
    setup_logging()
    logger.info("Starting Doudizhu Arena backend...")

    # Init DB
    from arena.db.repository import DatabaseRepository
    repo = DatabaseRepository(
        str(Path(settings.database_url.replace("sqlite:///", "")))
    )
    repo.init()
    app.state.db_repo = repo
    logger.info("Database initialized at %s", settings.database_url)

    # Load configs + default players from YAML
    try:
        from arena.agent.loader import load_agents_from_yaml
        player_ids = load_agents_from_yaml(repo)
        logger.info("Loaded %d configs and %d default players from YAML",
                     len(repo.list_player_configs()), len(player_ids))
    except FileNotFoundError:
        logger.warning("agents.yaml not found — no configs loaded")

    # Mark orphaned matches as finished — server restart loses the in-memory
    # MatchRunner, so these matches can never resume. Preserve all hand data
    # for replay rather than destroying it.
    orphaned_statuses = ("running", "paused")
    for status in orphaned_statuses:
        for m in repo.list_matches(status=status, page=1, page_size=1000)[0]:
            logger.warning(
                "Orphaned %s match '%s' (%s) — marking as 'finished' (server restarted)",
                status, m["name"], m["id"],
            )
            repo.update_match_status(m["id"], "finished")

    yield

    # Shutdown
    if hasattr(app.state, 'db_repo'):
        app.state.db_repo.close()
    logger.info("Doudizhu Arena backend stopped.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Doudizhu Arena",
        version="0.1.0",
        lifespan=lifespan,
    )

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
