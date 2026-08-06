"""Plugin SDK state models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional


@dataclass
class PluginManifest:
    name: str
    version: str
    author: str
    description: str = ""
    permissions: List[str] = field(default_factory=list)
    required_api_version: str = "1.0.0"
    dependencies: List[str] = field(default_factory=list)
    entry_point: str = "plugin.py"


@dataclass
class PluginContext:
    plugin_id: str
    manifest: PluginManifest
    enabled: bool = False
    loaded: bool = False
    instance: Any = None
    install_path: str = ""
    error: Optional[str] = None


@dataclass
class PluginEvent:
    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    plugin_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None).isoformat())


@dataclass
class PermissionRequest:
    permission: str
    plugin_id: str
    granted: bool = False
