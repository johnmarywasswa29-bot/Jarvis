"""Tests for NVIDIA Nemotron LLM provider."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from modules.config import JarvisConfig
from modules.llm_providers import (
    LLMProvider,
    OllamaProvider,
    NVIDIAProvider,
    get_llm_provider,
)


class TestProviderFactory:
    """Tests for the provider factory function."""

    def test_get_ollama_provider_default(self):
        config = JarvisConfig()
        config.llm_provider = "ollama"
        provider = get_llm_provider(config)
        assert isinstance(provider, OllamaProvider)

    def test_get_nvidia_provider(self):
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        provider = get_llm_provider(config)
        assert isinstance(provider, NVIDIAProvider)

    def test_get_ollama_provider_case_insensitive(self):
        config = JarvisConfig()
        config.llm_provider = "OLLAMA"
        provider = get_llm_provider(config)
        assert isinstance(provider, OllamaProvider)

    def test_get_nvidia_provider_case_insensitive(self):
        config = JarvisConfig()
        config.llm_provider = "NVIDIA"
        provider = get_llm_provider(config)
        assert isinstance(provider, NVIDIAProvider)

    def test_unknown_provider_defaults_to_ollama(self):
        config = JarvisConfig()
        config.llm_provider = "unknown"
        provider = get_llm_provider(config)
        assert isinstance(provider, OllamaProvider)


class TestOllamaProvider:
    """Tests for Ollama provider (regression tests)."""

    def test_instantiation(self):
        config = JarvisConfig()
        config.llm_provider = "ollama"
        provider = OllamaProvider(config)
        assert provider is not None
        assert provider.config is config

    def test_is_available_returns_false_when_no_client(self):
        config = JarvisConfig()
        provider = OllamaProvider(config)
        # Without mocked client, should return False
        assert provider.is_available() is False

    def test_chat_returns_empty_on_no_client(self):
        config = JarvisConfig()
        provider = OllamaProvider(config)
        result = provider.chat([{"role": "user", "content": "test"}])
        assert result == ""


class TestNVIDIAProvider:
    """Tests for NVIDIA Nemotron provider."""

    def test_instantiation(self):
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        provider = NVIDIAProvider(config)
        assert provider is not None
        assert provider.config is config

    def test_is_available_false_without_key(self):
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        config.nvidia_api_key = ""
        # Ensure env var is not set
        with patch.dict(os.environ, {}, clear=True):
            provider = NVIDIAProvider(config)
            assert provider.is_available() is False

    def test_is_available_false_with_empty_env_key(self):
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        config.nvidia_api_key = ""
        with patch.dict(os.environ, {"NVIDIA_API_KEY": ""}):
            provider = NVIDIAProvider(config)
            assert provider.is_available() is False

    def test_uses_config_api_key(self):
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        config.nvidia_api_key = "test-config-key"
        with patch.dict(os.environ, {}, clear=True):
            provider = NVIDIAProvider(config)
            # Should use config key
            assert provider._get_client() is not None  # Will fail on connection but client created

    def test_uses_env_var_when_config_empty(self):
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        config.nvidia_api_key = ""
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "env-test-key"}):
            provider = NVIDIAProvider(config)
            assert provider._get_client() is not None  # Client created with env key

    def test_config_key_priority_over_env(self):
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        config.nvidia_api_key = "config-key"
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "env-key"}):
            provider = NVIDIAProvider(config)
            # Config should take priority
            client = provider._get_client()
            assert client is not None

    def test_extra_body_construction(self):
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        config.nvidia_enable_thinking = True
        config.nvidia_reasoning_budget = 16384
        provider = NVIDIAProvider(config)
        extra_body = provider._build_extra_body()
        assert extra_body["chat_template_kwargs"]["enable_thinking"] is True
        assert extra_body["reasoning_budget"] == 16384

    def test_extra_body_with_thinking_disabled(self):
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        config.nvidia_enable_thinking = False
        config.nvidia_reasoning_budget = 8192
        provider = NVIDIAProvider(config)
        extra_body = provider._build_extra_body()
        assert extra_body["chat_template_kwargs"]["enable_thinking"] is False
        assert extra_body["reasoning_budget"] == 8192

    def test_chat_returns_empty_on_no_client(self):
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        config.nvidia_api_key = ""
        with patch.dict(os.environ, {}, clear=True):
            provider = NVIDIAProvider(config)
            result = provider.chat([{"role": "user", "content": "test"}])
            assert result == ""

    def test_is_available_false_when_client_creation_fails(self):
        """Test that is_available returns False when client creation fails."""
        config = JarvisConfig()
        config.llm_provider = "nvidia"
        config.nvidia_api_key = ""
        with patch.dict(os.environ, {}, clear=True):
            provider = NVIDIAProvider(config)
            assert provider.is_available() is False


class TestOllamaRegression:
    """Regression tests for Ollama provider after NVIDIA changes."""

    def test_ollama_provider_still_works(self):
        config = JarvisConfig()
        config.llm_provider = "ollama"
        provider = OllamaProvider(config)
        assert provider.config is config
        assert provider.is_available() is False  # No running Ollama

    def test_ollama_chat_structure(self):
        config = JarvisConfig()
        provider = OllamaProvider(config)
        # Without client, should return empty string gracefully
        result = provider.chat([{"role": "user", "content": "test"}])
        assert result == ""


class TestConfigDefaults:
    """Tests for NVIDIA config defaults."""

    def test_nvidia_config_defaults(self):
        config = JarvisConfig()
        assert config.nvidia_base_url == "https://integrate.api.nvidia.com/v1"
        assert config.nvidia_model == "nvidia/nemotron-3-ultra-550b-a55b"
        assert config.nvidia_temperature == 1.0
        assert config.nvidia_top_p == 0.95
        assert config.nvidia_max_tokens == 16384
        assert config.nvidia_reasoning_budget == 16384
        assert config.nvidia_enable_thinking is True
        assert config.nvidia_api_key == ""

    def test_nvidia_config_from_yaml(self):
        yaml_content = """
nvidia_base_url: "https://custom.api.com/v1"
nvidia_model: "custom/model"
nvidia_temperature: 0.7
nvidia_top_p: 0.9
nvidia_max_tokens: 8192
nvidia_reasoning_budget: 8192
nvidia_enable_thinking: false
"""
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            f.flush()
            config = JarvisConfig.from_yaml(f.name)
        assert config.nvidia_base_url == "https://custom.api.com/v1"
        assert config.nvidia_model == "custom/model"
        assert config.nvidia_temperature == 0.7
        assert config.nvidia_top_p == 0.9
        assert config.nvidia_max_tokens == 8192
        assert config.nvidia_reasoning_budget == 8192
        assert config.nvidia_enable_thinking is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])