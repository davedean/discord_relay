"""Routing utilities for assigning deliveries to backend bots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import AppConfig, ConfigError, RouteScope


@dataclass(frozen=True)
class MessageContext:
    discord_bot_id: str
    author_id: str
    channel_id: str | None
    guild_id: str | None
    is_dm: bool


class RoutingTable:
    """Resolves backend bots for incoming Discord messages."""

    def __init__(self, config: AppConfig):
        self._precedence: List[str] = config.routing.precedence
        self._defaults: Dict[str, str] = config.routing.defaults
        self._dm_routes: Dict[str, Dict[str, str]] = {}
        self._channel_routes: Dict[str, Dict[str, str]] = {}
        self._guild_routes: Dict[str, Dict[str, str]] = {}

        valid_discord_ids = {bot.id for bot in config.discord_bots}
        valid_backend_ids = {bot.id for bot in config.backend_bots}

        for route in config.routes:
            if route.discord_bot_id not in valid_discord_ids:
                raise ConfigError(
                    f"Route references unknown discord_bot_id '{route.discord_bot_id}'"
                )
            if route.backend_bot_id not in valid_backend_ids:
                raise ConfigError(
                    f"Route references unknown backend_bot_id '{route.backend_bot_id}'"
                )

            if route.scope_type == RouteScope.DM_USER:
                table = self._dm_routes.setdefault(route.discord_bot_id, {})
            elif route.scope_type == RouteScope.CHANNEL:
                table = self._channel_routes.setdefault(route.discord_bot_id, {})
            elif route.scope_type == RouteScope.GUILD:
                table = self._guild_routes.setdefault(route.discord_bot_id, {})
            else:
                raise ConfigError(f"Unsupported scope type: {route.scope_type}")

            if route.scope_id in table:
                raise ConfigError(
                    f"Multiple routes defined for {route.scope_type} '{route.scope_id}' "
                    f"under discord bot '{route.discord_bot_id}'"
                )
            table[route.scope_id] = route.backend_bot_id

        # Validate defaults
        for bot_id in self._defaults:
            if bot_id not in valid_discord_ids:
                raise ConfigError(f"Default route references unknown discord bot '{bot_id}'")

    def resolve_backend(self, ctx: MessageContext) -> Optional[str]:
        """Return the backend bot id for the given context, or None."""
        for scope in self._precedence:
            if scope == RouteScope.DM_USER and ctx.is_dm:
                backend = self._dm_routes.get(ctx.discord_bot_id, {}).get(ctx.author_id)
                if backend:
                    return backend
            elif scope == RouteScope.CHANNEL and not ctx.is_dm and ctx.channel_id:
                backend = self._channel_routes.get(ctx.discord_bot_id, {}).get(ctx.channel_id)
                if backend:
                    return backend
            elif scope == RouteScope.GUILD and ctx.guild_id:
                backend = self._guild_routes.get(ctx.discord_bot_id, {}).get(ctx.guild_id)
                if backend:
                    return backend
            elif scope == "default":
                backend = self._defaults.get(ctx.discord_bot_id)
                if backend:
                    return backend
        return None
