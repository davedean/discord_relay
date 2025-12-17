from relay_server.config import AppConfig
from relay_server.routing import MessageContext, RoutingTable


def test_routing_precedence_dm_over_channel(tmp_path):
    config_dict = {
        "server": {},
        "storage": {"database_url": f"sqlite:///{tmp_path}/relay.db"},
        "discord_bots": [
            {"id": "discord_a", "name": "Bot", "token": "T", "enabled": True, "channel_allowlist": []}
        ],
        "backend_bots": [
            {"id": "backend_alpha", "name": "Alpha", "api_key": "a", "enabled": True},
            {"id": "backend_beta", "name": "Beta", "api_key": "b", "enabled": True},
        ],
        "routing": {"precedence": ["dm_user", "channel", "default"], "defaults": {}},
        "routes": [
            {
                "discord_bot_id": "discord_a",
                "scope_type": "channel",
                "scope_id": "123",
                "backend_bot_id": "backend_beta",
            },
            {
                "discord_bot_id": "discord_a",
                "scope_type": "dm_user",
                "scope_id": "999",
                "backend_bot_id": "backend_alpha",
            },
        ],
    }
    config = AppConfig.model_validate(config_dict)
    routing = RoutingTable(config)

    ctx = MessageContext(
        discord_bot_id="discord_a",
        author_id="999",
        channel_id="123",
        guild_id=None,
        is_dm=True,
    )
    assert routing.resolve_backend(ctx) == "backend_alpha"

    ctx2 = MessageContext(
        discord_bot_id="discord_a",
        author_id="888",
        channel_id="123",
        guild_id=None,
        is_dm=False,
    )
    assert routing.resolve_backend(ctx2) == "backend_beta"
