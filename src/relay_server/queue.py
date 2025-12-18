"""Queue persistence helpers."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    Delivery,
    DeliveryState,
    DiscordMessage,
    WebhookNudge,
    WebhookNudgeState,
)

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


@dataclass(slots=True)
class LeasedDeliveryRecord:
    delivery_id: str
    lease_id: str
    backend_bot_id: str
    message: DiscordMessageRecord
    lease_expires_at: datetime


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
                    select(WebhookNudge).where(
                        WebhookNudge.backend_bot_id == backend_bot_id
                    )
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
            LOG.exception(
                "Failed to schedule webhook nudge for backend_bot_id=%s", backend_bot_id
            )
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

    async def lease_messages(
        self,
        backend_bot_id: str,
        limit: int,
        lease_seconds: int,
        include_conversation_history: bool,
        conversation_history_limit: int,
    ) -> tuple[List[LeasedDeliveryRecord], List[DiscordMessageRecord]]:
        return await asyncio.to_thread(
            self._lease_messages_sync,
            backend_bot_id,
            limit,
            lease_seconds,
            include_conversation_history,
            conversation_history_limit,
        )

    def _lease_messages_sync(
        self,
        backend_bot_id: str,
        limit: int,
        lease_seconds: int,
        include_conversation_history: bool,
        conversation_history_limit: int,
    ) -> tuple[List[LeasedDeliveryRecord], List[DiscordMessageRecord]]:
        session: Session = self._session_factory()
        try:
            now = datetime.now(timezone.utc)
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            lease_id = str(uuid.uuid4())

            # Lease pending deliveries
            deliveries: Sequence[Delivery] = (
                session.execute(
                    select(Delivery)
                    .where(
                        Delivery.backend_bot_id == backend_bot_id,
                        Delivery.state == DeliveryState.PENDING,
                    )
                    .order_by(Delivery.created_at)
                    .limit(limit)
                    .with_for_update()
                )
                .scalars()
                .all()
            )

            leased_records: List[LeasedDeliveryRecord] = []
            conversation_history: List[DiscordMessageRecord] = []

            for delivery in deliveries:
                delivery.state = DeliveryState.LEASED
                delivery.lease_id = lease_id
                delivery.lease_expires_at = lease_expires_at
                delivery.attempts += 1

                msg = delivery.message
                leased_records.append(
                    LeasedDeliveryRecord(
                        delivery_id=delivery.id,
                        lease_id=lease_id,
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
                        lease_expires_at=lease_expires_at,
                    )
                )

            # Get conversation history if requested
            if include_conversation_history and leased_records:
                # Use the first message's channel context for history
                first_msg = leased_records[0].message
                channel_id = first_msg.channel_id

                if channel_id:
                    # Get recent messages in the same channel from all Discord messages,
                    # ordered by timestamp (most recent first, then reverse for chronological)
                    history_messages = (
                        session.execute(
                            select(DiscordMessage)
                            .where(
                                DiscordMessage.channel_id == channel_id,
                                DiscordMessage.timestamp <= first_msg.timestamp,
                            )
                            .order_by(DiscordMessage.timestamp.desc())
                            .limit(conversation_history_limit)
                        )
                        .scalars()
                        .all()
                    )

                    # Convert to DiscordMessageRecords and reverse to chronological order
                    for msg in reversed(history_messages):
                        conversation_history.append(
                            DiscordMessageRecord(
                                discord_message_id=msg.discord_message_id,
                                discord_bot_id=msg.discord_bot_id,
                                author_id=msg.author_id,
                                author_name=msg.author_name,
                                channel_id=msg.channel_id,
                                guild_id=msg.guild_id,
                                is_dm=msg.is_dm,
                                content=msg.content,
                                timestamp=msg.timestamp,
                            )
                        )

            session.commit()
            return leased_records, conversation_history
        finally:
            session.close()

    async def acknowledge_deliveries(
        self,
        backend_bot_id: str,
        delivery_ids: List[str],
        lease_id: str,
    ) -> int:
        return await asyncio.to_thread(
            self._acknowledge_deliveries_sync,
            backend_bot_id,
            delivery_ids,
            lease_id,
        )

    def _acknowledge_deliveries_sync(
        self,
        backend_bot_id: str,
        delivery_ids: List[str],
        lease_id: str,
    ) -> int:
        session: Session = self._session_factory()
        try:
            now = datetime.now(timezone.utc)
            result = session.execute(
                select(Delivery).where(
                    Delivery.backend_bot_id == backend_bot_id,
                    Delivery.id.in_(delivery_ids),
                    Delivery.state == DeliveryState.LEASED,
                    Delivery.lease_id == lease_id,
                )
            )
            deliveries = result.scalars().all()

            for delivery in deliveries:
                delivery.state = DeliveryState.DELIVERED
                delivery.delivered_at = now

            session.commit()
            return len(deliveries)
        finally:
            session.close()

    async def negative_acknowledge_deliveries(
        self,
        backend_bot_id: str,
        delivery_ids: List[str],
        lease_id: str,
        reason: str | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self._negative_acknowledge_deliveries_sync,
            backend_bot_id,
            delivery_ids,
            lease_id,
            reason,
        )

    def _negative_acknowledge_deliveries_sync(
        self,
        backend_bot_id: str,
        delivery_ids: List[str],
        lease_id: str,
        reason: str | None = None,
    ) -> int:
        session: Session = self._session_factory()
        try:
            result = session.execute(
                select(Delivery).where(
                    Delivery.backend_bot_id == backend_bot_id,
                    Delivery.id.in_(delivery_ids),
                    Delivery.state == DeliveryState.LEASED,
                    Delivery.lease_id == lease_id,
                )
            )
            deliveries = result.scalars().all()

            for delivery in deliveries:
                delivery.state = DeliveryState.PENDING
                delivery.lease_id = None
                delivery.lease_expires_at = None
                delivery.last_error = reason

            session.commit()
            return len(deliveries)
        finally:
            session.close()

    async def reap_expired_leases(self) -> int:
        """Release expired leases back to pending state. Returns number of leases reaped."""
        return await asyncio.to_thread(self._reap_expired_leases_sync)

    def _reap_expired_leases_sync(self) -> int:
        session: Session = self._session_factory()
        try:
            now = datetime.now(timezone.utc)
            result = session.execute(
                select(Delivery).where(
                    Delivery.state == DeliveryState.LEASED,
                    Delivery.lease_expires_at < now,
                )
            )
            deliveries = result.scalars().all()

            for delivery in deliveries:
                delivery.state = DeliveryState.PENDING
                delivery.lease_id = None
                delivery.lease_expires_at = None
                delivery.last_error = "Lease expired"

            session.commit()
            return len(deliveries)
        finally:
            session.close()
