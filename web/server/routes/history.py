"""Chat history API routes for Jarvis Web UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from web.server.dependencies import get_runtime, get_optional_runtime
from web.server.auth import optional_auth

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/")
async def get_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    runtime = Depends(get_optional_runtime),
    _auth = Depends(optional_auth)
):
    """Get conversation history from chat memory. Optional auth."""
    if runtime is None or runtime.chat_memory is None:
        raise HTTPException(status_code=503, detail="Chat memory not initialized")
    
    try:
        # Get recent context from chat memory (v2)
        # This returns formatted conversation context
        context = runtime.chat_memory.get_recent_context(
            max_messages=limit + offset,
            max_chars=50000
        )
        
        # Parse the context into individual messages
        # The context format is "ROLE: content\nROLE: content..."
        lines = context.strip().split('\n') if context else []
        
        messages = []
        for line in lines[offset:offset+limit]:
            if ':' in line:
                role, content = line.split(':', 1)
                messages.append({
                    "role": role.strip().lower(),
                    "content": content.strip()
                })
        
        return {
            "messages": messages,
            "total": len(lines),
            "limit": limit,
            "offset": offset
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve history: {exc}")


@router.delete("/")
async def clear_history(request: Request, runtime = Depends(get_runtime), _auth = Depends(optional_auth)):
    """Clear chat history. Optional auth."""
    if runtime.chat_memory is None:
        raise HTTPException(status_code=503, detail="Chat memory not initialized")
    
    try:
        # Clear by creating a new memory instance or truncating
        # For now, we'll flush and the next context will be empty
        runtime.chat_memory.flush()
        
        return {"status": "cleared"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to clear history: {exc}")