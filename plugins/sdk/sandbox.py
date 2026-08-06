"""PluginSandbox: minimal runtime guard."""
from __future__ import annotations

from typing import Any, Optional

from plugins.sdk.permissions import PluginPermissions
from plugins.sdk.state import PluginContext


class PluginSandbox:
    def __init__(self, context: PluginContext, permissions: Optional[PluginPermissions] = None) -> None:
        self.context = context
        self.permissions = permissions or PluginPermissions()

    def enforce(self, permission: str) -> None:
        self.permissions.check(permission)

    def safe_call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        instance = self.context.instance
        if instance is None:
            raise RuntimeError("Plugin not loaded")
        method = getattr(instance, method_name, None)
        if method is None:
            raise AttributeError(f"Plugin missing {method_name}")
        return method(*args, **kwargs)
