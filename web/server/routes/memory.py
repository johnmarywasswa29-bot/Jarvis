"""Memory API routes for Jarvis Web UI."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from web.server.dependencies import get_runtime, get_optional_runtime
from web.server.auth import optional_auth

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)
    types: Optional[list[str]] = None


class MemoryAddRequest(BaseModel):
    content: str
    memory_type: str = Field(default="episodic", pattern="^(episodic|semantic|procedural)$")
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = Field(default="user")
    tags: list[str] = Field(default_factory=list)
    related_memories: list[str] = Field(default_factory=list)
    deduplicate: bool = True


class MemoryResponse(BaseModel):
    memory_id: str
    memory_type: str
    content: str
    importance: float
    confidence: float
    access_count: int
    last_accessed: float
    created_at: float
    source: str
    tags: list[str]
    related_memories: list[str]
    decay_score: float


class MemorySearchResponse(BaseModel):
    results: list[dict[str, Any]]
    total: int


@router.get("/")
async def get_memory_status(request: Request, runtime = Depends(get_optional_runtime), _auth = Depends(optional_auth)):
    """Get memory system status. Optional auth."""
    if runtime is None or runtime.memory_manager is None:
        return {
            "available": False,
            "message": "Memory system not initialized"
        }
    
    try:
        mm = runtime.memory_manager
        # Get basic stats
        with mm._lock:
            total = mm._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        
        return {
            "available": True,
            "total_memories": total,
            "supports_semantic_search": mm._embed is not None or mm._shared_embed is not None
        }
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc)
        }


@router.post("/search")
async def search_memory(request: Request, search_req: MemorySearchRequest, runtime = Depends(get_runtime), _auth = Depends(optional_auth)):
    """Search memories. Optional auth."""
    if runtime.memory_manager is None:
        raise HTTPException(status_code=503, detail="Memory system not initialized")
    
    try:
        results = runtime.memory_manager.search(
            query=search_req.query,
            limit=search_req.limit,
            types=search_req.types
        )
        
        # Sanitize results for web response
        sanitized = []
        for r in results:
            mem = r.get("memory")
            if mem is not None:
                sanitized.append({
                    "memory_id": mem.memory_id,
                    "memory_type": mem.memory_type,
                    "content": mem.content,
                    "importance": mem.importance,
                    "confidence": mem.confidence,
                    "access_count": mem.access_count,
                    "last_accessed": mem.last_accessed,
                    "created_at": mem.created_at,
                    "source": mem.source,
                    "tags": mem.tags,
                    "related_memories": mem.related_memories,
                    "decay_score": mem.decay_score,
                    "score": r.get("score", 0.0),
                    "recency_score": r.get("recency_score", 0.0),
                    "importance_score": r.get("importance_score", 0.0),
                    "confidence_score": r.get("confidence_score", 0.0),
                    "semantic_score": r.get("semantic_score", 0.0)
                })
        
        return MemorySearchResponse(results=sanitized, total=len(sanitized))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Memory search failed: {exc}")


@router.post("/")
async def add_memory(request: Request, memory_req: MemoryAddRequest, runtime = Depends(get_runtime), _auth = Depends(optional_auth)):
    """Add a new memory. Optional auth."""
    if runtime.memory_manager is None:
        raise HTTPException(status_code=503, detail="Memory system not initialized")
    
    try:
        record = runtime.memory_manager.add_memory(
            content=memory_req.content,
            memory_type=memory_req.memory_type,
            importance=memory_req.importance,
            confidence=memory_req.confidence,
            source=memory_req.source,
            tags=memory_req.tags,
            related_memories=memory_req.related_memories,
            deduplicate=memory_req.deduplicate
        )
        
        return {
            "memory_id": record.memory_id,
            "memory_type": record.memory_type,
            "content": record.content,
            "importance": record.importance,
            "confidence": record.confidence,
            "source": record.source,
            "tags": record.tags,
            "related_memories": record.related_memories,
            "created_at": record.created_at
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to add memory: {exc}")