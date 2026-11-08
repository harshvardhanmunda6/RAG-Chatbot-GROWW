"""Mistral provider (OpenAI-compatible chat completions)."""

from __future__ import annotations

from src.providers.base import OpenAICompatibleProvider


class MistralProvider(OpenAICompatibleProvider):
    name = "mistral"
    api_key_env = "MISTRAL_API_KEY"
    default_base_url = "https://api.mistral.ai/v1"
