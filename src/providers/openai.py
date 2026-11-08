"""OpenAI provider (chat completions)."""

from __future__ import annotations

from src.providers.base import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    api_key_env = "OPENAI_API_KEY"
    default_base_url = "https://api.openai.com/v1"
