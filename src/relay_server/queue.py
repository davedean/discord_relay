"""Queue persistence helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import Delivery, DeliveryState, DiscordMessage


@dataclass(slots=True)
class DiscordMessageRecord:
    discord_message_id: str
    discord_bot_id: str
    author_id: str
    author_name: str
    channel_id: str | None
    guild_id: str | None
    is_dm: bool
    content: str
    timestamp: datetime


@dataclass(slots=True)
class DeliveryRecord:
    delivery_id: str
    backend_bot_id: str
    message: DiscordMessageRecord


class QueueService:
    """Encapsulates queue persistence with async-friendly helpers."""

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    async def enqueue_message(
        self,
        backend_bot_id: str,
        payload: DiscordMessageRecord,
        dedupe_key: str,
    ) -> None:
        await asyncio.to_thread(
            self._enqueue_message_sync,
            backend_bot_id,
            payload,
            dedupe_key,
        )

    def _enqueue_message_sync(
        self,
        backend_bot_id: str,
        payload: DiscordMessageRecord,
        dedupe_key: str,
    ) -> None:
        session: Session
        session = self._session_factory()
        try:
            message = DiscordMessage(
                discord_bot_id=payload.discord_bot_id,
                discord_message_id=payload.discord_message_id,
                author_id=payload.author_id,
                author_name=payload.author_name,
                channel_id=payload.channel_id,
                guild_id=payload.guild_id,
                is_dm=payload.is_dm,
                content=payload.content,
                timestamp=payload.timestamp,
                dedupe_key=dedupe_key,
            )
            delivery = Delivery(
                backend_bot_id=backend_bot_id,
                message=message,
                state=DeliveryState.PENDING,
            )
            session.add_all([message, delivery])
            session.commit()
        except IntegrityError:
            session.rollback()
            # Duplicate message (dedupe key). Ignore.
        finally:
            session.close()

    async def fetch_and_mark_delivered(
        self,
        backend_bot_id: str,
        limit: int,
    ) -> List[DeliveryRecord]:
        return await asyncio.to_thread(
            self._fetch_and_mark_delivered_sync,
            backend_bot_id,
            limit,
        )

    def _fetch_and_mark_delivered_sync(
        self,
        backend_bot_id: str,
        limit: int,
    ) -> List[DeliveryRecord]:
        session: Session = self._session_factory()
        try:
            deliveries: Sequence[Delivery] = (
                session.execute(
                    select(Delivery)
                    .where(
                        Delivery.backend_bot_id == backend_bot_id,
                        Delivery.state == DeliveryState.PENDING,
                    )
                    .order_by(Delivery.created_at)
                    .limit(limit)
                )
                .scalars()
                .all()
            )

            now = datetime.now(timezone.utc)
            result: List[DeliveryRecord] = []

            for delivery in deliveries:
                delivery.state = DeliveryState.DELIVERED
                delivery.delivered_at = now
                msg = delivery.message
                result.append(
                    DeliveryRecord(
                        delivery_id=delivery.id,
                        backend_bot_id=delivery.backend_bot_id,
                        message=DiscordMessageRecord(
                            discord_message_id=msg.discord_message_id,
                            discord_bot_id=msg.discord_bot_id,
                            author_id=msg.author_id,
                            author_name=msg.author_name,
                            channel_id=msg.channel_id,
                            guild_id=msg.guild_id,
                            is_dm=msg.is_dm,
                            content=msg.content,
                            timestamp=msg.timestamp,
                        ),
                    )
                )

            session.commit()
            return result
        finally:
            session.close()
