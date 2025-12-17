# Push-to-Bot Architecture — Plan (Future Enhancement)

## Goals
- Trigger backend bot execution (or notify it immediately) when a relevant Discord message arrives.
- Preserve current isolation/routing model (each backend bot only sees its own deliveries).
- Improve latency vs cron polling while retaining reliability (no message loss, retry on failure).

## Key Decision: What “Push” Means
There are two practical push models; choose based on how/where backend bots run:

1. **Push notifications to a long-lived bot process**
   - Backend bot runs continuously (or at least during “on hours”) and receives events via streaming or webhook.
2. **Push-triggered execution (server causes bot to run)**
   - Backend bot remains “mostly off” and is started on demand via a scheduler/executor (Docker/K8s/AWS Lambda/etc.).

Both models benefit from adding explicit `lease` + `ack` to avoid losing work when a bot crashes.

## Recommended Prerequisite: Leasing + Ack (Server-Side)
Add “at-least-once with retries” semantics so push can be reliable:
- Delivery states: `pending` → `leased` → `acked` (or back to `pending` on expiry/nack).
- Fields: `lease_expires_at`, `lease_id`, `last_error`, `attempts`.
- Endpoints:
  - `POST /v1/messages/lease` (or `GET`): returns messages, sets `leased` with TTL (e.g., 60s).
  - `POST /v1/messages/ack`: confirms processing by `delivery_id` + `lease_id`.
  - `POST /v1/messages/nack`: releases early (optional).
- Reaper job: periodically requeue expired leases (`leased` where `lease_expires_at < now`).

This can ship even before push; it hardens polling and enables safe push.

## Option A — Server Push via Streaming (SSE/WebSocket)
Best when backend bot can run continuously somewhere (tiny VM, container, etc.).

### Approach
- Keep the REST API; add a streaming endpoint per backend bot:
  - **SSE**: `GET /v1/stream` (simple, HTTP-friendly, good for firewalls).
  - **WebSocket**: `GET /v1/ws` (bidirectional, slightly more complexity).
- When a new delivery becomes `pending` for a backend bot:
  - Emit a lightweight event: `{type:"delivery_available"}` (no payload), or include a small payload.
- Backend bot:
  - Maintains a connection; on event, calls `lease` to fetch actual messages and process them.

### Why “notify then lease” (not “push full message”)?
- Keeps a single source of truth (the DB queue).
- Handles reconnects and missed events naturally (bot can call `lease` on startup).
- Avoids large payloads and simplifies backpressure.

### Server implementation notes
- Maintain per-backend subscriber registry in memory (works for one instance).
- For multi-instance scaling:
  - Use Redis pub/sub or a message broker to fan out events to all API instances.
  - Or pin each backend bot’s stream to a single instance via sticky sessions.

### Client changes
- Add `relayctl watch` command that:
  - Connects to `/v1/stream`
  - On event, runs the same processing loop as cron would (lease → handle → ack).

## Option B — Server Push via Webhook Callback (Bot Exposes HTTP Endpoint)
Best when backend bot is running behind an HTTP server (or can run one).

### Approach
- Configure each backend bot’s webhook URL + secret in the YAML config (no dynamic registration in v1):
  - `backend_bots[].webhook_url`, `backend_bots[].webhook_secret` (or `*_env` for secrets)
- On new pending delivery:
  - Server POSTs `{backend_bot_id, hint:"new_messages"}` to the webhook.
  - Bot then calls `lease` to retrieve messages (again: notify then lease).

### Reliability
- Server retries webhook calls with exponential backoff; stores webhook delivery attempts.
- Webhook must be idempotent; repeated notifications are fine.
- Still rely on queue leasing/ack for message processing reliability.

### Security
- Sign webhook payloads (HMAC) with timestamp + nonce to prevent replay.
- Allow IP allowlists if applicable.

## Option C — Server Triggers Bot Execution (Jobs/Tasks)
Best when backend bot should remain dormant and only run on demand.

### Approach
- Add an “executor” abstraction to the relay server:
  - For each backend bot, define how to start it:
    - `local_process`: run a command (simple but fragile; avoid for multi-tenant).
    - `docker_run`: start a container.
    - `k8s_job`: create a Kubernetes Job.
    - `lambda_invoke` / `cloud_run_job` / `ecs_task_run`: invoke serverless/managed compute.
- Store executor configuration in YAML (per backend bot), e.g.:
  - `backend_bots[].executor.type`
  - `backend_bots[].executor.*` (command, image, args, labels, etc.)
- On new pending delivery:
  - Enqueue a “run backend bot X” job (dedupe to avoid stampedes).
  - Executor starts the bot, passing configuration via env:
    - `RELAY_BASE_URL`, `RELAY_API_KEY`, maybe `RUN_ONCE=1`.
- Bot runs: `lease` → process → `ack` → exit.

### Dedupe & rate control (important)
- Maintain a `backend_bot_run` table:
  - track last run start/end, status, and “currently running” lock.
- Only trigger if not already running, or if last run is stale.
- Add a minimum interval between triggers to prevent thrash under high volume.

### Security boundaries
- Treat backend bots as untrusted code; run them in containers with restricted permissions.
- Avoid passing raw Discord tokens; backend only talks to relay via API key.

## Suggested Roadmap
1. Implement leasing + ack (server + client).
2. Add `relayctl watch` (SSE client) and `/v1/stream` (SSE server).
3. Add webhook callback support (optional alternative to SSE).
4. Add executor-based “run on message” (if you truly want on-demand compute).

## Operational Considerations
- **Backpressure**: if a backend bot is slow, deliveries remain pending/leased; don’t flood it with triggers.
- **Ordering**: decide whether per-channel/DM ordering matters; enforce by leasing oldest-first.
- **Observability**: metrics for pending depth, lease expiries, ack latency, webhook failures.
- **Multi-instance**: if scaling the API, use a shared pub/sub for push notifications or rely on bots polling `lease` periodically as a fallback.
