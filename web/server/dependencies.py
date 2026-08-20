"""FastAPI dependency injection for Jarvis Web server."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, HTTPException, Request
from modules.config import JarvisConfig

from web.server.auth import require_auth, require_ws_auth, optional_auth

def get_config(request: Request) -> JarvisConfig:
    """Dependency to get the application configuration from app state."""
    return request.app.state.config


def get_runtime(request: Request) -> Any:
    """Dependency to get the runtime context from app state."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not initialized")
    return runtime


def get_optional_runtime(request: Request) -> Optional[Any]:
    """Optional runtime dependency (for endpoints that work without web_enabled)."""
    return getattr(request.app.state, "runtime", None)