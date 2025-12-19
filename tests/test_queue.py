import asyncio
from datetime import datetime, timezone

from relay_server.models import create_session_factory
from relay_server.queue import DiscordMessageRecord, QueueService


def test_queue_enqueue_and_fetch(tmp_path):
    db_url = f"sqlite:///{tmp_path}/queue.db"
    queue = QueueService(create_session_factory(db_url))

    payload = DiscordMessageRecord(
        discord_message_id="1",
        discord_bot_id="discord_a",
        author_id="user1",
        author_name="User",
        channel_id="chan1",
        guild_id=None,
        is_dm=False,
        content="hello",
        timestamp=datetime.now(timezone.utc),
    )

    asyncio.run(queue.enqueue_message("backend_alpha", payload, dedupe_key="discord_a:1"))
    leased, history = asyncio.run(
        queue.lease_messages(
            "backend_alpha",
            limit=10,
            lease_seconds=300,
            include_conversation_history=False,
            conversation_history_limit=20,
        )
    )
    assert history == []
    assert len(leased) == 1
    assert leased[0].message.content == "hello"

    acknowledged = asyncio.run(
        queue.acknowledge_deliveries(
            "backend_alpha",
            delivery_ids=[leased[0].delivery_id],
            lease_id=leased[0].lease_id,
        )
    )
    assert acknowledged == 1

    # Second lease should be empty because already delivered.
    leased, _ = asyncio.run(
        queue.lease_messages(
            "backend_alpha",
            limit=10,
            lease_seconds=300,
            include_conversation_history=False,
            conversation_history_limit=20,
        )
    )
    assert leased == []
