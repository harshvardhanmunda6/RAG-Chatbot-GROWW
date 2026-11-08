"""Configuration loading for the data-loading phase."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:  # optional at import time; the dependency is in requirements.txt
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - only reached in a minimal install
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    root: Path
    data: dict[str, Any]

    def path(self, key: str) -> Path:
        value = self.data["paths"][key]
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    @property
    def sources(self) -> dict[str, Any]:
        return self.data["sources"]

    @property
    def config_hash(self) -> str:
        payload = {
            "paths": self.data.get("paths"),
            "sources": self.data.get("sources"),
            "chunking": self.data.get("chunking"),
            "embedding": self.data.get("embedding"),
            "vector_db": self.data.get("vector_db"),
            "retrieval": self.data.get("retrieval"),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_settings(config_path: str | Path | None = None) -> Settings:
    path = Path(config_path) if config_path else PROJECT_ROOT / "config.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if "paths" not in data or "sources" not in data:
        raise ValueError("config.yaml must define paths and sources")

    return Settings(root=PROJECT_ROOT, data=data)
