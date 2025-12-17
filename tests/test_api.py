import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from relay_server.main import create_app
from relay_server.queue import DiscordMessageRecord


def _config_dict(db_path: str) -> dict:
    return {
        "server": {},
        "storage": {"database_url": f"sqlite:///{db_path}"},
        "discord_bots": [
            {
                "id": "discord_a",
                "name": "Bot",
                "token": "TEST",
                "enabled": True,
                "channel_allowlist": [],
            }
        ],
        "backend_bots": [
            {
                "id": "backend_alpha",
                "name": "Backend Alpha",
                "api_key": "alpha-key",
                "enabled": True,
            }
        ],
        "routing": {"defaults": {"discord_a": "backend_alpha"}},
        "routes": [],
    }


def test_pending_requires_auth(write_config, tmp_path):
    cfg = _config_dict(tmp_path / "relay.db")
    path = write_config(cfg)
    app = create_app(str(path), start_discord=False)
    client = TestClient(app)
    with client:
        resp = client.get("/v1/messages/pending")
        assert resp.status_code == 401


def test_pending_returns_messages(write_config, tmp_path):
    cfg = _config_dict(tmp_path / "relay.db")
    path = write_config(cfg)
    app = create_app(str(path), start_discord=False)
    state = app.state.relay_state

    payload = DiscordMessageRecord(
        discord_message_id="55",
        discord_bot_id="discord_a",
        author_id="user1",
        author_name="User One",
        channel_id=None,
        guild_id=None,
        is_dm=True,
        content="hello relay",
        timestamp=datetime.now(timezone.utc),
    )
    asyncio.run(state.queue_service.enqueue_message("backend_alpha", payload, "discord_a:55"))

    client = TestClient(app)
    headers = {"Authorization": "Bearer alpha-key"}
    with client:
        resp = client.get("/v1/messages/pending", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"][0]["discord_message"]["content"] == "hello relay"


def test_send_message_uses_discord_manager(write_config, tmp_path):
    cfg = _config_dict(tmp_path / "relay.db")
    path = write_config(cfg)
    app = create_app(str(path), start_discord=False)
    state = app.state.relay_state
    state.discord_manager.send_text = AsyncMock(return_value="999")

    client = TestClient(app)
    headers = {"Authorization": "Bearer alpha-key"}
    payload = {
        "discord_bot_id": "discord_a",
        "destination": {"type": "dm", "user_id": "1"},
        "content": "hi there",
    }
    with client:
        resp = client.post("/v1/messages/send", json=payload, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["discord_message_id"] == "999"
        state.discord_manager.send_text.assert_awaited_once()
