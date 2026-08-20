"""FastAPI lifespan management for Jarvis Web server."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI

from runtime.runtime import build_runtime, shutdown as rt_shutdown
from modules.config import JarvisConfig
from web.server.auth import verify_web_security

logger = logging.getLogger("web.server")

# Global runtime context (single instance per process)
_runtime_ctx: Any = None


def _get_repo_root() -> Path:
    """Get the repository root directory."""
    return Path(__file__).resolve().parent.parent.parent


def build_web_runtime(config: Optional[JarvisConfig] = None) -> Any:
    """Build the shared runtime context for web server.
    
    This reuses the existing build_runtime factory from runtime.runtime
    to ensure we don't create duplicate managers.
    """
    repo = _get_repo_root()
    return build_runtime(config=config, repo=repo)


async def shutdown_web_runtime(ctx: Any) -> None:
    """Shutdown the web runtime context."""
    if ctx is not None:
        rt_shutdown(ctx)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager.
    
    Startup:
    - Validate security configuration
    - Build runtime context if web_enabled is True
    - Start background subsystems (workspace, proactive, ollama_health)
    - Set up event bridge for WebSocket clients
    
    Shutdown:
    - Gracefully shutdown all subsystems
    """
    global _runtime_ctx
    
    config = JarvisConfig.from_yaml(_get_repo_root() / "config.yaml")
    
    # Always store config on app state for dependency injection
    app.state.config = config
    
    # Validate security configuration at startup
    try:
        await verify_web_security(config)
    except ValueError as exc:
        logger.error("Security validation failed: %s", exc)
        raise
    
    if not config.web_enabled:
        logger.info("Web server disabled (web_enabled=false)")
        yield
        return
    
    logger.info("Starting Jarvis Web server...")
    
    # Build runtime using the existing factory
    _runtime_ctx = build_web_runtime(config=config)
    
    # Check for construction errors
    if _runtime_ctx.errors:
        logger.warning("Runtime construction completed with %d error(s): %s", 
                      len(_runtime_ctx.errors), _runtime_ctx.errors)
    
    # Start background subsystems
    try:
        from runtime.runtime import startup as rt_startup
        rt_startup(_runtime_ctx)
        logger.info("Background subsystems started")
    except Exception as exc:
        logger.error("Failed to start background subsystems: %s", exc)
    
    # Set up event bridge for WebSocket clients
    try:
        from web.server.routes.ws import setup_event_bridge
        setup_event_bridge(_runtime_ctx)
        logger.info("Event bridge initialized")
    except Exception as exc:
        logger.error("Failed to set up event bridge: %s", exc)
    
    # Store runtime context on app state for dependency injection
    app.state.runtime = _runtime_ctx
    
    logger.info("Jarvis Web server started on %s:%s", config.web_host, config.web_port)
    
    try:
        yield
    finally:
        logger.info("Shutting down Jarvis Web server...")
        if _runtime_ctx is not None:
            await shutdown_web_runtime(_runtime_ctx)
            _runtime_ctx = None
        logger.info("Jarvis Web server stopped")