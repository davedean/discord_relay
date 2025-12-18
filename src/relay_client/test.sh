## Channel messages test 

# lmao@lxcl5:~/discord_messages_relay/src/relay_client$ python cli.py  --config ../../config.yaml --backend-id backend_lmao retrieve
# Relay config missing at config.yaml (set RELAY_CONFIG). Using placeholder app.
# Retrieving up to 50 pending messages...
# [{"delivery_id": "32f3f796-af61-4db4-9710-1242e2cd3b7f", "discord_bot_id": "discord_lmao", "discord_message": {"discord_message_id": "1451036844836917350", "discord_bot_id": "discord_lmao", "timestamp": "2025-12-18T02:22:22.770000", "content": "testing 8888", "source": {"is_dm": false, "guild_id": "967990495395577926", "channel_id": "1450655195850735707", "author_id": "230985725926047744", "author_name": "t_mech"}}}]

# send using channel-id from retrieve
python cli.py --config ../../config.yaml --backend-id backend_lmao send --discord-bot-id discord_lmao --channel-id 1450655195850735707 --content 'content'

## DM Test 

# lmao@lxcl5:~/discord_messages_relay/src/relay_client$ python cli.py  --config ../../config.yaml --backend-id backend_lmao retrieve
# Relay config missing at config.yaml (set RELAY_CONFIG). Using placeholder app.
# Retrieving up to 50 pending messages...
# [{"delivery_id": "eef60124-e8cd-4a5b-87b0-b1094484b50c", "discord_bot_id": "discord_lmao", "discord_message": {"discord_message_id": "1451036605132312587", "discord_bot_id": "discord_lmao", "timestamp": "2025-12-18T02:21:25.620000", "content": "test dm 3", "source": {"is_dm": true, "guild_id": null, "channel_id": "1450778342637965312", "author_id": "230985725926047744", "author_name": "t_mech"}}}]

# send using author-id from retrieve
python cli.py --config ../../config.yaml --backend-id backend_lmao send --discord-bot-id discord_lmao --dm-user-id 230985725926047744  --content 'dm reply'


# using an alias 

so using an alias of relayctl='python /home/lmao/discord_messages_relay/src/relay_client/cli.py  --config /home/lmao/discord_messages_relay/config.yaml --backend-id backend_lmao'
