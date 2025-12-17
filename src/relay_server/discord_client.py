"""Discord gateway integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

import discord

from .config import AppConfig, DiscordBotConfig
from .queue import DiscordMessageRecord, QueueService
from .routing import MessageContext, RoutingTable

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Destination:
    type: str  # "dm" or "channel"
    user_id: Optional[str] = None
    channel_id: Optional[str] = None


class RelayDiscordClient(discord.Client):
    """Discord client wired to enqueue messages into the queue."""

    def __init__(
        self,
        bot_config: DiscordBotConfig,
        routing: RoutingTable,
        queue_service: QueueService,
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.dm_messages = True
        intents.guilds = True
        super().__init__(intents=intents)
        self._bot_config = bot_config
        self._routing = routing
        self._queue_service = queue_service
        self._ready = asyncio.Event()

    async def on_ready(self) -> None:  # type: ignore[override]
        LOG.info("Discord bot '%s' connected as %s", self._bot_config.id, self.user)
        self._ready.set()

    async def close(self) -> None:  # type: ignore[override]
        await super().close()
        self._ready.clear()

    async def on_message(self, message: discord.Message) -> None:  # type: ignore[override]
        if message.author.bot:
            return
        me = self.user
        if me and message.author.id == me.id:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        channel_id = str(message.channel.id) if message.channel else None

        if not is_dm and channel_id not in self._bot_config.channel_allowlist:
            # Not ingesting from this channel.
            return

        guild_id = str(message.guild.id) if message.guild else None

        ctx = MessageContext(
            discord_bot_id=self._bot_config.id,
            author_id=str(message.author.id),
            channel_id=channel_id,
            guild_id=guild_id,
            is_dm=is_dm,
        )

        backend_id = self._routing.resolve_backend(ctx)
        if not backend_id:
            LOG.debug(
                "No route for message from user %s on bot %s",
                ctx.author_id,
                self._bot_config.id,
            )
            return

        dedupe_key = f"{self._bot_config.id}:{message.id}"
        payload = DiscordMessageRecord(
            discord_message_id=str(message.id),
            discord_bot_id=self._bot_config.id,
            author_id=str(message.author.id),
            author_name=message.author.name,
            channel_id=channel_id,
            guild_id=guild_id,
            is_dm=is_dm,
            content=message.content,
            timestamp=message.created_at or datetime.now(timezone.utc),
        )
        await self._queue_service.enqueue_message(backend_id, payload, dedupe_key)

    async def send_text(self, destination: Destination, content: str) -> discord.Message:
        await self._ready.wait()
        if destination.type == "dm" and destination.user_id:
            user = await self.fetch_user(int(destination.user_id))
            return await user.send(content)
        if destination.type == "channel" and destination.channel_id:
            channel = self.get_channel(int(destination.channel_id))
            if channel is None:
                channel = await self.fetch_channel(int(destination.channel_id))
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                return await channel.send(content)
        raise ValueError(f"Unsupported destination: {destination}")


class DiscordManager:
    """Starts/stops Discord clients defined in config."""

    def __init__(
        self,
        config: AppConfig,
        routing: RoutingTable,
        queue_service: QueueService,
    ):
        self._config = config
        self._routing = routing
        self._queue_service = queue_service
        self._clients: Dict[str, RelayDiscordClient] = {}
        self._tasks: Dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        for bot in self._config.discord_bots:
            if not bot.enabled:
                continue
            token = bot.resolved_token()
            client = RelayDiscordClient(bot, self._routing, self._queue_service)
            task = asyncio.create_task(self._run_client(bot.id, client, token))
            self._clients[bot.id] = client
            self._tasks[bot.id] = task

    async def _run_client(self, bot_id: str, client: RelayDiscordClient, token: str) -> None:
        LOG.info("Starting discord bot '%s'", bot_id)
        try:
            await client.start(token)
        except asyncio.CancelledError:
            LOG.info("Discord bot '%s' cancelled", bot_id)
            raise
        except Exception:
            LOG.exception("Discord bot '%s' stopped with error", bot_id)

    async def stop(self) -> None:
        for bot_id, client in self._clients.items():
            LOG.info("Stopping discord bot '%s'", bot_id)
            await client.close()
        for bot_id, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._clients.clear()
        self._tasks.clear()

    async def send_text(self, discord_bot_id: str, destination: Destination, content: str) -> str:
        client = self._clients.get(discord_bot_id)
        if not client:
            raise ValueError(f"Discord bot '{discord_bot_id}' is not running or enabled")
        message = await client.send_text(destination, content)
        return str(message.id)
