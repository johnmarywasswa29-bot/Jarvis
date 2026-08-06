from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class JarvisConfig:
    """Central configuration object for Jarvis."""
    project_root: Path = Path(__file__).resolve().parent.parent
    
    # Wake word
    keyword_path: str = "assets/jarvis.ppn"
    sensitivities: list[float] = field(default_factory=lambda: [0.6])
    
    # VAD
    frame_ms: int = 30
    threshold: float = 0.01
    
    # Speech-to-text
    stt_model_name: str = "small"
    stt_backend: str = "vosk"
    _stt_models_dir: Path | None = None
    
    # LLM
    llm_provider: str = "ollama"
    llm_model: str = "llama3"
    llm_base_url: str = "http://localhost:11434"
    llm_fallback_model: str = "llama2"
    llm_timeout_s: float = 12.0
    ollama_health_interval_s: float = 30.0
    ollama_warning_latency_s: float = 8.0
    ollama_critical_latency_s: float = 20.0
    ollama_auto_reconnect: bool = True
    ollama_degraded_mode: bool = True
    
    # TTS
    tts_engine: str = "pyttsx3"
    tts_rate: int = 170
    tts_volume: float = 0.9
    
    # Vision
    vision_mode: str = "gpt-4v"
    
    # Memory
    memory_persist_directory: str = "memory"
    memory_collection: str = "jarvis_memory"
    
    # Tools
    web_search_enabled: bool = True
    desktop_control_enabled: bool = True
    code_execution_enabled: bool = True
    filesystem_enabled: bool = True
    vision_enabled: bool = True
    
    # Paths
    downloads: str = str(Path.home() / "Downloads")
    desktop: str = str(Path.home() / "Desktop")
    documents: str = str(Path.home() / "Documents")

    # Knowledge / RAG
    knowledge_root: str = str(Path.home() / "Documents" / "JarvisKnowledge")
    knowledge_indexed_folders: list[str] = field(default_factory=lambda: [str(Path.home() / "Documents"), str(Path.home() / "Desktop")])
    knowledge_ignore_dirs: list[str] = field(default_factory=lambda: [".git", "__pycache__", "node_modules", "venv", ".venv", "dist", "build"])
    knowledge_ignore_extensions: list[str] = field(default_factory=lambda: [])
    knowledge_max_file_size: int = 20 * 1024 * 1024
    knowledge_auto_index_enabled: bool = True
    knowledge_auto_index_interval_s: float = 3600.0
    knowledge_chunk_size: int = 1200
    knowledge_chunk_overlap: int = 120
    knowledge_search_k: int = 5
    knowledge_max_context_chars: int = 2500

    # Habits
    habits_root: str = str(Path.home() / "Documents" / "JarvisHabits")
    habits_learn_interval_s: float = 1800.0
    habits_decay_half_life_days: float = 14.0
    habits_min_confidence: float = 0.3
    habits_suggest_threshold: float = 0.5
    habits_max_suggestions: int = 5

    @classmethod
    def from_yaml(cls, path: str | Path) -> "JarvisConfig":
        with open(path, "r", encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f)
        return cls(**data)
    
    def stt_models_dir(self) -> Path:
        if self._stt_models_dir is None:
            self._stt_models_dir = self.project_root / "assets" / "stt_models"
        return self._stt_models_dir
    
    def wake_word_path(self) -> Path:
        p = Path(self.keyword_path)
        if not p.is_absolute():
            p = self.project_root / p
        return p
    
    def memory_path(self) -> Path:
        p = Path(self.memory_persist_directory)
        if not p.is_absolute():
            p = self.project_root / p
        p.mkdir(parents=True, exist_ok=True)
        return p
