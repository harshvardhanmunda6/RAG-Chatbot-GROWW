"""Groq provider (OpenAI-compatible chat completions)."""

from __future__ import annotations

from src.providers.base import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    api_key_env = "GROQ_API_KEY"
    default_base_url = "https://api.groq.com/openai/v1"
