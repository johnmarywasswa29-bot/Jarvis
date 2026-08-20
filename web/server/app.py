"""FastAPI application factory for Jarvis Web server."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from web.server.lifespan import lifespan
from web.server.routes import health, providers, config, ws, memory, history, workspace, voice, plugins, auth


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(
        title="Jarvis Web API",
        description="Local Desktop AI Assistant Web API",
        version="1.2.0-dev",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    
    # Include routers FIRST
    app.include_router(health.router)
    app.include_router(providers.router)
    app.include_router(config.router)
    app.include_router(ws.router)
    app.include_router(memory.router)
    app.include_router(history.router)
    app.include_router(workspace.router)
    app.include_router(voice.router)
    app.include_router(plugins.router)
    app.include_router(auth.router)
    
    # Serve frontend static files LAST (so API routes take precedence)
    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    
    return app


# For backward compatibility with lifespan module
def _get_config():
    """Placeholder - overridden by app.state.config in request context."""
    from modules.config import JarvisConfig
    return JarvisConfig()


def _get_runtime():
    """Placeholder - overridden by app.state.runtime in request context."""
    return None


# Make these available for dependencies module
import web.server.dependencies as deps
deps._get_config = _get_config
deps._get_runtime = _get_runtime