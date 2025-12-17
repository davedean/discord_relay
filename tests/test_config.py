import pytest

from relay_server.config import ConfigError, load_config


def _base_config(tmp_sqlite_path: str) -> dict:
    return {
        "server": {"bind_host": "127.0.0.1", "bind_port": 9999},
        "storage": {"database_url": tmp_sqlite_path},
        "discord_bots": [
            {
                "id": "discord_a",
                "name": "Bot A",
                "token": "TEST_TOKEN",
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
        "routing": {"mode": "first_match", "defaults": {"discord_a": "backend_alpha"}},
        "routes": [],
    }


def test_load_config_success(write_config, tmp_path):
    cfg = _base_config(f"sqlite:///{tmp_path}/relay.db")
    path = write_config(cfg)
    loaded = load_config(str(path))
    assert loaded.data.discord_bots[0].resolved_token() == "TEST_TOKEN"
    assert loaded.data.backend_bots[0].resolved_api_key() == "alpha-key"


def test_load_config_missing_file():
    with pytest.raises(ConfigError):
        load_config("missing.yaml")


def test_load_config_duplicate_backend_ids(write_config, tmp_path):
    cfg = _base_config(f"sqlite:///{tmp_path}/relay.db")
    cfg["backend_bots"].append(
        {"id": "backend_alpha", "name": "Dup", "api_key": "dup", "enabled": True}
    )
    path = write_config(cfg)
    with pytest.raises(ConfigError):
        load_config(str(path))
