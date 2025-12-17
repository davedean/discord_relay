"""FastAPI app for the Discord relay."""

from __future__ import annotations

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
from .queue import DeliveryRecord, DiscordMessageRecord, QueueService
from .routing import MessageContext, RoutingTable
from .schemas import (
    PendingMessage,
    PendingMessagesResponse,
    SendMessageRequest,
    SendMessageResponse,
)

LOG = logging.getLogger("relay")


@dataclass
class RelayState:
    loaded_config: LoadedConfig
    queue_service: QueueService
    routing: RoutingTable
    auth_service: AuthService
    discord_manager: DiscordManager
    discord_bots: dict[str, DiscordBotConfig]


def create_state(config_path: Optional[str] = None) -> RelayState:
    loaded = load_config(config_path)
    session_factory = create_session_factory(loaded.data.storage.database_url)
    queue_service = QueueService(session_factory)
    routing = RoutingTable(loaded.data)
    auth_service = AuthService(loaded.data)
    discord_manager = DiscordManager(loaded.data, routing, queue_service)
    discord_bots = {bot.id: bot for bot in loaded.data.discord_bots if bot.enabled}

    return RelayState(
        loaded_config=loaded,
        queue_service=queue_service,
        routing=routing,
        auth_service=auth_service,
        discord_manager=discord_manager,
        discord_bots=discord_bots,
    )


def create_app(config_path: Optional[str] = None, *, start_discord: bool = True) -> FastAPI:
    state = create_state(config_path)

    app = FastAPI(
        title="Discord Messages Relay",
        version="0.1.0",
    )
    app.state.relay_state = state

    @app.on_event("startup")
    async def _startup() -> None:
        logging.basicConfig(
            level=state.loaded_config.data.server.log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        if start_discord:
            await state.discord_manager.start()
        else:
            LOG.info("Skipping Discord startup (start_discord=False)")
        LOG.info(
            "Relay server started using config %s",
            state.loaded_config.path,
        )

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if start_discord:
            await state.discord_manager.stop()
        LOG.info("Relay server stopped")

    def get_state(request: Request) -> RelayState:
        return request.app.state.relay_state

    async def get_backend_identity(
        request: Request,
        authorization: Annotated[Optional[str], Header(alias="Authorization")] = None,
    ) -> BackendIdentity:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
        api_key = authorization.split(" ", 1)[1].strip()
        backend = request.app.state.relay_state.auth_service.authenticate(api_key)
        if not backend:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
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
        deliveries = await relay_state.queue_service.fetch_and_mark_delivered(backend.id, limit)
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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return SendMessageResponse(
            discord_message_id=message_id,
            channel_id=payload.destination.channel_id,
        )

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


def _build_default_app() -> FastAPI:
    config_path = os.getenv("RELAY_CONFIG")
    try:
        return create_app(config_path)
    except ConfigError as exc:
        LOG.warning(
            "Relay config missing at %s (set RELAY_CONFIG). Using placeholder app.",
            config_path or "config.yaml",
        )
        placeholder = FastAPI(title="Discord Relay (config error)")

        @placeholder.on_event("startup")
        async def _fail_startup() -> None:
            raise RuntimeError(
                "Relay configuration missing. Set RELAY_CONFIG or provide config.yaml "
                "before importing relay_server.main:app."
            )

        return placeholder


app = _build_default_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "relay_server.main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
    )
