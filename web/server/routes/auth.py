"""Authentication endpoints for Jarvis Web UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from web.server.auth import (
    get_current_config_from_request,
    is_auth_required,
    optional_auth,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthStatusResponse(BaseModel):
    """Response for auth status endpoint."""
    auth_enabled: bool
    auth_required: bool
    token_configured: bool


class VerifyTokenRequest(BaseModel):
    """Request to verify a token."""
    token: str


class VerifyTokenResponse(BaseModel):
    """Response for token verification."""
    valid: bool


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(request: Request):
    """Get authentication status (public endpoint)."""
    config = request.app.state.config
    return AuthStatusResponse(
        auth_enabled=config.web_auth_enabled,
        auth_required=is_auth_required(config),
        token_configured=bool(config.web_auth_token),
    )


@router.post("/verify", response_model=VerifyTokenResponse)
async def verify_token(
    req: VerifyTokenRequest,
    request: Request,
    config=Depends(get_current_config_from_request)
):
    """Verify a token (public endpoint for frontend to check)."""
    if not config.web_auth_enabled:
        return VerifyTokenResponse(valid=False)
    
    valid = req.token == config.web_auth_token
    return VerifyTokenResponse(valid=valid)