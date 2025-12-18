"""Webhook nudge delivery (persisted outbox + background dispatcher)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .config import WebhookConfig
from .models import WebhookNudge, WebhookNudgeState

LOG = logging.getLogger(__name__)


def compute_webhook_signature(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), digestmod=sha256)
    mac.update(timestamp.encode("utf-8"))
    mac.update(b".")
    mac.update(body)
    return mac.hexdigest()


@dataclass(frozen=True, slots=True)
class ClaimedNudge:
    id: str
    backend_bot_id: str
    discord_bot_id: Optional[str]
    last_dedupe_key: Optional[str]
    attempts: int


class WebhookDispatcher:
    """Background worker that delivers pending webhook nudges."""

    def __init__(
        self,
        session_factory: sessionmaker,
        backend_webhooks: Dict[str, WebhookConfig],
        *,
        client: Optional[httpx.AsyncClient] = None,
        poll_interval_seconds: float = 1.0,
    ):
        self._session_factory = session_factory
        self._backend_webhooks = backend_webhooks
        self._poll_interval_seconds = poll_interval_seconds
        self._client = client or httpx.AsyncClient()
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self._task:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="webhook-dispatcher")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._client.aclose()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.process_once()
            except Exception:
                LOG.exception("Webhook dispatcher loop error")
            await asyncio.sleep(self._poll_interval_seconds)

    async def process_once(self, *, limit: int = 25) -> int:
        """Process due nudges once; returns number of nudges attempted."""
        now = datetime.now(timezone.utc)
        claimed = await asyncio.to_thread(self._claim_due_nudges_sync, now, limit)
        for nudge in claimed:
            await self._deliver_one(nudge)
        return len(claimed)

    def _claim_due_nudges_sync(self, now: datetime, limit: int) -> List[ClaimedNudge]:
        session: Session = self._session_factory()
        try:
            nudges: List[WebhookNudge] = (
                session.execute(
                    select(WebhookNudge)
                    .where(
                        WebhookNudge.state == WebhookNudgeState.PENDING,
                        WebhookNudge.next_attempt_at <= now,
                    )
                    .order_by(WebhookNudge.next_attempt_at)
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            claimed: List[ClaimedNudge] = []
            for nudge in nudges:
                nudge.state = WebhookNudgeState.SENDING
                nudge.updated_at = now
                claimed.append(
                    ClaimedNudge(
                        id=nudge.id,
                        backend_bot_id=nudge.backend_bot_id,
                        discord_bot_id=nudge.discord_bot_id,
                        last_dedupe_key=nudge.last_dedupe_key,
                        attempts=nudge.attempts,
                    )
                )
            session.commit()
            return claimed
        finally:
            session.close()

    async def _deliver_one(self, nudge: ClaimedNudge) -> None:
        webhook = self._backend_webhooks.get(nudge.backend_bot_id)
        if not webhook:
            await asyncio.to_thread(self._delete_nudge_sync, nudge.id)
            return

        payload = {
            "event": "messages_available",
            "backend_bot_id": nudge.backend_bot_id,
            "discord_bot_id": nudge.discord_bot_id,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "dedupe_key": nudge.last_dedupe_key,
        }
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        timestamp = str(int(time.time()))
        try:
            secret = webhook.resolved_secret()
        except Exception as exc:
            await asyncio.to_thread(
                self._mark_failed_sync,
                nudge.id,
                f"secret_error:{exc.__class__.__name__}",
            )
            return
        signature = compute_webhook_signature(secret, timestamp, body)

        headers = {
            "Content-Type": "application/json",
            "X-Relay-Timestamp": timestamp,
            "X-Relay-Signature": signature,
        }

        timeout = httpx.Timeout(webhook.request_timeout_seconds)
        try:
            resp = await self._client.post(webhook.url, content=body, headers=headers, timeout=timeout)
        except Exception as exc:
            await self._schedule_retry(nudge, webhook, f"request_error:{exc.__class__.__name__}")
            return

        if 200 <= resp.status_code < 300:
            await asyncio.to_thread(self._delete_nudge_sync, nudge.id)
            return

        retryable = resp.status_code == 429 or resp.status_code >= 500
        error = f"http_status:{resp.status_code}"
        if retryable:
            await self._schedule_retry(nudge, webhook, error)
        else:
            await asyncio.to_thread(self._mark_failed_sync, nudge.id, error)

    async def _schedule_retry(self, nudge: ClaimedNudge, webhook: WebhookConfig, error: str) -> None:
        attempts = nudge.attempts + 1
        if attempts > webhook.max_retries:
            await asyncio.to_thread(self._mark_failed_sync, nudge.id, error)
            return

        backoff_idx = max(0, min(attempts - 1, len(webhook.retry_backoff_seconds) - 1))
        retry_in = webhook.retry_backoff_seconds[backoff_idx]
        next_attempt = datetime.now(timezone.utc) + timedelta(seconds=retry_in)
        await asyncio.to_thread(self._reschedule_nudge_sync, nudge.id, attempts, next_attempt, error)

    def _delete_nudge_sync(self, nudge_id: str) -> None:
        session: Session = self._session_factory()
        try:
            nudge = session.get(WebhookNudge, nudge_id)
            if nudge:
                session.delete(nudge)
                session.commit()
        finally:
            session.close()

    def _mark_failed_sync(self, nudge_id: str, error: str) -> None:
        session: Session = self._session_factory()
        try:
            nudge = session.get(WebhookNudge, nudge_id)
            if not nudge:
                return
            nudge.state = WebhookNudgeState.FAILED
            nudge.last_error = error
            nudge.updated_at = datetime.now(timezone.utc)
            session.commit()
        finally:
            session.close()

    def _reschedule_nudge_sync(
        self,
        nudge_id: str,
        attempts: int,
        next_attempt_at: datetime,
        error: str,
    ) -> None:
        session: Session = self._session_factory()
        try:
            nudge = session.get(WebhookNudge, nudge_id)
            if not nudge:
                return
            nudge.state = WebhookNudgeState.PENDING
            nudge.attempts = attempts
            nudge.next_attempt_at = next_attempt_at
            nudge.last_error = error
            nudge.updated_at = datetime.now(timezone.utc)
            session.commit()
        finally:
            session.close()
