import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from relay_server.config import WebhookConfig
from relay_server.models import WebhookNudge, WebhookNudgeState, create_session_factory
from relay_server.queue import DiscordMessageRecord, QueueService
from relay_server.webhooks import WebhookDispatcher, compute_webhook_signature


def test_enqueue_schedules_debounced_webhook_nudge(tmp_path):
    db_path = tmp_path / "relay.db"
    session_factory = create_session_factory(f"sqlite:///{db_path}")
    queue = QueueService(session_factory, webhook_debounce_seconds={"backend_alpha": 2.0})

    payload1 = DiscordMessageRecord(
        discord_message_id="55",
        discord_bot_id="discord_a",
        author_id="user1",
        author_name="User One",
        channel_id=None,
        guild_id=None,
        is_dm=True,
        content="hello",
        timestamp=datetime.now(timezone.utc),
    )
    asyncio.run(queue.enqueue_message("backend_alpha", payload1, "discord_a:55"))

    session = session_factory()
    try:
        nudge1 = session.execute(select(WebhookNudge)).scalars().one()
        assert nudge1.backend_bot_id == "backend_alpha"
        assert nudge1.state == WebhookNudgeState.PENDING
        assert nudge1.last_dedupe_key == "discord_a:55"
        next1 = nudge1.next_attempt_at
    finally:
        session.close()

    payload2 = DiscordMessageRecord(
        discord_message_id="56",
        discord_bot_id="discord_a",
        author_id="user1",
        author_name="User One",
        channel_id=None,
        guild_id=None,
        is_dm=True,
        content="another message",
        timestamp=datetime.now(timezone.utc),
    )
    asyncio.run(queue.enqueue_message("backend_alpha", payload2, "discord_a:56"))

    session = session_factory()
    try:
        nudge2 = session.execute(select(WebhookNudge)).scalars().one()
        assert nudge2.last_dedupe_key == "discord_a:56"
        assert nudge2.next_attempt_at >= next1
    finally:
        session.close()


def test_dispatcher_sends_signed_webhook_and_clears_outbox(tmp_path):
    db_path = tmp_path / "relay.db"
    session_factory = create_session_factory(f"sqlite:///{db_path}")
    secret = "test-secret"
    webhook = WebhookConfig(url="https://example.com/nudge", secret=secret)

    now = datetime.now(timezone.utc)
    session = session_factory()
    try:
        session.add(
            WebhookNudge(
                backend_bot_id="backend_alpha",
                discord_bot_id="discord_a",
                last_dedupe_key="discord_a:55",
                state=WebhookNudgeState.PENDING,
                attempts=0,
                next_attempt_at=now - timedelta(seconds=1),
                last_error=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    finally:
        session.close()

    def handler(request: httpx.Request) -> httpx.Response:
        timestamp = request.headers.get("X-Relay-Timestamp")
        signature = request.headers.get("X-Relay-Signature")
        assert timestamp
        assert signature
        expected = compute_webhook_signature(secret, timestamp, request.content)
        assert signature == expected
        return httpx.Response(200, text="ok")

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            dispatcher = WebhookDispatcher(
                session_factory,
                {"backend_alpha": webhook},
                client=client,
                poll_interval_seconds=0.0,
            )
            assert await dispatcher.process_once() == 1

    asyncio.run(_run())

    session = session_factory()
    try:
        nudges = session.execute(select(WebhookNudge)).scalars().all()
        assert nudges == []
    finally:
        session.close()

