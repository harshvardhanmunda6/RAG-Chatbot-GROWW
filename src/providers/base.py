"""Shared provider contract and HTTP helpers.

Providers are deliberately small adapters. They never build prompts, validate
answers, or decide whether a citation is safe; those jobs belong to the
generator, validator, and citation resolver.
"""

from __future__ import annotations

import os
import time
from typing import Protocol

import httpx


class ProviderError(RuntimeError):
    """Raised when a provider call fails after one retry."""


class MissingAPIKeyError(ProviderError):
    """Raised when the configured API key environment variable is empty."""


class ModelValidationError(ProviderError):
    """Raised when the configured model is not available at the provider."""


class LLMProvider(Protocol):
    name: str
    api_key_env: str
    default_base_url: str

    def validate_model(self, model: str) -> None: ...

    def generate(
        self,
        system: str,
        user: str,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> str: ...


class HTTPProvider:
    """Base class with one retry on 429/5xx and secret-safe errors."""

    name = "base"
    api_key_env = ""
    default_base_url = ""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.environ.get(self.api_key_env, "")).strip()
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._client = client

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise MissingAPIKeyError(f"{self.api_key_env} is not set")
        return {"Authorization": f"Bearer {self.api_key}"}

    def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict | None = None,
        timeout_s: float = 30.0,
        allow_status: set[int] | None = None,
    ) -> httpx.Response:
        transient = {429, 500, 502, 503, 504}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                if self._client is not None:
                    response = self._client.request(
                        method, url, headers=headers, json=json, timeout=timeout_s
                    )
                else:
                    with httpx.Client(timeout=timeout_s) as client:
                        response = client.request(method, url, headers=headers, json=json)
                if response.status_code in transient and attempt == 0:
                    last_error = ProviderError(
                        f"{self.name} HTTP {response.status_code}"
                    )
                    time.sleep(0.5)
                    continue
                if response.status_code >= 400 and not (
                    allow_status and response.status_code in allow_status
                ):
                    raise ProviderError(
                        f"{self.name} HTTP {response.status_code}: "
                        f"{response.text[:240]}"
                    )
                return response
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise ProviderError(f"{self.name} request failed: {exc}") from exc
        raise ProviderError(f"{self.name} request failed: {last_error}")

    def _payload_error(self, detail: str) -> ProviderError:
        return ProviderError(f"{self.name} response could not be parsed: {detail}")


class OpenAICompatibleProvider(HTTPProvider):
    """Common chat-completions adapter for Groq, OpenAI, and Mistral."""

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def validate_model(self, model: str) -> None:
        if not self.api_key:
            raise MissingAPIKeyError(f"{self.api_key_env} is not set")
        response = self._request(
            "GET",
            f"{self.base_url}/models",
            headers=self._headers(),
            timeout_s=15.0,
            allow_status={404, 405},
        )
        if response.status_code in {404, 405}:
            # Some compatible gateways do not expose a model list. The first
            # generation call remains the practical validation.
            return
        try:
            payload = response.json()
        except ValueError as exc:
            raise self._payload_error("models endpoint did not return JSON") from exc
        ids = {
            str(item.get("id"))
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        }
        if ids and model not in ids:
            raise ModelValidationError(
                f"model {model!r} is not available from {self.name}"
            )

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
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout_s=timeout_s,
        )
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise self._payload_error("missing choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise self._payload_error("empty message content")
        return content
