# Discord Messages Relay Client

A command-line client for interacting with the Discord Messages Relay REST API.

## Installation

The client is a Python script with dependencies on `httpx` and `pyyaml`. Ensure you have these installed:

```bash
pip install httpx pyyaml
```

## Usage

The client is invoked via `cli.py` (or the installed `relayctl` entrypoint) and supports health checks, leasing, acknowledgements, and sending messages.

### Configuration

You can configure the client using:
- Command line arguments
- Environment variables
- A YAML configuration file

#### Configuration Options

- `--config`: Path to relay config YAML file
- `--backend-id`: Backend bot ID 
- `--base-url`: Relay server base URL
- `--api-key`: API key for the backend bot

#### Environment Variables

- `RELAY_CONFIG`: Path to config file
- `RELAY_BACKEND_ID`: Backend bot ID
- `RELAY_BASE_URL`: Server base URL
- `RELAY_API_KEY`: API key

### Commands

#### Health Check

Check the relay server is reachable:

```bash
python cli.py --config ../../config.yaml health
```

#### Verify Auth (`whoami`)

Verify the API key is accepted and print the backend identity:

```bash
python cli.py --config ../../config.yaml --backend-id backend_lmao whoami
```

#### Lease Messages

Lease messages for processing (messages are only marked delivered after `ack`):

```bash
python cli.py --config ../../config.yaml --backend-id backend_lmao lease --limit 50 --lease-seconds 300
```

#### Send Messages

Send a message on behalf of a backend bot:

```bash
# Send to a channel
python cli.py --config ../../config.yaml --backend-id backend_lmao send \
  --discord-bot-id discord_lmao \
  --channel-id 1450655195850735707 \
  --content "Hello from relay!"

# Send as DM
python cli.py --config ../../config.yaml --backend-id backend_lmao send \
  --discord-bot-id discord_lmao \
  --dm-user-id 230985725926047744 \
  --content "DM reply"
```

Required arguments:
- `--discord-bot-id`: Discord bot ID to use
- `--channel-id` OR `--dm-user-id`: Destination
- `--content`: Message text

Optional:
- `--reply-to`: Discord message ID to reply to

### Example Workflow

1. Lease messages:
```bash
python cli.py --config ../../config.yaml --backend-id backend_lmao lease --limit 10
```

2. Send replies using the channel/user IDs in the leased messages:
```bash
python cli.py --config ../../config.yaml --backend-id backend_lmao send \
  --discord-bot-id discord_lmao \
  --channel-id 1450655195850735707 \
  --content "Reply to message"
```

3. Acknowledge the deliveries you successfully processed:
```bash
python cli.py --config ../../config.yaml --backend-id backend_lmao ack --lease-id <lease_id> --delivery-ids <delivery_id> [<delivery_id> ...]
```

### Aliases

For convenience, you can create an alias:

```bash
alias relayctl='python /path/to/cli.py --config /path/to/config.yaml --backend-id backend_lmao'
```

Then use:
```bash
relayctl lease
relayctl send --discord-bot-id discord_lmao --channel-id 123456 --content "Hello"
```

## Output Formats

- **JSON** (default): Machine-readable output
- **Human-readable**: Concise summaries with `--no-json`

## Error Codes

- `0`: Success
- `2`: Usage error
- `10`: Authentication error  
- `20`: Network error
- `30`: Server error
