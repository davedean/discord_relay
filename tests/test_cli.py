import argparse

import pytest
import yaml

from relay_client.cli import CLIError, resolve_connection


def _write_config(path, data):
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh)
    return path


def _base_config():
    return {
        "server": {"base_url": "http://example.com"},
        "storage": {"database_url": "sqlite:///./test.db"},
        "discord_bots": [
            {
                "id": "discord_a",
                "name": "Test Bot",
                "token": "token-value",
                "enabled": True,
                "channel_allowlist": [],
            }
        ],
        "backend_bots": [
            {
                "id": "backend_alpha",
                "name": "Backend",
                "api_key": "alpha-key",
                "enabled": True,
            }
        ],
        "routing": {"defaults": {"discord_a": "backend_alpha"}},
        "routes": [],
    }


def test_resolve_connection_reads_config_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    _write_config(path, _base_config())
    args = argparse.Namespace(
        config=str(path),
        backend_id="backend_alpha",
        base_url=None,
        api_key=None,
    )

    connection = resolve_connection(args)
    assert connection.base_url == "http://example.com"
    assert connection.api_key == "alpha-key"
    assert connection.backend_id == "backend_alpha"


def test_resolve_connection_prefers_overrides(tmp_path):
    path = tmp_path / "config.yaml"
    _write_config(path, _base_config())
    args = argparse.Namespace(
        config=str(path),
        backend_id="backend_alpha",
        base_url="https://override/",
        api_key="direct-key",
    )

    connection = resolve_connection(args)
    assert connection.base_url == "https://override"
    assert connection.api_key == "direct-key"


def test_resolve_connection_requires_backend_id(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    _write_config(path, _base_config())
    monkeypatch.delenv("RELAY_BACKEND_ID", raising=False)
    args = argparse.Namespace(
        config=str(path),
        backend_id=None,
        base_url=None,
        api_key=None,
    )

    with pytest.raises(CLIError) as exc:
        resolve_connection(args)
    assert "--backend-id" in str(exc.value)
