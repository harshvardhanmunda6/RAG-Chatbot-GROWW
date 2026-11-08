"""Provider factory for the configurable LLM adapters."""

from __future__ import annotations

import os

from src.config import Settings
from src.providers.anthropic import AnthropicProvider
from src.providers.base import (
    LLMProvider,
    MissingAPIKeyError,
    ModelValidationError,
    ProviderError,
)
from src.providers.gemini import GeminiProvider
from src.providers.groq import GroqProvider
from src.providers.mistral import MistralProvider
from src.providers.openai import OpenAIProvider

PROVIDERS = {
    "groq": GroqProvider,
    "openai": OpenAIProvider,
    "mistral": MistralProvider,
    "gemini": GeminiProvider,
    "anthropic": AnthropicProvider,
}

DEFAULT_KEY_ENVS = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def get_provider(
    name: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    client=None,
) -> LLMProvider:
    key = (name or "").strip().lower()
    try:
        provider_class = PROVIDERS[key]
    except KeyError as exc:
        available = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown LLM provider {name!r}. Choose one of: {available}") from exc
    return provider_class(api_key=api_key, base_url=base_url, client=client)


def resolve_provider(
    settings: Settings,
) -> tuple[LLMProvider | None, str | None, str | None]:
    """Return provider, model, and an error code without exposing secrets."""
    config = settings.data.get("llm", {})
    provider_env = str(config.get("provider_env", "LLM_PROVIDER"))
    name = (os.environ.get(provider_env) or config.get("provider") or "groq").strip().lower()
    model_env = str(config.get("model_env", "LLM_MODEL"))
    model = os.environ.get(model_env, "").strip()
    provider_config = (config.get("providers") or {}).get(name, {})
    key_env = str(
        provider_config.get("api_key_env")
        or (config.get("api_key_env") if name == "groq" else "")
        or DEFAULT_KEY_ENVS.get(name, "")
    )
    api_key = os.environ.get(key_env, "").strip()

    if not model:
        return None, None, "missing_model"
    if not api_key:
        return None, model, "missing_api_key"

    base_url = provider_config.get("base_url")
    if not base_url and name == "groq":
        base_url = config.get("base_url")
    try:
        provider = get_provider(name, api_key=api_key, base_url=base_url)
    except (ValueError, ProviderError) as exc:
        return None, model, f"provider_error:{exc}"
    return provider, model, None


__all__ = [
    "DEFAULT_KEY_ENVS",
    "LLMProvider",
    "MissingAPIKeyError",
    "ModelValidationError",
    "PROVIDERS",
    "ProviderError",
    "get_provider",
    "resolve_provider",
]
