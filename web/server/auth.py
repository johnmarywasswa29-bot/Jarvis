"""Authentication module for Jarvis Web server.

Provides token-based authentication with fail-closed security model:
- REST endpoints protected by Bearer token
- WebSocket connections authenticated during handshake
- Fail-closed: authentication required when enabled or when binding to non-localhost
- Localhost bypass optional via config
- Non-localhost binding requires authentication
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from modules.config import JarvisConfig

logger = logging.getLogger("web.auth")

# Security scheme for Bearer token
bearer_scheme = HTTPBearer(auto_error=False)


class AuthError(Exception):
    """Authentication error."""
    def __init__(self, message: str, code: str = "UNAUTHORIZED"):
        self.message = message
        self.code = code
        super().__init__(message)


def validate_config(config: JarvisConfig) -> None:
    """Validate security configuration.
    
    Raises:
        ValueError: If configuration violates security requirements.
    """
    # Non-localhost binding requires authentication (regardless of web_enabled)
    if config.web_auth_require_for_non_localhost and config.web_host != "127.0.0.1":
        if not config.web_auth_enabled:
            raise ValueError(
                f"SECURITY ERROR: Binding to non-localhost ({config.web_host}) "
                f"requires web_auth_enabled=true. Set web_auth_enabled=true and "
                f"provide a web_auth_token, or bind to 127.0.0.1."
            )
        if not config.web_auth_token:
            raise ValueError(
                f"SECURITY ERROR: Non-localhost binding requires web_auth_token. "
                f"Set a secure token in config.yaml."
            )


async def get_current_config_from_request(request: Request) -> JarvisConfig:
    """Get configuration from app state (for REST endpoints)."""
    return request.app.state.config


async def get_current_config_from_ws(websocket: WebSocket) -> JarvisConfig:
    """Get configuration from app state (for WebSocket endpoints)."""
    return websocket.app.state.config


async def require_auth(
    request: Request,
    config: JarvisConfig = Depends(get_current_config_from_request),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    """Dependency that enforces authentication for REST endpoints.
    
    Raises:
        HTTPException: 401 if authentication required but missing/invalid.
    """
    # Skip if auth not enabled and not required for this host
    if not config.web_auth_enabled:
        if config.web_auth_required_localhost or config.web_host == "127.0.0.1":
            return  # No auth required for localhost when not required
        # Auth enabled but not required locally - still allow
        if config.web_host == "127.0.0.1":
            return
    
    # Auth is required (either globally enabled or non-localhost binding)
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Validate token
    if credentials.credentials != config.web_auth_token:
        logger.warning("Invalid auth token from %s", request.client.host if request.client else "unknown")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_ws_auth(
    websocket: WebSocket,
    config: JarvisConfig = Depends(get_current_config_from_ws),
) -> None:
    """Dependency that enforces authentication for WebSocket connections.
    
    Token is passed as query parameter: ?token=<token>
    
    Raises:
        WebSocketException: 4001 if authentication required but missing/invalid.
    """
    # Skip if auth not enabled and not required for this host
    if not config.web_auth_enabled:
        if config.web_auth_required_localhost or config.web_host == "127.0.0.1":
            return
        if config.web_host == "127.0.0.1":
            return
    
    # Auth is required
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
        raise Exception("Authentication required")
    
    if token != config.web_auth_token:
        logger.warning("Invalid WS auth token from %s", websocket.client.host if websocket.client else "unknown")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid authentication token")
        raise Exception("Invalid authentication token")


def optional_auth(
    request: Request,
    config: JarvisConfig = Depends(get_current_config_from_request),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[str]:
    """Optional authentication - returns token if valid, None otherwise.
    
    Use for endpoints that work with or without auth.
    """
    if not config.web_auth_enabled:
        return None
    
    if credentials and credentials.credentials == config.web_auth_token:
        return credentials.credentials
    
    return None


async def verify_web_security(config: JarvisConfig) -> None:
    """Verify web security configuration at startup.
    
    Raises:
        ValueError: If configuration is insecure.
    """
    validate_config(config)
    
    if config.web_auth_enabled and config.web_auth_token:
        # Token should be reasonably secure
        if len(config.web_auth_token) < 16:
            logger.warning("web_auth_token is short (<16 chars); consider a longer token")
    
    logger.info(
        "Web security: enabled=%s, host=%s, auth_required_localhost=%s, "
        "require_for_non_localhost=%s",
        config.web_auth_enabled,
        config.web_host,
        config.web_auth_required_localhost,
        config.web_auth_require_for_non_localhost,
    )