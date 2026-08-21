from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Optional, AsyncGenerator
from modules.config import JarvisConfig
from modules.logger import get_logger


logger = get_logger("llm_providers")


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def chat(self, messages: list[dict[str, str]], *, stream: bool = False, **kwargs) -> Any:
        """Send a chat completion request."""
        pass

    @abstractmethod
    def stream_chat(self, messages: list[dict[str, str]], **kwargs) -> AsyncGenerator[str, None]:
        """Stream a chat completion."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is available/configured."""
        pass


class OllamaProvider(LLMProvider):
    """Ollama LLM provider (original implementation)."""

    def __init__(self, config: JarvisConfig) -> None:
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from ollama import Client
                self._client = Client(host=self.config.llm_base_url)
            except Exception as exc:
                logger.error("Failed to create Ollama client: %s", exc)
                self._client = None
        return self._client

    def chat(self, messages: list[dict[str, str]], *, stream: bool = False, **kwargs) -> str:
        client = self._get_client()
        if client is None:
            return ""

        options = {"num_predict": 1024, "temperature": 0.1}
        options.update(kwargs.get("options", {}))

        try:
            response = client.chat(
                model=self.config.llm_model,
                messages=messages,
                options=options,
                stream=stream,
            )
            if stream:
                return response
            msg = getattr(response, "message", None)
            if msg is None:
                return ""
            content = getattr(msg, "content", "")
            return content.strip()
        except Exception as exc:
            logger.error("Ollama chat failed: %s", exc)
            return ""

    async def stream_chat(self, messages: list[dict[str, str]], **kwargs) -> AsyncGenerator[str, None]:
        client = self._get_client()
        if client is None:
            return
        options = {"num_predict": 1024, "temperature": 0.1}
        options.update(kwargs.get("options", {}))

        try:
            response = client.chat(
                model=self.config.llm_model,
                messages=messages,
                options=options,
                stream=True,
            )
            for chunk in response:
                msg = getattr(chunk, "message", None)
                if msg is not None:
                    token = getattr(msg, "content", "")
                    if token:
                        yield token
        except Exception as exc:
            logger.error("Ollama stream open failed: %s", exc)

    def is_available(self) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            client.list()
            return True
        except Exception:
            return False


class NVIDIAProvider(LLMProvider):
    """NVIDIA Nemotron LLM provider using OpenAI-compatible API."""

    def __init__(self, config: JarvisConfig) -> None:
        self.config = config
        self._client = None

    def _get_client(self):
        if self._client is None:
            api_key = self.config.nvidia_api_key or os.environ.get("NVIDIA_API_KEY")
            if not api_key:
                logger.warning("NVIDIA_API_KEY not configured")
                return None
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=self.config.nvidia_base_url,
                    api_key=api_key,
                )
            except Exception as exc:
                logger.error("Failed to create NVIDIA client: %s", exc)
                self._client = None
        return self._client

    def _build_extra_body(self) -> dict:
        return {
            "chat_template_kwargs": {"enable_thinking": self.config.nvidia_enable_thinking},
            "reasoning_budget": self.config.nvidia_reasoning_budget,
        }

    def chat(self, messages: list[dict[str, str]], *, stream: bool = False, **kwargs) -> str:
        client = self._get_client()
        if client is None:
            return ""

        options = {
            "temperature": self.config.nvidia_temperature,
            "top_p": self.config.nvidia_top_p,
            "max_tokens": self.config.nvidia_max_tokens,
        }
        options.update(kwargs.get("options", {}))

        try:
            response = client.chat.completions.create(
                model=self.config.nvidia_model,
                messages=messages,
                stream=stream,
                extra_body=self._build_extra_body(),
                **options,
            )
            if stream:
                return response
            if response.choices and response.choices[0].message:
                return response.choices[0].message.content or ""
            return ""
        except Exception as exc:
            logger.error("NVIDIA chat failed: %s", exc)
            return ""

    async def stream_chat(self, messages: list[dict[str, str]], **kwargs) -> AsyncGenerator[str, None]:
        client = self._get_client()
        if client is None:
            return

        options = {
            "temperature": self.config.nvidia_temperature,
            "top_p": self.config.nvidia_top_p,
            "max_tokens": self.config.nvidia_max_tokens,
        }
        options.update(kwargs.get("options", {}))

        try:
            response = client.chat.completions.create(
                model=self.config.nvidia_model,
                messages=messages,
                stream=True,
                extra_body=self._build_extra_body(),
                **options,
            )
            for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                # Handle reasoning content (internal, not shown to user by default)
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    logger.debug("NVIDIA reasoning: %s", reasoning)
                # Handle normal content
                if delta.content is not None:
                    yield delta.content
        except Exception as exc:
            logger.error("NVIDIA stream failed: %s", exc)

    def is_available(self) -> bool:
        api_key = self.config.nvidia_api_key or os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            return False
        client = self._get_client()
        if client is None:
            return False
        try:
            # Quick health check
            client.models.list()
            return True
        except Exception:
            return False


def get_llm_provider(config: JarvisConfig) -> LLMProvider:
    """Factory function to get the configured LLM provider."""
    provider_name = config.llm_provider.lower()
    if provider_name == "nvidia":
        return NVIDIAProvider(config)
    return OllamaProvider(config)