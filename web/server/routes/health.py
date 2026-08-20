"""Health check endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from web.server.dependencies import get_optional_runtime
from web.server.auth import optional_auth

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health_check(request: Request, runtime = Depends(get_optional_runtime), _auth = Depends(optional_auth)):
    """Health check endpoint.

    Returns basic health status. Works even when web_enabled=false.
    Optional auth - works with or without authentication.
    """
    config = request.app.state.config
    runtime = getattr(request.app.state, "runtime", None)
    
    return {
        "status": "healthy",
        "web_enabled": config.web_enabled,
        "runtime_initialized": runtime is not None,
        "runtime_errors": len(runtime.errors) if runtime else 0,
    }