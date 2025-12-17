"""YAML-backed configuration loading and validation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class ConfigError(Exception):
    """Raised when configuration fails validation."""


class ServerConfig(BaseModel):
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    base_url: Optional[str] = None
    log_level: str = "INFO"


class StorageConfig(BaseModel):
    database_url: str = "sqlite:///./relay.db"


class DiscordBotConfig(BaseModel):
    id: str
    name: str
    token: Optional[str] = None
    token_env: Optional[str] = None
    enabled: bool = True
    channel_allowlist: List[str] = Field(default_factory=list)

    @field_validator("channel_allowlist", mode="before")
    @classmethod
    def _ensure_str_list(cls, value: Optional[List[int | str]]) -> List[str]:
        if value is None:
            return []
        return [str(v) for v in value]

    def resolved_token(self) -> str:
        if self.token:
            return self.token
        if self.token_env:
            env_value = os.getenv(self.token_env)
            if env_value:
                return env_value
        raise ConfigError(
            f"Discord bot '{self.id}' is missing a token. "
            "Provide 'token' or set the referenced 'token_env'."
        )


class BackendBotConfig(BaseModel):
    id: str
    name: str
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None
    enabled: bool = True

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            env_value = os.getenv(self.api_key_env)
            if env_value:
                return env_value
        raise ConfigError(
            f"Backend bot '{self.id}' is missing an API key. "
            "Provide 'api_key' or set the referenced 'api_key_env'."
        )


class RouteScope(str):
    DM_USER = "dm_user"
    CHANNEL = "channel"
    GUILD = "guild"


class RouteConfig(BaseModel):
    discord_bot_id: str
    scope_type: str
    scope_id: str
    backend_bot_id: str

    @field_validator("scope_type")
    @classmethod
    def _validate_scope_type(cls, value: str) -> str:
        allowed = {RouteScope.DM_USER, RouteScope.CHANNEL, RouteScope.GUILD}
        if value not in allowed:
            raise ValueError(f"scope_type must be one of {sorted(allowed)}")
        return value

    @field_validator("scope_id")
    @classmethod
    def _stringify_scope_id(cls, value: str | int) -> str:
        return str(value)


class RoutingConfig(BaseModel):
    mode: str = "first_match"
    precedence: List[str] = Field(
        default_factory=lambda: [
            RouteScope.DM_USER,
            RouteScope.CHANNEL,
            RouteScope.GUILD,
            "default",
        ]
    )
    defaults: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_precedence(self) -> "RoutingConfig":
        seen = set()
        for scope in self.precedence:
            if scope in seen:
                raise ValueError("Routing precedence cannot contain duplicates")
            seen.add(scope)
        return self


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    discord_bots: List[DiscordBotConfig]
    backend_bots: List[BackendBotConfig]
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    routes: List[RouteConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_uniqueness(self) -> "AppConfig":
        def ensure_unique(items: List[BaseModel], field_name: str) -> None:
            seen = set()
            for item in items:
                value = getattr(item, field_name)
                if value in seen:
                    raise ConfigError(f"Duplicate {field_name} detected: '{value}'")
                seen.add(value)

        ensure_unique(self.discord_bots, "id")
        ensure_unique(self.backend_bots, "id")
        return self


@dataclass(frozen=True)
class LoadedConfig:
    path: Path
    data: AppConfig


def load_config(path: Optional[str]) -> LoadedConfig:
    """Load and validate the YAML config file."""
    candidate = Path(path or os.getenv("RELAY_CONFIG", "config.yaml"))
    if not candidate.exists():
        raise ConfigError(f"Config file not found: {candidate}")
    with candidate.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    try:
        config = AppConfig.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - surfacing details
        raise ConfigError(f"Invalid config: {exc}") from exc
    return LoadedConfig(path=candidate, data=config)
