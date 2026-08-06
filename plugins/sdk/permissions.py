"""PluginPermissions: granular permission checks."""
from __future__ import annotations

from typing import Optional, Set


ALL_PERMISSIONS = {
    "filesystem",
    "network",
    "clipboard",
    "desktop_automation",
    "calendar",
    "email",
    "browser",
    "memory",
    "rag",
    "workflows",
    "workspace",
    "voice",
}


class PluginPermissions:
    def __init__(self, granted: Optional[Set[str]] = None) -> None:
        self._granted = granted or set()

    def grant(self, permission: str) -> None:
        if permission not in ALL_PERMISSIONS:
            raise ValueError(f"Unknown permission: {permission}")
        self._granted.add(permission)

    def revoke(self, permission: str) -> None:
        self._granted.discard(permission)

    def has(self, permission: str) -> bool:
        return permission in self._granted

    def check(self, permission: str) -> None:
        if not self.has(permission):
            raise PermissionError(f"Permission denied: {permission}")

    def granted(self) -> Set[str]:
        return set(self._granted)
