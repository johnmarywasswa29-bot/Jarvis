"""Workspace API routes for Jarvis Web UI."""
from __future__ import annotations

import os
from fastapi import APIRouter, Depends, HTTPException, Request

from web.server.dependencies import get_runtime, get_optional_runtime
from web.server.auth import optional_auth

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


@router.get("/")
async def get_workspace(request: Request, runtime = Depends(get_optional_runtime), _auth = Depends(optional_auth)):
    """Get current workspace snapshot. Optional auth."""
    if runtime is None or runtime.workspace_manager is None:
        return {
            "available": False,
            "message": "Workspace manager not initialized"
        }
    
    try:
        wm = runtime.workspace_manager
        snapshot = wm.snapshot()
        
        if snapshot is None:
            return {
                "available": True,
                "current_project": None,
                "working_directory": None,
                "git_repository": None,
                "active_applications": [],
                "confidence": 0.0,
                "message": "No workspace snapshot available"
            }
        
        # Sanitize the snapshot - remove absolute paths and sensitive info
        working_dir = snapshot.working_directory
        # Only return the last component of the path for privacy
        import os
        working_dir_name = os.path.basename(working_dir) if working_dir else None
        
        project = wm.current_project()
        
        return {
            "available": True,
            "current_project": {
                "name": project.name if project else snapshot.active_project,
                "language": project.language if project else None,
                "ide": project.ide if project else None,
                "git_repository": project.git_repo if project else None,
            },
            "working_directory_name": working_dir_name,
            "git_repository": snapshot.git_repository,
            "active_applications": snapshot.open_applications,
            "confidence": snapshot.confidence,
        }
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc)
        }


@router.get("/projects")
async def get_recent_projects(request: Request, runtime = Depends(get_optional_runtime), _auth = Depends(optional_auth)):
    """Get recent projects from workspace history. Optional auth."""
    if runtime is None or runtime.workspace_manager is None:
        raise HTTPException(status_code=503, detail="Workspace manager not initialized")
    
    try:
        wm = runtime.workspace_manager
        projects = wm.recent_projects(limit=20)
        
        sanitized = []
        for p in projects:
            sanitized.append({
                "name": p.name,
                "language": p.language,
                "ide": p.ide,
                "git_repo": p.git_repo,
                "path_name": os.path.basename(p.path) if p.path else None,
            })
        
        return {"projects": sanitized}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get projects: {exc}")