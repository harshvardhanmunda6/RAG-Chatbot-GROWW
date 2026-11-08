"""Anthropic provider (Messages API)."""

from __future__ import annotations

from src.providers.base import (
    HTTPProvider,
    MissingAPIKeyError,
    ModelValidationError,
    ProviderError,
)


class AnthropicProvider(HTTPProvider):
    name = "anthropic"
    api_key_env = "ANTHROPIC_API_KEY"
    default_base_url = "https://api.anthropic.com"
    api_version = "2023-06-01"

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise MissingAPIKeyError(f"{self.api_key_env} is not set")
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "Content-Type": "application/json",
        }

    def validate_model(self, model: str) -> None:
        if not self.api_key:
            raise MissingAPIKeyError(f"{self.api_key_env} is not set")
        response = self._request(
            "GET",
            f"{self.base_url}/v1/models",
            headers=self._headers(),
            timeout_s=15.0,
            allow_status={404, 405},
        )
        if response.status_code in {404, 405}:
            return
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("anthropic models endpoint did not return JSON") from exc
        ids = {
            str(item.get("id"))
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        }
        if ids and model not in ids:
            raise ModelValidationError(f"model {model!r} is not available from anthropic")

    def generate(
        self,
        system: str,
        user: str,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> str:
        response = self._request(
            "POST",
            f"{self.base_url}/v1/messages",
            headers=self._headers(),
            json={
                "model": model,
                "system": system,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": user}],
            },
            timeout_s=timeout_s,
        )
        try:
            payload = response.json()
            blocks = payload["content"]
            text = "".join(
                str(block.get("text", ""))
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError("anthropic response could not be parsed") from exc
        if not text.strip():
            raise ProviderError("anthropic returned an empty response")
        return text
