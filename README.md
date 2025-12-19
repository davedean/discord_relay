# Discord Messages Relay

Python implementation of the relay service described in `plans/discord-messages-relay.md`. The service listens to one or more Discord bots, stores incoming messages, and exposes a REST API for backend bots to lease messages for processing and send replies.

## Features
- FastAPI REST API (`/v1/messages/lease`, `/v1/messages/ack`, `/v1/messages/send`, `/v1/health`).
- YAML configuration (see `config.example.yaml`).
- SQLite-backed queue with per-backend delivery tracking.
- Optional persisted webhook nudges per backend bot (`backend_bots[].webhook`).
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

### API-only mode (no Discord connections)
Useful for smoke-testing the API container/config without Discord tokens:
```bash
export RELAY_CONFIG=./config.yaml
export RELAY_START_DISCORD=0
export RELAY_START_WEBHOOKS=0
uvicorn relay_server.main:app --host 0.0.0.0 --port 8080
```

## REST API Quick Reference
- `GET /v1/health` → `{ "status": "ok" }`
- `GET /v1/auth/whoami` (requires `Authorization: Bearer <api_key>`)
- `POST /v1/messages/lease` with body:
  ```json
  { "limit": 50, "lease_seconds": 300 }
  ```
- `POST /v1/messages/ack` with body:
  ```json
  { "lease_id": "…", "delivery_ids": ["…"] }
  ```
- `POST /v1/messages/send` with body:
  ```json
  {
    "discord_bot_id": "discord_a",
    "destination": { "type": "dm", "user_id": "111..." },
    "content": "hello!"
  }
  ```

## Notes
- Backends should use `lease → ack/nack`; messages are only marked delivered on `ack`.
- Unmatched routes are dropped unless you specify `routing.defaults` per Discord bot.
- For production use, consider moving secrets out of YAML and enabling webhook nudges (push-triggered processing).

## Running tests
Install dev dependencies and run pytest:
```bash
pip install -e '.[dev]'
pytest
```
