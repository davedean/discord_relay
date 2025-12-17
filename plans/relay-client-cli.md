# Relay Client CLI (Python) — Plan

## Goals
- Provide a very small CLI for interacting with the relay REST API.
- Support cron-friendly usage (non-interactive, stable exit codes, JSON output).
- Require minimal arguments and avoid heavyweight dependencies.
- Prefer reading connection/auth from the same YAML config file as the server.

## Scope (MVP)
- Commands:
  - `retrieve`: fetch pending messages for the backend bot (consume-on-read behavior matches server MVP).
  - `send`: send a message to Discord (DM or channel).
- Auth:
  - API key via config file, env var, or CLI flag.
- Output:
  - JSON to stdout by default, optional human-readable mode.

## CLI UX
### Global flags/env
- `--config` (or env `RELAY_CONFIG`) path to YAML; preferred
- `--backend-id` backend bot ID from config (required when using `--config`)
- `--base-url` (or env `RELAY_BASE_URL`) override
- `--api-key` (or env `RELAY_API_KEY`) override
- `--timeout` seconds (default 10)
- `--json` (default on) and `--pretty`
- `--quiet` (suppress non-JSON logging)
- `--request-id` (optional) forwarded as `X-Request-Id`

### Example (config-first)
- Retrieve:
  - `relayctl --config ./config.yaml --backend-id backend_alpha retrieve --limit 50`
- Send a DM:
  - `relayctl --config ./config.yaml --backend-id backend_alpha send --discord-bot-id discord_a --dm-user-id 111... --content "hi"`

### `retrieve`
- Usage:
  - `relayctl retrieve [--limit 50]`
- Behavior:
  - `GET /v1/messages/pending?limit=...`
  - Print JSON array of messages; exit `0` even if empty.
  - Exit non-zero on auth/network/5xx errors.

### `send`
- Usage:
  - `relayctl send --discord-bot-id <id> (--dm-user-id <id> | --channel-id <id>) --content <text>`
  - Optional: `--reply-to <discord_message_id>`
- Behavior:
  - `POST /v1/messages/send`
  - Print server response JSON; exit non-zero on validation/auth errors.

## Implementation Plan
1. Package layout
   - Add `client/` (or `src/relayctl/`) with a single entrypoint module.
2. Arg parsing
   - Use stdlib `argparse` (min deps) with subcommands.
3. Config loading
   - Parse YAML (same schema as server for `backend_bots` and `server.base_url`).
   - Resolve `api_key`/`api_key_env`; allow env/flag overrides.
4. HTTP transport
   - Use `httpx` (recommended) or `requests` (acceptable) with timeouts and clean error handling.
5. Output
   - Default JSON; `--pretty` uses indented JSON.
   - Ensure content printed only to stdout; diagnostics to stderr.
6. Distribution
   - Installable console script entry point: `relayctl`.
   - Optional single-file “script mode” for very small deployments.

## Progress
- ✅ Package layout + CLI entrypoint under `src/relay_client`.
- ✅ Arg parsing, config resolution, and env overrides with `resolve_connection`.
- ✅ HTTP transport/output handling for `retrieve` and `send`, including exit codes.
- ✅ Tests covering config resolution flows and pytest harness.

## Testing
- Unit tests for:
  - argument parsing (happy paths + missing required args)
  - env var fallback behavior
  - HTTP error mapping to exit codes
- Integration-ish tests:
  - stub server using FastAPI `TestClient` to validate request/response shapes.

## Exit Codes (suggested)
- `0`: success (including empty retrieve)
- `2`: CLI usage error / validation
- `10`: auth error (401/403)
- `20`: network/timeout
- `30`: server error (5xx) or unexpected response

## Future Enhancements
- Support explicit `lease`/`ack` if/when the server adds it:
  - `relayctl lease`, `relayctl ack <delivery_id>`, `relayctl nack <delivery_id>`
- Add `relayctl watch` for SSE push (see `plans/push-to-bot-architecture.md`).
- Support attachments (file upload or URL) if server supports it.
