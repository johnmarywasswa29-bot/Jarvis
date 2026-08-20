"""Plugin API routes for Jarvis Web UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Any, List, Optional

from web.server.dependencies import get_runtime, get_optional_runtime
from web.server.auth import optional_auth

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class PluginEnableRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1)
    auto_load: bool = True


class PluginDisableRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1)


class PluginReloadRequest(BaseModel):
    plugin_id: str = Field(..., min_length=1)


class PluginListResponse(BaseModel):
    plugins: List[dict]


class PluginResponse(BaseModel):
    plugin_id: str
    name: str
    version: str
    author: str
    enabled: bool
    loaded: bool
    error: Optional[str] = None
    manifest: dict


@router.get("/", response_model=PluginListResponse)
async def list_plugins(request: Request, runtime = Depends(get_runtime), _auth = Depends(optional_auth)):
    """List all discovered plugins with their status. Optional auth."""
    if runtime is None or runtime.plugin_manager is None:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    
    try:
        plugins = runtime.plugin_manager.list_plugins()
        return PluginListResponse(plugins=plugins)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list plugins: {exc}")


@router.get("/{plugin_id}", response_model=PluginResponse)
async def get_plugin(plugin_id: str, request: Request, runtime = Depends(get_runtime), _auth = Depends(optional_auth)):
    """Get detailed info about a specific plugin. Optional auth."""
    if runtime is None or runtime.plugin_manager is None:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    
    try:
        ctx = runtime.plugin_manager.registry.get(plugin_id)
        if ctx is None:
            raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")
        
        return PluginResponse(
            plugin_id=ctx.plugin_id,
            name=ctx.manifest.name,
            version=ctx.manifest.version,
            author=ctx.manifest.author,
            enabled=ctx.enabled,
            loaded=ctx.loaded,
            error=ctx.error,
            manifest={
                "name": ctx.manifest.name,
                "version": ctx.manifest.version,
                "author": ctx.manifest.author,
                "description": ctx.manifest.description,
                "permissions": ctx.manifest.permissions,
                "required_api_version": ctx.manifest.required_api_version,
                "dependencies": ctx.manifest.dependencies,
                "entry_point": ctx.manifest.entry_point,
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get plugin: {exc}")


@router.post("/enable")
async def enable_plugin(req: PluginEnableRequest, request: Request, runtime = Depends(get_runtime), _auth = Depends(optional_auth)):
    """Enable a plugin (and optionally load it). Optional auth."""
    if runtime is None or runtime.plugin_manager is None:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    
    try:
        runtime.plugin_manager.enable(req.plugin_id, auto_load=req.auto_load)
        return {"success": True, "plugin_id": req.plugin_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to enable plugin: {exc}")


@router.post("/disable")
async def disable_plugin(req: PluginDisableRequest, request: Request, runtime = Depends(get_runtime), _auth = Depends(optional_auth)):
    """Disable a plugin. Optional auth."""
    if runtime is None or runtime.plugin_manager is None:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    
    try:
        runtime.plugin_manager.disable(req.plugin_id)
        return {"success": True, "plugin_id": req.plugin_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to disable plugin: {exc}")


@router.post("/reload")
async def reload_plugin(req: PluginReloadRequest, request: Request, runtime = Depends(get_runtime), _auth = Depends(optional_auth)):
    """Reload a plugin. Optional auth."""
    if runtime is None or runtime.plugin_manager is None:
        raise HTTPException(status_code=503, detail="Plugin manager not initialized")
    
    try:
        runtime.plugin_manager.reload(req.plugin_id)
        return {"success": True, "plugin_id": req.plugin_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to reload plugin: {exc}")


# Calendar plugin specific endpoints
@router.get("/calendar/events")
async def get_calendar_events(
    provider: str = "ics",
    start: Optional[str] = None,
    end: Optional[str] = None,
    request: Request = None,
    runtime = Depends(get_runtime),
    _auth = Depends(optional_auth)
):
    """Get calendar events from the calendar plugin. Optional auth."""
    if runtime is None or runtime.calendar_plugin is None:
        raise HTTPException(status_code=503, detail="Calendar plugin not available")
    
    try:
        # Default to today if no dates provided
        from datetime import datetime, timedelta
        if start is None:
            start = datetime.now().date().isoformat()
        if end is None:
            end = (datetime.now() + timedelta(days=7)).date().isoformat()
        
        events = runtime.calendar_plugin.get_events(provider_name=provider, start=start, end=end)
        return {"events": [e.__dict__ if hasattr(e, '__dict__') else str(e) for e in events]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get calendar events: {exc}")


@router.get("/calendar/reminders")
async def get_calendar_reminders(
    provider: Optional[str] = None,
    minutes_before: int = 15,
    request: Request = None,
    runtime = Depends(get_runtime),
    _auth = Depends(optional_auth)
):
    """Get upcoming calendar reminders. Optional auth."""
    if runtime is None or runtime.calendar_plugin is None:
        raise HTTPException(status_code=503, detail="Calendar plugin not available")
    
    try:
        reminders = runtime.calendar_plugin.reminders(provider_name=provider, minutes_before=minutes_before)
        return {"reminders": reminders}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get reminders: {exc}")


@router.get("/calendar/free-time")
async def get_calendar_free_time(
    provider: Optional[str] = None,
    request: Request = None,
    runtime = Depends(get_runtime),
    _auth = Depends(optional_auth)
):
    """Get free time slots from calendar. Optional auth."""
    if runtime is None or runtime.calendar_plugin is None:
        raise HTTPException(status_code=503, detail="Calendar plugin not available")
    
    try:
        free_time = runtime.calendar_plugin.free_time(provider_name=provider)
        return {"free_time": free_time}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get free time: {exc}")


@router.get("/calendar/conflicts")
async def get_calendar_conflicts(
    provider: Optional[str] = None,
    request: Request = None,
    runtime = Depends(get_runtime),
    _auth = Depends(optional_auth)
):
    """Get calendar conflicts. Optional auth."""
    if runtime is None or runtime.calendar_plugin is None:
        raise HTTPException(status_code=503, detail="Calendar plugin not available")
    
    try:
        conflicts = runtime.calendar_plugin.conflicts(provider_name=provider)
        return {"conflicts": conflicts}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get conflicts: {exc}")


@router.get("/calendar/search")
async def search_calendar(
    query: str,
    provider: Optional[str] = None,
    request: Request = None,
    runtime = Depends(get_runtime),
    _auth = Depends(optional_auth)
):
    """Search calendar events. Optional auth."""
    if runtime is None or runtime.calendar_plugin is None:
        raise HTTPException(status_code=503, detail="Calendar plugin not available")
    
    try:
        events = runtime.calendar_plugin.search(query=query, provider_name=provider)
        return {"events": [e.__dict__ if hasattr(e, '__dict__') else str(e) for e in events]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to search calendar: {exc}")