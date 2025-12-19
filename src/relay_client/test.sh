## Channel messages test 

# Lease messages (does not mark delivered until ack):
# python cli.py --config ../../config.yaml --backend-id backend_lmao lease --limit 50 --lease-seconds 300
# [{"delivery_id": "...", "lease_id": "...", "discord_bot_id": "discord_lmao", "discord_message": {...}, "lease_expires_at": "..."}]
#
# Ack once processed:
# python cli.py --config ../../config.yaml --backend-id backend_lmao ack --lease-id <lease_id> --delivery-ids <delivery_id>

# send using channel-id from leased message
python cli.py --config ../../config.yaml --backend-id backend_lmao send --discord-bot-id discord_lmao --channel-id 1450655195850735707 --content 'content'

## DM Test 

# send using author-id from leased message
python cli.py --config ../../config.yaml --backend-id backend_lmao send --discord-bot-id discord_lmao --dm-user-id 230985725926047744  --content 'dm reply'


# using an alias 

so using an alias of relayctl='python /home/lmao/discord_messages_relay/src/relay_client/cli.py  --config /home/lmao/discord_messages_relay/config.yaml --backend-id backend_lmao'
