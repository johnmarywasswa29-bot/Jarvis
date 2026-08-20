"""Providers endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from web.server.dependencies import get_optional_runtime
from web.server.auth import optional_auth

router = APIRouter(prefix="/api", tags=["providers"])


@router.get("/providers")
async def get_providers(request: Request, runtime = Depends(get_optional_runtime), _auth = Depends(optional_auth)):
    """Get available LLM providers and current selection.
    
    Optional auth - works with or without authentication.
    """
    config = request.app.state.config
    runtime = getattr(request.app.state, "runtime", None)
    
    providers = [
        {
            "id": "ollama",
            "name": "Ollama",
            "available": False,
            "models": [],
            "base_url": config.llm_base_url,
            "default_model": config.llm_model,
        },
        {
            "id": "nvidia",
            "name": "NVIDIA Nemotron",
            "available": False,
            "models": [],
            "base_url": config.nvidia_base_url,
            "default_model": config.nvidia_model,
        },
    ]
    
    # Check provider availability if runtime is available
    if runtime is not None:
        from modules.llm_providers import get_llm_provider
        
        # Check Ollama
        config.llm_provider = "ollama"
        ollama_provider = get_llm_provider(config)
        providers[0]["available"] = ollama_provider.is_available()
        
        # Check NVIDIA
        config.llm_provider = "nvidia"
        nvidia_provider = get_llm_provider(config)
        providers[1]["available"] = nvidia_provider.is_available()
        
        # Restore config
        config.llm_provider = config.llm_provider
    
    return {
        "current": config.llm_provider,
        "providers": providers,
    }