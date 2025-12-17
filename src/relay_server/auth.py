"""Simple API key auth for backend bots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .config import AppConfig, ConfigError


@dataclass(frozen=True)
class BackendIdentity:
    id: str
    name: str


class AuthService:
    """Resolves backend bots from API keys."""

    def __init__(self, config: AppConfig):
        self._keys: Dict[str, BackendIdentity] = {}
        for bot in config.backend_bots:
            if not bot.enabled:
                continue
            api_key = bot.resolved_api_key()
            if api_key in self._keys:
                raise ConfigError("Duplicate backend API key detected. Keys must be unique.")
            self._keys[api_key] = BackendIdentity(id=bot.id, name=bot.name)

    def authenticate(self, api_key: str) -> Optional[BackendIdentity]:
        return self._keys.get(api_key)
