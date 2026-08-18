# plugins.sdk package - minimal init to avoid circular imports
# Import only what's actually available in the modules
from plugins.sdk.api import PluginAPI
from plugins.sdk.manager import PluginManager
from plugins.sdk.events import PluginEvents, PluginEvent
from plugins.sdk.loader import PluginLoader
from plugins.sdk.permissions import PluginPermissions
from plugins.sdk.registry import PluginRegistry
from plugins.sdk.sandbox import PluginSandbox
from plugins.sdk.state import PluginContext, PluginManifest, PluginEvent, PermissionRequest, PluginEventType

__all__ = [
    # api
    "PluginAPI",
    # manager
    "PluginManager",
    # events
    "PluginEvents",
    "PluginEvent",
    "PluginEventType",
    # other modules
    "PluginLoader",
    "PluginPermissions",
    "PluginRegistry",
    "PluginSandbox",
    "PluginContext",
    "PluginManifest",
    "PermissionRequest",
]