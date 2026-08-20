"""Configuration endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from web.server.dependencies import get_optional_runtime
from web.server.auth import optional_auth

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config")
async def get_config(request: Request, runtime = Depends(get_optional_runtime), _auth = Depends(optional_auth)):
    """Get non-sensitive configuration.

    Never exposes secrets like NVIDIA_API_KEY.
    Optional auth - works with or without authentication.
    """
    config = request.app.state.config
    
    return {
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "llm_base_url": config.llm_base_url,
        "llm_fallback_model": config.llm_fallback_model,
        "llm_timeout_s": config.llm_timeout_s,
        "tts_engine": config.tts_engine,
        "tts_rate": config.tts_rate,
        "tts_volume": config.tts_volume,
        "stt_model_name": config.stt_model_name,
        "stt_backend": config.stt_backend,
        "openwakeword_model": config.openwakeword_model,
        "openwakeword_threshold": config.openwakeword_threshold,
        "openwakeword_inference": config.openwakeword_inference,
        "vision_mode": config.vision_mode,
        "web_search_enabled": config.web_search_enabled,
        "desktop_control_enabled": config.desktop_control_enabled,
        "code_execution_enabled": config.code_execution_enabled,
        "filesystem_enabled": config.filesystem_enabled,
        "vision_enabled": config.vision_enabled,
        "web_enabled": config.web_enabled,
        "web_host": config.web_host,
        "web_port": config.web_port,
        # NVIDIA config (no secrets)
        "nvidia_base_url": config.nvidia_base_url,
        "nvidia_model": config.nvidia_model,
        "nvidia_temperature": config.nvidia_temperature,
        "nvidia_top_p": config.nvidia_top_p,
        "nvidia_max_tokens": config.nvidia_max_tokens,
        "nvidia_reasoning_budget": config.nvidia_reasoning_budget,
        "nvidia_enable_thinking": config.nvidia_enable_thinking,
        # Wake word
        "openwakeword_model": config.openwakeword_model,
        "openwakeword_threshold": config.openwakeword_threshold,
        "openwakeword_inference": config.openwakeword_inference,
    }