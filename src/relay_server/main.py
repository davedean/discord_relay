"""FastAPI app for the Discord relay."""

from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status

from .auth import AuthService, BackendIdentity
from .config import AppConfig, ConfigError, DiscordBotConfig, LoadedConfig, load_config
from .discord_client import Destination as DiscordDestination
from .discord_client import DiscordManager
from .models import create_session_factory
from .queue import (
    DeliveryRecord,
    DiscordMessageRecord,
    LeasedDeliveryRecord,
    QueueService,
)
from .routing import MessageContext, RoutingTable
from .schemas import (
    AckRequest,
    DiscordMessagePayload,
    LeaseMessagesRequest,
    LeaseMessagesResponse,
    LeasedMessage,
    NackRequest,
    PendingMessage,
    PendingMessagesResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from .webhooks import WebhookDispatcher

LOG = logging.getLogger("relay")


@dataclass
class RelayState:
    loaded_config: LoadedConfig
    queue_service: QueueService
    routing: RoutingTable
    auth_service: AuthService
    discord_manager: DiscordManager
    discord_bots: dict[str, DiscordBotConfig]
    webhook_dispatcher: WebhookDispatcher | None


def create_state(config_path: Optional[str] = None) -> RelayState:
    loaded = load_config(config_path)
    session_factory = create_session_factory(loaded.data.storage.database_url)
    webhook_debounce_seconds = {
        bot.id: bot.webhook.send_debounce_seconds
        for bot in loaded.data.backend_bots
        if bot.enabled and bot.webhook is not None
    }
    queue_service = QueueService(
        session_factory, webhook_debounce_seconds=webhook_debounce_seconds
    )
    routing = RoutingTable(loaded.data)
    auth_service = AuthService(loaded.data)
    discord_manager = DiscordManager(loaded.data, routing, queue_service)
    discord_bots = {bot.id: bot for bot in loaded.data.discord_bots if bot.enabled}
    backend_webhooks = {
        bot.id: bot.webhook
        for bot in loaded.data.backend_bots
        if bot.enabled and bot.webhook is not None
    }
    webhook_dispatcher = (
        WebhookDispatcher(session_factory, backend_webhooks)
        if backend_webhooks
        else None
    )

    return RelayState(
        loaded_config=loaded,
        queue_service=queue_service,
        routing=routing,
        auth_service=auth_service,
        discord_manager=discord_manager,
        discord_bots=discord_bots,
        webhook_dispatcher=webhook_dispatcher,
    )


def create_app(
    config_path: Optional[str] = None,
    *,
    start_discord: bool = True,
    start_webhooks: bool = True,
) -> FastAPI:
    state = create_state(config_path)

    async def reap_expired_leases():
        """Periodic task to reap expired leases."""
        while True:
            try:
                reaped_count = await state.queue_service.reap_expired_leases()
                if reaped_count > 0:
                    LOG.info("Reaped %d expired leases", reaped_count)
            except Exception as exc:
                LOG.exception("Error reaping expired leases: %s", exc)
            await asyncio.sleep(60)  # Run every 60 seconds

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logging.basicConfig(
            level=state.loaded_config.data.server.log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        if start_discord:
            await state.discord_manager.start()
        else:
            LOG.info("Skipping Discord startup (start_discord=False)")
        if start_webhooks and state.webhook_dispatcher:
            await state.webhook_dispatcher.start()

        # Start lease reaper task
        reaper_task = asyncio.create_task(reap_expired_leases())

        LOG.info(
            "Relay server started using config %s",
            state.loaded_config.path,
        )
        try:
            yield
        finally:
            # Cancel lease reaper task
            reaper_task.cancel()
            try:
                await reaper_task
            except asyncio.CancelledError:
                pass

            if start_discord:
                await state.discord_manager.stop()
            if start_webhooks and state.webhook_dispatcher:
                await state.webhook_dispatcher.stop()
            LOG.info("Relay server stopped")

    app = FastAPI(
        title="Discord Messages Relay",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.relay_state = state

    def get_state(request: Request) -> RelayState:
        return request.app.state.relay_state

    async def get_backend_identity(
        request: Request,
        authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
    ) -> BackendIdentity:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
            )
        api_key = authorization.split(" ", 1)[1].strip()
        backend = request.app.state.relay_state.auth_service.authenticate(api_key)
        if not backend:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
            )
        return backend

    @app.get("/v1/health")
    async def health(request: Request) -> dict[str, str]:
        relay_state: RelayState = request.app.state.relay_state
        return {
            "status": "ok",
            "config_path": str(relay_state.loaded_config.path),
        }

    @app.get(
        "/v1/messages/pending",
        response_model=PendingMessagesResponse,
    )
    async def get_pending_messages(
        limit: Annotated[int, Query(gt=0, le=100)] = 50,
        backend: BackendIdentity = Depends(get_backend_identity),
        relay_state: RelayState = Depends(get_state),
    ) -> PendingMessagesResponse:
        deliveries = await relay_state.queue_service.fetch_and_mark_delivered(
            backend.id, limit
        )
        response = PendingMessagesResponse(
            messages=[_delivery_to_schema(record) for record in deliveries]
        )
        return response

    @app.post(
        "/v1/messages/send",
        response_model=SendMessageResponse,
    )
    async def send_message(
        payload: SendMessageRequest,
        backend: BackendIdentity = Depends(get_backend_identity),  # noqa: ARG001 - reserved for future auditing
        relay_state: RelayState = Depends(get_state),
    ) -> SendMessageResponse:
        bot_config = relay_state.discord_bots.get(payload.discord_bot_id)
        if not bot_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Discord bot '{payload.discord_bot_id}' not found or not enabled",
            )
        destination = DiscordDestination(
            type=payload.destination.type,
            user_id=payload.destination.user_id,
            channel_id=payload.destination.channel_id,
        )
        try:
            message_id = await relay_state.discord_manager.send_text(
                payload.discord_bot_id,
                destination,
                payload.content,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return SendMessageResponse(
            discord_message_id=message_id,
            channel_id=payload.destination.channel_id,
        )

    @app.post(
        "/v1/messages/lease",
        response_model=LeaseMessagesResponse,
    )
    async def lease_messages(
        payload: LeaseMessagesRequest,
        backend: BackendIdentity = Depends(get_backend_identity),
        relay_state: RelayState = Depends(get_state),
    ) -> LeaseMessagesResponse:
        (
            leased_records,
            conversation_history,
        ) = await relay_state.queue_service.lease_messages(
            backend.id,
            payload.limit,
            payload.lease_seconds,
            payload.include_conversation_history,
            payload.conversation_history_limit,
        )

        response = LeaseMessagesResponse(
            messages=[_leased_delivery_to_schema(record) for record in leased_records],
        )

        if payload.include_conversation_history and conversation_history:
            response.conversation_history = [
                _message_record_to_schema(record) for record in conversation_history
            ]

        return response

    @app.post("/v1/messages/ack")
    async def acknowledge_messages(
        payload: AckRequest,
        backend: BackendIdentity = Depends(get_backend_identity),
        relay_state: RelayState = Depends(get_state),
    ) -> dict[str, int]:
        count = await relay_state.queue_service.acknowledge_deliveries(
            backend.id, payload.delivery_ids, payload.lease_id
        )
        return {"acknowledged_count": count}

    @app.post("/v1/messages/nack")
    async def negative_acknowledge_messages(
        payload: NackRequest,
        backend: BackendIdentity = Depends(get_backend_identity),
        relay_state: RelayState = Depends(get_state),
    ) -> dict[str, int]:
        count = await relay_state.queue_service.negative_acknowledge_deliveries(
            backend.id, payload.delivery_ids, payload.lease_id, payload.reason
        )
        return {"nacked_count": count}

    return app


def _delivery_to_schema(record: DeliveryRecord) -> PendingMessage:
    msg: DiscordMessageRecord = record.message
    source = {
        "is_dm": msg.is_dm,
        "guild_id": msg.guild_id,
        "channel_id": msg.channel_id,
        "author_id": msg.author_id,
        "author_name": msg.author_name,
    }
    return PendingMessage(
        delivery_id=record.delivery_id,
        discord_bot_id=msg.discord_bot_id,
        discord_message={
            "discord_message_id": msg.discord_message_id,
            "discord_bot_id": msg.discord_bot_id,
            "timestamp": msg.timestamp,
            "content": msg.content,
            "source": source,
        },
    )


def _leased_delivery_to_schema(record: LeasedDeliveryRecord) -> LeasedMessage:
    msg: DiscordMessageRecord = record.message
    source = {
        "is_dm": msg.is_dm,
        "guild_id": msg.guild_id,
        "channel_id": msg.channel_id,
        "author_id": msg.author_id,
        "author_name": msg.author_name,
    }
    return LeasedMessage(
        delivery_id=record.delivery_id,
        lease_id=record.lease_id,
        discord_bot_id=msg.discord_bot_id,
        discord_message={
            "discord_message_id": msg.discord_message_id,
            "discord_bot_id": msg.discord_bot_id,
            "timestamp": msg.timestamp,
            "content": msg.content,
            "source": source,
        },
        lease_expires_at=record.lease_expires_at,
    )


def _message_record_to_schema(record: DiscordMessageRecord) -> DiscordMessagePayload:
    source = {
        "is_dm": record.is_dm,
        "guild_id": record.guild_id,
        "channel_id": record.channel_id,
        "author_id": record.author_id,
        "author_name": record.author_name,
    }
    return DiscordMessagePayload(
        discord_message_id=record.discord_message_id,
        discord_bot_id=record.discord_bot_id,
        timestamp=record.timestamp,
        content=record.content,
        source=source,
    )


def _build_default_app() -> FastAPI:
    config_path = os.getenv("RELAY_CONFIG")
    try:
        return create_app(config_path)
    except ConfigError as exc:
        LOG.warning(
            "Relay config missing at %s (set RELAY_CONFIG). Using placeholder app.",
            config_path or "config.yaml",
        )

        @asynccontextmanager
        async def fail_lifespan(_: FastAPI):
            raise RuntimeError(
                "Relay configuration missing. Set RELAY_CONFIG or provide config.yaml "
                "before importing relay_server.main:app."
            )
            yield  # pragma: no cover

        placeholder = FastAPI(
            title="Discord Relay (config error)", lifespan=fail_lifespan
        )

        return placeholder


app = None  # Lazy initialization - only create when explicitly needed


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "relay_server.main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
    )
