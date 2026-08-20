"""API routes package."""
from __future__ import annotations

from web.server.routes import health, providers, config, ws, memory, history, workspace, voice, plugins, auth

__all__ = ["health", "providers", "config", "ws", "memory", "history", "workspace", "voice", "plugins", "auth"]