# Discord Messages Relay

Python implementation of the relay service described in `plans/discord-messages-relay.md`. The service listens to one or more Discord bots, stores incoming messages, and exposes a REST API for backend bots to retrieve pending messages and send replies.

## Features
- FastAPI REST API (`/v1/messages/pending`, `/v1/messages/send`, `/v1/health`).
- YAML configuration (see `config.example.yaml`).
- SQLite-backed queue with per-backend delivery tracking.
- Discord gateway ingestion with channel allowlists and DM support.

## Requirements
- Python 3.12+
- A Discord bot token for each bot you want to connect.

## Setup
1. Install dependencies:
   ```bash
   pip install -e .
   ```
2. Copy `config.example.yaml` to `config.yaml` and fill in your Discord tokens and backend API keys.
3. Set any referenced env vars (e.g., `DISCORD_TOKEN_A`).

## Running the server
```bash
export RELAY_CONFIG=./config.yaml
uvicorn relay_server.main:app --host 0.0.0.0 --port 8080
```

During startup the server automatically launches each enabled Discord bot from the config.

## REST API Quick Reference
- `GET /v1/health` → `{ "status": "ok" }`
- `GET /v1/messages/pending?limit=50` (requires `Authorization: Bearer <api_key>`)
- `POST /v1/messages/send` with body:
  ```json
  {
    "discord_bot_id": "discord_a",
    "destination": { "type": "dm", "user_id": "111..." },
    "content": "hello!"
  }
  ```

## Notes
- Messages are marked delivered as soon as they are retrieved (consume-on-read).
- Unmatched routes are dropped unless you specify `routing.defaults` per Discord bot.
- For production use, consider moving secrets out of YAML and enabling leasing/acknowledgements as described in the plans.

## Running tests
Install dev dependencies and run pytest:
```bash
pip install -e '.[dev]'
pytest
```
