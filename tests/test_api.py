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
    asyncio.run(
        state.queue_service.enqueue_message("backend_alpha", payload, "discord_a:55")
    )

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


def test_lease_messages_requires_auth(write_config, tmp_path):
    cfg = _config_dict(tmp_path / "relay.db")
    path = write_config(cfg)
    app = create_app(str(path), start_discord=False)
    client = TestClient(app)
    with client:
        resp = client.post("/v1/messages/lease", json={})
        assert resp.status_code == 401


def test_lease_messages_returns_leased_messages(write_config, tmp_path):
    cfg = _config_dict(tmp_path / "relay.db")
    path = write_config(cfg)
    app = create_app(str(path), start_discord=False)
    state = app.state.relay_state

    payload = DiscordMessageRecord(
        discord_message_id="lease_test_1",
        discord_bot_id="discord_a",
        author_id="user1",
        author_name="User One",
        channel_id="channel_123",
        guild_id=None,
        is_dm=False,
        content="lease me",
        timestamp=datetime.now(timezone.utc),
    )
    asyncio.run(
        state.queue_service.enqueue_message(
            "backend_alpha", payload, "discord_a:lease_test_1"
        )
    )

    client = TestClient(app)
    headers = {"Authorization": "Bearer alpha-key"}
    payload_req = {
        "limit": 10,
        "lease_seconds": 300,
        "include_conversation_history": False,
    }
    with client:
        resp = client.post("/v1/messages/lease", json=payload_req, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 1
        assert data["messages"][0]["discord_message"]["content"] == "lease me"
        assert "lease_id" in data["messages"][0]
        assert "lease_expires_at" in data["messages"][0]


def test_ack_messages_requires_auth(write_config, tmp_path):
    cfg = _config_dict(tmp_path / "relay.db")
    path = write_config(cfg)
    app = create_app(str(path), start_discord=False)
    client = TestClient(app)
    with client:
        resp = client.post(
            "/v1/messages/ack", json={"delivery_ids": [], "lease_id": "test"}
        )
        assert resp.status_code == 401


def test_ack_messages_acknowledges_leased_deliveries(write_config, tmp_path):
    cfg = _config_dict(tmp_path / "relay.db")
    path = write_config(cfg)
    app = create_app(str(path), start_discord=False)
    state = app.state.relay_state

    # First lease a message
    payload = DiscordMessageRecord(
        discord_message_id="ack_test_1",
        discord_bot_id="discord_a",
        author_id="user1",
        author_name="User One",
        channel_id=None,
        guild_id=None,
        is_dm=True,
        content="ack me",
        timestamp=datetime.now(timezone.utc),
    )
    asyncio.run(
        state.queue_service.enqueue_message(
            "backend_alpha", payload, "discord_a:ack_test_1"
        )
    )

    client = TestClient(app)
    headers = {"Authorization": "Bearer alpha-key"}

    # Lease the message
    lease_payload = {
        "limit": 10,
        "lease_seconds": 300,
        "include_conversation_history": False,
    }
    with client:
        lease_resp = client.post(
            "/v1/messages/lease", json=lease_payload, headers=headers
        )
        assert lease_resp.status_code == 200
        lease_data = lease_resp.json()
        delivery_id = lease_data["messages"][0]["delivery_id"]
        lease_id = lease_data["messages"][0]["lease_id"]

        # Now ack it
        ack_payload = {
            "delivery_ids": [delivery_id],
            "lease_id": lease_id,
        }
        ack_resp = client.post("/v1/messages/ack", json=ack_payload, headers=headers)
        assert ack_resp.status_code == 200
        assert ack_resp.json()["acknowledged_count"] == 1


def test_nack_messages_requires_auth(write_config, tmp_path):
    cfg = _config_dict(tmp_path / "relay.db")
    path = write_config(cfg)
    app = create_app(str(path), start_discord=False)
    client = TestClient(app)
    with client:
        resp = client.post(
            "/v1/messages/nack", json={"delivery_ids": [], "lease_id": "test"}
        )
        assert resp.status_code == 401


def test_conversation_history_included_when_requested(write_config, tmp_path):
    cfg = _config_dict(tmp_path / "relay.db")
    path = write_config(cfg)
    app = create_app(str(path), start_discord=False)
    state = app.state.relay_state

    # Add some historical messages in the same channel
    base_time = datetime.now(timezone.utc)
    for i in range(3):
        payload = DiscordMessageRecord(
            discord_message_id=f"hist_{i}",
            discord_bot_id="discord_a",
            author_id=f"user{i}",
            author_name=f"User {i}",
            channel_id="channel_123",
            guild_id=None,
            is_dm=False,
            content=f"historical message {i}",
            timestamp=base_time,
        )
        asyncio.run(
            state.queue_service.enqueue_message(
                "backend_alpha", payload, f"discord_a:hist_{i}"
            )
        )

    client = TestClient(app)
    headers = {"Authorization": "Bearer alpha-key"}

    with client:
        # First consume the historical messages using the old API
        resp = client.get("/v1/messages/pending?limit=10", headers=headers)
        assert resp.status_code == 200

        # Now add a new message to lease
        new_payload = DiscordMessageRecord(
            discord_message_id="new_msg",
            discord_bot_id="discord_a",
            author_id="user_new",
            author_name="New User",
            channel_id="channel_123",
            guild_id=None,
            is_dm=False,
            content="new message",
            timestamp=base_time,
        )
        asyncio.run(
            state.queue_service.enqueue_message(
                "backend_alpha", new_payload, "discord_a:new_msg"
            )
        )

        # Lease the new message with conversation history
        payload_req = {
            "limit": 10,
            "lease_seconds": 300,
            "include_conversation_history": True,
            "conversation_history_limit": 5,
        }
        resp = client.post("/v1/messages/lease", json=payload_req, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) == 1  # Only the new message should be leased
        assert "conversation_history" in data
        assert (
            len(data["conversation_history"]) > 0
        )  # Should include historical messages
