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
    messages = asyncio.run(queue.fetch_and_mark_delivered("backend_alpha", limit=10))
    assert len(messages) == 1
    assert messages[0].message.content == "hello"

    # Second fetch should be empty because already marked delivered.
    messages = asyncio.run(queue.fetch_and_mark_delivered("backend_alpha", limit=10))
    assert messages == []
