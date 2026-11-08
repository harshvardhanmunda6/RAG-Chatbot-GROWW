"""Google Gemini provider (Generative Language API)."""

from __future__ import annotations

import httpx

from src.providers.base import (
    HTTPProvider,
    MissingAPIKeyError,
    ModelValidationError,
    ProviderError,
)


class GeminiProvider(HTTPProvider):
    name = "gemini"
    api_key_env = "GEMINI_API_KEY"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise MissingAPIKeyError(f"{self.api_key_env} is not set")
        return {"Content-Type": "application/json"}

    def validate_model(self, model: str) -> None:
        if not self.api_key:
            raise MissingAPIKeyError(f"{self.api_key_env} is not set")
        response = self._request(
            "GET",
            f"{self.base_url}/models?key={self.api_key}",
            headers={"Content-Type": "application/json"},
            timeout_s=15.0,
            allow_status={404, 405},
        )
        if response.status_code in {404, 405}:
            return
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("gemini models endpoint did not return JSON") from exc
        names = {
            str(item.get("name", "")).removeprefix("models/")
            for item in payload.get("models", [])
            if isinstance(item, dict)
        }
        if names and model.removeprefix("models/") not in names:
            raise ModelValidationError(f"model {model!r} is not available from gemini")

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
            f"{self.base_url}/models/{model}:generateContent?key={self.api_key}",
            headers=self._headers(),
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                },
            },
            timeout_s=timeout_s,
        )
        try:
            payload = response.json()
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(str(part.get("text", "")) for part in parts)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("gemini response could not be parsed") from exc
        if not text.strip():
            raise ProviderError("gemini returned an empty response")
        return text


__all__ = ["GeminiProvider", "httpx"]
