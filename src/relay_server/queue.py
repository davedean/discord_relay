"""Queue persistence helpers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import Delivery, DeliveryState, DiscordMessage, WebhookNudge, WebhookNudgeState

LOG = logging.getLogger(__name__)


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

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        webhook_debounce_seconds: Dict[str, float] | None = None,
    ):
        self._session_factory = session_factory
        self._webhook_debounce_seconds = webhook_debounce_seconds or {}

    async def enqueue_message(
        self,
        backend_bot_id: str,
        payload: DiscordMessageRecord,
        dedupe_key: str,
    ) -> None:
        inserted = await asyncio.to_thread(
            self._enqueue_message_sync,
            backend_bot_id,
            payload,
            dedupe_key,
        )
        debounce = self._webhook_debounce_seconds.get(backend_bot_id)
        if inserted and debounce is not None:
            await asyncio.to_thread(
                self._schedule_webhook_nudge_sync,
                backend_bot_id,
                payload.discord_bot_id,
                dedupe_key,
                debounce,
            )

    def _enqueue_message_sync(
        self,
        backend_bot_id: str,
        payload: DiscordMessageRecord,
        dedupe_key: str,
    ) -> bool:
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
            return True
        except IntegrityError:
            session.rollback()
            # Duplicate message (dedupe key). Ignore.
            return False
        finally:
            session.close()

    def _schedule_webhook_nudge_sync(
        self,
        backend_bot_id: str,
        discord_bot_id: str,
        dedupe_key: str,
        debounce_seconds: float,
    ) -> None:
        now = datetime.now(timezone.utc)
        next_attempt_at = now + timedelta(seconds=max(0.0, debounce_seconds))
        session: Session = self._session_factory()
        try:
            nudge: WebhookNudge | None = (
                session.execute(
                    select(WebhookNudge).where(WebhookNudge.backend_bot_id == backend_bot_id)
                )
                .scalars()
                .first()
            )
            if not nudge:
                nudge = WebhookNudge(
                    backend_bot_id=backend_bot_id,
                    discord_bot_id=discord_bot_id,
                    last_dedupe_key=dedupe_key,
                    state=WebhookNudgeState.PENDING,
                    attempts=0,
                    next_attempt_at=next_attempt_at,
                    last_error=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(nudge)
            else:
                nudge.discord_bot_id = discord_bot_id
                nudge.last_dedupe_key = dedupe_key
                nudge.next_attempt_at = next_attempt_at
                nudge.updated_at = now
                if nudge.state == WebhookNudgeState.FAILED:
                    nudge.state = WebhookNudgeState.PENDING
                    nudge.attempts = 0
                    nudge.last_error = None
                if nudge.state == WebhookNudgeState.SENDING:
                    nudge.state = WebhookNudgeState.PENDING
            session.commit()
        except Exception:
            session.rollback()
            LOG.exception("Failed to schedule webhook nudge for backend_bot_id=%s", backend_bot_id)
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
