"""Simple CLI for interacting with the relay REST API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional

import httpx

from relay_server.config import ConfigError, LoadedConfig, load_config

EXIT_SUCCESS = 0
EXIT_USAGE = 2
EXIT_AUTH = 10
EXIT_NETWORK = 20
EXIT_SERVER = 30


class CLIError(Exception):
    """Custom exception to control the exit code from the CLI."""

    def __init__(self, message: str, exit_code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class ConnectionSettings:
    """Resolved connection metadata for the REST client."""

    base_url: str
    api_key: str
    backend_id: str


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="relayctl", description="Relay REST client.")
    parser.add_argument(
        "--config",
        help="Path to relay config YAML (falls back to RELAY_CONFIG).",
    )
    parser.add_argument(
        "--backend-id",
        help="Backend bot ID (can also be set via RELAY_BACKEND_ID).",
    )
    parser.add_argument(
        "--base-url",
        help="Relay server base URL (falls back to RELAY_BASE_URL or config.server.base_url).",
    )
    parser.add_argument(
        "--api-key",
        help="API key for the backend bot (falls back to RELAY_API_KEY or config backend).",
    )
    parser.add_argument(
        "--timeout",
        type=_parse_positive_float,
        default=10.0,
        help="Request timeout in seconds (default 10).",
    )
    json_group = parser.add_mutually_exclusive_group()
    json_group.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output JSON (default).",
    )
    json_group.add_argument(
        "--no-json",
        dest="json_output",
        action="store_false",
        help="Disable JSON output in favor of human-readable summaries.",
    )
    parser.set_defaults(json_output=True)
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational logging on stderr.",
    )
    parser.add_argument(
        "--request-id",
        help="Optional request ID forwarded via X-Request-Id.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    _build_retrieve_parser(subparsers)
    _build_send_parser(subparsers)
    _build_lease_parser(subparsers)
    _build_ack_parser(subparsers)
    _build_nack_parser(subparsers)
    return parser.parse_args(argv)


def _build_retrieve_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[arg-type]
    parser = subparsers.add_parser("retrieve", help="Fetch pending bot messages.")
    parser.add_argument(
        "--limit",
        type=_parse_limit,
        default=50,
        help="Maximum number of pending messages (1-100, default 50).",
    )
    parser.set_defaults(command="retrieve")


def _build_send_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[arg-type]
    parser = subparsers.add_parser(
        "send", help="Send a message on behalf of a backend bot."
    )
    parser.add_argument(
        "--discord-bot-id",
        required=True,
        help="Discord bot ID to use when delivering the message.",
    )
    destination_group = parser.add_mutually_exclusive_group(required=True)
    destination_group.add_argument("--dm-user-id", help="DM user ID.")
    destination_group.add_argument("--channel-id", help="Channel ID.")
    parser.add_argument(
        "--content",
        required=True,
        help="Message text to deliver.",
    )
    parser.add_argument("--reply-to", help="Discord message ID to reply to.")
    parser.set_defaults(command="send")


def _build_lease_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[arg-type]
    parser = subparsers.add_parser(
        "lease", help="Lease pending messages for processing."
    )
    parser.add_argument(
        "--limit",
        type=_parse_limit,
        default=50,
        help="Maximum number of messages to lease (1-100, default 50).",
    )
    parser.add_argument(
        "--lease-seconds",
        type=_parse_lease_seconds,
        default=300,
        help="Lease duration in seconds (1-3600, default 300).",
    )
    parser.add_argument(
        "--include-history",
        action="store_true",
        help="Include conversation history in the response.",
    )
    parser.add_argument(
        "--history-limit",
        type=_parse_history_limit,
        default=20,
        help="Maximum conversation history messages (1-100, default 20).",
    )
    parser.set_defaults(command="lease")


def _build_ack_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[arg-type]
    parser = subparsers.add_parser(
        "ack", help="Acknowledge successful processing of leased messages."
    )
    parser.add_argument(
        "--delivery-ids",
        required=True,
        nargs="+",
        help="Delivery IDs to acknowledge.",
    )
    parser.add_argument(
        "--lease-id",
        required=True,
        help="Lease ID for the deliveries being acknowledged.",
    )
    parser.set_defaults(command="ack")


def _build_nack_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[arg-type]
    parser = subparsers.add_parser(
        "nack", help="Negative acknowledge failed processing of leased messages."
    )
    parser.add_argument(
        "--delivery-ids",
        required=True,
        nargs="+",
        help="Delivery IDs to nack.",
    )
    parser.add_argument(
        "--lease-id",
        required=True,
        help="Lease ID for the deliveries being nacked.",
    )
    parser.add_argument(
        "--reason",
        help="Optional reason for the nack.",
    )
    parser.set_defaults(command="nack")


def _parse_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not (1 <= limit <= 100):
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return limit


def _parse_positive_float(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive")
    return timeout


def _parse_lease_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("lease-seconds must be an integer") from exc
    if not (1 <= seconds <= 3600):
        raise argparse.ArgumentTypeError("lease-seconds must be between 1 and 3600")
    return seconds


def _parse_history_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("history-limit must be an integer") from exc
    if not (1 <= limit <= 100):
        raise argparse.ArgumentTypeError("history-limit must be between 1 and 100")
    return limit


def resolve_connection(
    args: argparse.Namespace,
    *,
    config_loader: Callable[[str], LoadedConfig] | None = None,
) -> ConnectionSettings:
    """Determine base URL and API key from args, env, or config."""

    if config_loader is None:
        config_loader = load_config

    config_path = args.config or os.getenv("RELAY_CONFIG")
    loaded = None
    if config_path:
        try:
            loaded = config_loader(config_path)
        except ConfigError as exc:
            raise CLIError(f"Failed to load config: {exc}") from exc

    backend_id = args.backend_id or os.getenv("RELAY_BACKEND_ID")
    if loaded and not backend_id:
        raise CLIError("--backend-id is required when --config is provided")
    if not backend_id:
        raise CLIError("Backend ID is required (--backend-id or RELAY_BACKEND_ID)")

    base_url = args.base_url or os.getenv("RELAY_BASE_URL")
    api_key = args.api_key or os.getenv("RELAY_API_KEY")

    if loaded:
        server_url = loaded.data.server.base_url
        if server_url:
            base_url = base_url or server_url
        backend = next(
            (bot for bot in loaded.data.backend_bots if bot.id == backend_id),
            None,
        )
        if not backend:
            raise CLIError(f"Backend '{backend_id}' not found in config")
        if not api_key:
            try:
                api_key = backend.resolved_api_key()
            except ConfigError as exc:
                raise CLIError(str(exc)) from exc

    base_url = (base_url or "http://127.0.0.1:8080").rstrip("/")
    if not base_url:
        raise CLIError("Base URL cannot be empty")
    if not api_key:
        raise CLIError(
            "API key is required (--api-key, RELAY_API_KEY, or backend config api_key)"
        )

    return ConnectionSettings(base_url=base_url, api_key=api_key, backend_id=backend_id)


def _build_headers(
    settings: ConnectionSettings, request_id: Optional[str]
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {settings.api_key}"}
    if request_id:
        headers["X-Request-Id"] = request_id
    return headers


def _log(message: str, *, quiet: bool) -> None:
    if not quiet:
        print(message, file=sys.stderr)


def _handle_response_error(response: httpx.Response) -> int:
    status = response.status_code
    detail = _extract_error_detail(response)
    msg = f"Request failed ({status}): {detail}"
    print(msg, file=sys.stderr)
    if status in (401, 403):
        return EXIT_AUTH
    if 500 <= status < 600:
        return EXIT_SERVER
    return EXIT_USAGE


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, Mapping):
            return data.get("detail") or json.dumps(data)
        return str(data)
    except ValueError:
        return response.text.strip() or response.reason_phrase


def _print_json(value: object, pretty: bool) -> None:
    dump = json.dumps(value, indent=2 if pretty else None)
    sys.stdout.write(dump + "\n")


def _print_human_retrieve(messages: list[Mapping[str, object]]) -> None:
    if not messages:
        print("No pending messages.")
        return
    for message in messages:
        delivery_id = message.get("delivery_id")
        discord = message.get("discord_message") or {}
        content = discord.get("content")
        author = discord.get("source", {}).get("author_name", "unknown")
        print(f"- {delivery_id}: {content!r} (from {author})")


def _print_human_send(payload: Mapping[str, object]) -> None:
    discord_id = payload.get("discord_message_id")
    channel_id = payload.get("channel_id", "n/a")
    print(f"Sent {discord_id} (channel={channel_id})")


def _print_human_lease(payload: Mapping[str, object]) -> None:
    messages = payload.get("messages", [])
    if not messages:
        print("No messages leased.")
        return
    history = payload.get("conversation_history", [])
    print(f"Leased {len(messages)} message(s)")
    if history:
        print(f" (with {len(history)} conversation history message(s))")
    else:
        print()
    for message in messages:
        delivery_id = message.get("delivery_id")
        discord = message.get("discord_message") or {}
        content = discord.get("content")
        author = discord.get("source", {}).get("author_name", "unknown")
        lease_id = message.get("lease_id")
        expires = message.get("lease_expires_at")
        print(
            f"- {delivery_id}: {content!r} (from {author}, lease={lease_id}, expires={expires})"
        )


def _print_human_ack(payload: Mapping[str, object]) -> None:
    acknowledged = payload.get("acknowledged_count", 0)
    print(f"Acknowledged {acknowledged} message(s)")


def _print_human_nack(payload: Mapping[str, object]) -> None:
    nacked = payload.get("nacked_count", 0)
    print(f"Nacked {nacked} message(s)")


def _handle_request_exception(exc: httpx.RequestError) -> int:
    print(f"Request error: {exc}", file=sys.stderr)
    return EXIT_NETWORK


def _run_command(args: argparse.Namespace, settings: ConnectionSettings) -> int:
    headers = _build_headers(settings, args.request_id)
    try:
        with httpx.Client(base_url=settings.base_url, timeout=args.timeout) as client:
            if args.command == "retrieve":
                _log(
                    f"Retrieving up to {args.limit} pending messages...",
                    quiet=args.quiet,
                )
                response = client.get(
                    "/v1/messages/pending",
                    headers=headers,
                    params={"limit": args.limit},
                )
                if response.is_success:
                    data = response.json()
                    messages = data.get("messages", [])
                    if args.json_output:
                        _print_json(messages, args.pretty)
                    else:
                        _print_human_retrieve(messages)
                    return EXIT_SUCCESS
                return _handle_response_error(response)

            if args.command == "send":
                destination: dict[str, object] = {"type": "dm"}
                if args.dm_user_id:
                    destination = {"type": "dm", "user_id": args.dm_user_id}
                elif args.channel_id:
                    destination = {"type": "channel", "channel_id": args.channel_id}
                payload: dict[str, object] = {
                    "discord_bot_id": args.discord_bot_id,
                    "destination": destination,
                    "content": args.content,
                }
                if getattr(args, "reply_to", None):
                    payload["reply_to_discord_message_id"] = args.reply_to
                _log(
                    f"Sending message via {args.discord_bot_id} to {destination['type']}",
                    quiet=args.quiet,
                )
                response = client.post(
                    "/v1/messages/send",
                    headers=headers,
                    json=payload,
                )
                if response.is_success:
                    data = response.json()
                    if args.json_output:
                        _print_json(data, args.pretty)
                    else:
                        _print_human_send(data)
                    return EXIT_SUCCESS
                return _handle_response_error(response)

            if args.command == "lease":
                payload = {
                    "limit": args.limit,
                    "lease_seconds": args.lease_seconds,
                    "include_conversation_history": args.include_history,
                    "conversation_history_limit": args.history_limit,
                }
                _log(f"Leasing up to {args.limit} messages...", quiet=args.quiet)
                response = client.post(
                    "/v1/messages/lease",
                    headers=headers,
                    json=payload,
                )
                if response.is_success:
                    data = response.json()
                    if args.json_output:
                        _print_json(data, args.pretty)
                    else:
                        _print_human_lease(data)
                    return EXIT_SUCCESS
                return _handle_response_error(response)

            if args.command == "ack":
                payload = {
                    "delivery_ids": args.delivery_ids,
                    "lease_id": args.lease_id,
                }
                _log(
                    f"Acknowledging {len(args.delivery_ids)} message(s)...",
                    quiet=args.quiet,
                )
                response = client.post(
                    "/v1/messages/ack",
                    headers=headers,
                    json=payload,
                )
                if response.is_success:
                    data = response.json()
                    if args.json_output:
                        _print_json(data, args.pretty)
                    else:
                        _print_human_ack(data)
                    return EXIT_SUCCESS
                return _handle_response_error(response)

            if args.command == "nack":
                payload = {
                    "delivery_ids": args.delivery_ids,
                    "lease_id": args.lease_id,
                }
                if args.reason:
                    payload["reason"] = args.reason
                _log(
                    f"Nacking {len(args.delivery_ids)} message(s)...", quiet=args.quiet
                )
                response = client.post(
                    "/v1/messages/nack",
                    headers=headers,
                    json=payload,
                )
                if response.is_success:
                    data = response.json()
                    if args.json_output:
                        _print_json(data, args.pretty)
                    else:
                        _print_human_nack(data)
                    return EXIT_SUCCESS
                return _handle_response_error(response)

            print("Unknown command.", file=sys.stderr)
            return EXIT_USAGE
    except httpx.TimeoutException as exc:
        return _handle_request_exception(exc)
    except httpx.RequestError as exc:
        return _handle_request_exception(exc)


def _run(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = resolve_connection(args)
    except CLIError as exc:
        print(exc, file=sys.stderr)
        return exc.exit_code
    return _run_command(args, settings)


def main(argv: Iterable[str] | None = None) -> None:
    raise SystemExit(_run(argv))


if __name__ == "__main__":
    main()
