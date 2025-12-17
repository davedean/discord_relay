# Discord Messages Relay (Python) — Build Plan (MVP → v1)

## Goals
- Relay Discord DMs and (optionally) guild channels to/from “backend bots” via a REST API.
- Support multiple Discord bots and multiple backend bots, with isolation so end-users don’t see other backends’ messages.
- Support a cron-style backend: backend wakes, pulls pending messages, posts responses, exits.
- Track which messages have been retrieved by each backend bot (basic queue semantics for MVP).
- Keep the design extensible for future explicit ack/pop (at-least-once → exactly-once-ish) behavior.

## Non-goals (MVP)
- Full Matterbridge feature parity (rich attachments, threads, reactions, edits/deletes).
- Zero-loss delivery guarantees across crashes without explicit ack/pop.
- Complex permission management UI.

## Concrete Choices (So Devs Can Start)
- Python: 3.12
- Discord library: `discord.py` 2.x (gateway client, async)
- API: FastAPI + Uvicorn
- DB: SQLite (WAL mode) via SQLAlchemy 2.x + Alembic migrations
- HTTP client (CLI): `httpx`
- Config: single YAML file (restart to apply changes); secrets may still come from env via references

## Proposed Architecture
**Process model**
- One always-on Python service:
  - Discord gateway client(s) (one per Discord bot token) to ingest messages in real time.
  - REST API server for backend bots to poll and to post outbound messages.
  - Persistent store for users, mappings, and queued messages (SQLite for MVP, Postgres-ready schema).

**Core components**
1. `discord_ingest`: `discord.py` client(s) that listen to:
   - DMs to the bot account.
   - Guild channel messages where the bot is present (configurable allowlist per bot).
2. `router`: applies routing rules to decide which backend bot(s) receive each incoming Discord message.
3. `queue_store`: persists messages and per-backend delivery state.
4. `rest_api`: backend-facing API (FastAPI recommended).
5. `auth`: simple API keys for backend bots (loaded from config).

## Configuration File (YAML)
Use a single config file (e.g., `config.yaml`) as the source of truth for Discord bots, backend bots, and routes.

### Suggested shape
- `server`
  - `bind_host`, `bind_port`
  - `base_url` (optional; for clients/docs)
- `storage`
  - `database_url` (e.g., `sqlite:///./relay.db`)
- `discord_bots[]`
  - `id` (string, stable; used in API)
  - `name`
  - `token` (string) OR `token_env` (env var name)
  - `enabled`
  - `channel_allowlist` (list of channel IDs; empty = no guild ingest)
- `backend_bots[]`
  - `id` (string, stable; used in deliveries)
  - `name`
  - `api_key` (string) OR `api_key_env` (env var name)
  - `enabled`
- `routing`
  - `mode`: `first_match`
  - `precedence`: `dm_user` > `channel` > `guild` > `default`
  - `defaults`: per-`discord_bot_id` optional `default_backend_bot_id` (if absent, unrouted messages are dropped)
- `routes[]`
  - `discord_bot_id`
  - `scope_type`: `dm_user|channel|guild`
  - `scope_id`: discord user/channel/guild ID
  - `backend_bot_id`

### Minimal example
```yaml
server:
  bind_host: "0.0.0.0"
  bind_port: 8080
  base_url: "http://127.0.0.1:8080"

storage:
  database_url: "sqlite:///./relay.db"

discord_bots:
  - id: "discord_a"
    name: "Discord Bot A"
    token_env: "DISCORD_TOKEN_A"
    enabled: true
    channel_allowlist: ["123456789012345678"]

backend_bots:
  - id: "backend_alpha"
    name: "Backend Alpha"
    api_key: "dev-only-change-me"
    enabled: true

routing:
  mode: "first_match"
  defaults:
    discord_a: "backend_alpha" # optional; omit to drop unrouted messages

routes:
  - discord_bot_id: "discord_a"
    scope_type: "dm_user"
    scope_id: "111111111111111111"
    backend_bot_id: "backend_alpha"
  - discord_bot_id: "discord_a"
    scope_type: "channel"
    scope_id: "123456789012345678"
    backend_bot_id: "backend_alpha"
```

### Validation rules (MVP)
- If multiple routes match at the same precedence level, treat config as invalid and fail fast at startup.
- If no route matches and no default is configured, do not enqueue a delivery (prevents accidental cross-talk).

## Data Model (MVP)
Use SQLite with clear upgrade path (e.g., `sqlalchemy` + Alembic migrations).

### Entities
- `discord_bot`
  - `id` (string from config), `name`, `enabled`
- `backend_bot`
  - `id` (string from config), `name`, `api_key` (optional snapshot), `enabled`
- `route`
  - For MVP, keep routes in config only (no DB table); persist only message + delivery state.
- `discord_message`
  - `id`, `discord_bot_id`, `discord_message_id`, `author_id`, `author_name`, `channel_id`, `guild_id`, `is_dm`
  - `content`, `timestamp`, `raw_json` (optional for future), `dedupe_key` (unique)
- `delivery`
  - `id`, `discord_message_id`, `backend_bot_id`, `state` (`pending|delivered`)
  - `delivered_at`, `delivery_attempts`

### MVP queue semantics
- On ingest: create `discord_message`, then create one `delivery` per target backend bot with `state=pending`.
- On backend poll: return pending deliveries and mark them `delivered` immediately (simple “consume-on-read”).
- Future: introduce `leased` state with a lease timeout, and explicit `ack` endpoint.

## Discord Ingest Details
- Library: `discord.py` (async).
- Intents:
  - DMs: ensure DM events enabled.
  - Guild channels: message content intent may be required depending on bot permissions.
- Dedupe:
  - Unique constraint on (`discord_bot_id`, `discord_message_id`) and/or computed `dedupe_key`.
- Ignore:
  - Messages authored by the bot itself to prevent loops.
  - Optionally ignore other bots.

## Routing & Isolation Model
- Routing mode (MVP): `first_match` (exactly one backend bot per message).
- Match precedence (MVP): `dm_user` > `channel` > `guild` > `default`.
- Scopes:
  - `dm_user`: DM messages from a specific Discord user ID
  - `channel`: messages in a specific channel ID
  - `guild`: any message in a guild ID (still subject to `channel_allowlist` to ingest at all)
- Unmatched behavior (MVP): drop (do not enqueue) unless `default_backend_bot_id` configured for that Discord bot.
- Future: optional fan-out routes.

## REST API (MVP)
Use FastAPI + Uvicorn. All endpoints require backend bot API key.

## Companion Client (CLI)
- Add a lightweight CLI client for backend bots/operators to `retrieve` pending messages and `send` messages without writing custom HTTP code.
- Detailed client plan: `plans/relay-client-cli.md`.

### Auth
- Header: `Authorization: Bearer <api_key>`
- For this hobby MVP, accept API keys loaded from YAML (or `*_env`) and compare directly.
- Note: storing API keys in plaintext on disk is not recommended for production; prefer `*_env` or a secrets manager if you later harden this.

### Endpoints
1. `GET /v1/messages/pending?limit=50`
   - Returns list of pending messages for the authenticated backend bot.
   - MVP behavior: marks returned messages as `delivered` in the same transaction.
2. `POST /v1/messages/send`
   - Body includes:
     - `discord_bot_id`
     - destination: `dm_user_id` OR `channel_id`
     - `content`
     - optional: `reply_to_discord_message_id`
   - Sends to Discord and records an outbound record (optional in MVP).
3. `GET /v1/health`
   - Basic liveness.

### API contract details (MVP)
- Auth header: `Authorization: Bearer <api_key>`
- Error responses (suggested):
  - `400`: invalid request
  - `401/403`: auth failure
  - `404`: unknown `discord_bot_id` (or destination invalid)
  - `409`: duplicate/out-of-order idempotency key (if implemented)
  - `429`: rate limited (optional)
  - `500`: server error
- Add `X-Request-Id` to responses; accept optional `X-Request-Id` from clients.

### Schemas (MVP)
`GET /v1/messages/pending` response body:
```json
{
  "messages": [
    {
      "delivery_id": "uuid",
      "discord_bot_id": "discord_a",
      "discord_message": {
        "discord_message_id": "109876543210987654",
        "timestamp": "2025-01-01T00:00:00Z",
        "content": "hello",
        "source": {
          "is_dm": true,
          "guild_id": null,
          "channel_id": "222222222222222222",
          "author_id": "111111111111111111",
          "author_name": "someuser"
        }
      }
    }
  ]
}
```

`POST /v1/messages/send` request body:
```json
{
  "discord_bot_id": "discord_a",
  "destination": { "type": "dm", "user_id": "111111111111111111" },
  "content": "hi back",
  "reply_to_discord_message_id": "109876543210987654"
}
```

`POST /v1/messages/send` response body:
```json
{
  "discord_message_id": "209876543210987654",
  "channel_id": "222222222222222222"
}
```

## Service Configuration
- Primary config: YAML file (see “Configuration File” section).
- Env vars only for secrets referenced by config (`*_env`) and for selecting the config file path (e.g., `RELAY_CONFIG=...`).
- No admin UI/CLI in MVP; edit config + restart.

## Implementation Milestones
### Milestone 0 — Skeleton ✅
- Project layout, FastAPI app, and config loader are wired up in `src/relay_server/main.py` and `src/relay_server/config.py`.
- `src/relay_server/models.py` and `src/relay_server/queue.py` define the SQLite schema, delivery pipeline, and queue persistence helpers.

### Milestone 1 — Ingest DMs for one Discord bot ✅
- `RelayDiscordClient` in `src/relay_server/discord_client.py` listens for DM events, applies routing, and enqueues with deduplication.
- The `/v1/messages/pending` endpoint in `src/relay_server/main.py` consumes pending deliveries and marks them delivered (`tests/test_api.py` verifies the end-to-end flow).

### Milestone 2 — Outbound send ✅
- `POST /v1/messages/send` forwards validated payloads to `DiscordManager.send_text` and returns Discord’s message ID; self-messages are ignored by the guild/DM handler filters.

### Milestone 3 — Multi-bot support + routing ✅
- `RoutingTable` (`src/relay_server/routing.py`) enforces precedence across scopes and defaults, while `DiscordManager` iterates every enabled bot/token and honors each bot’s `channel_allowlist`.

### Milestone 4 — Hardening (partial) ⚠️
- API key auth is implemented in `src/relay_server/auth.py` and used by every endpoint, with logging configured during startup.
- Rate limiting, advanced structured logging, and idempotency (beyond `X-Request-Id`) remain future work.

## Testing Strategy
- Unit tests for:
  - routing selection
  - queue “consume-on-read” transaction behavior
  - auth
- Integration tests:
  - FastAPI endpoints with SQLite in-memory
- Manual test checklist:
  - DM flows end-to-end
  - Channel flows end-to-end
  - Multi-bot isolation (no cross-delivery)

## Deployment Notes
- Run as a long-lived service (systemd, Docker, or similar).
- Ensure Discord bot privileges and intents are correctly configured in the Discord developer portal.
- Prefer Postgres for v1+ if message volume grows.

## Future Enhancements (Post-MVP)
- Explicit `lease` + `ack`:
  - `GET /v1/messages/lease` returns messages and marks as `leased` with `lease_expires_at`
  - `POST /v1/messages/ack` confirms processing; `POST /v1/messages/nack` requeues
- Attachments/embeds support (store URLs/metadata).
- Thread and reply context, edits/deletes, reactions.
- Webhooks or server-sent events for push (if cron model changes).

## Push Architecture (Future)
- Plan: `plans/push-to-bot-architecture.md`.
