import argparse
import asyncio
import math
import os
import secrets
import shutil

import uvicorn

from a2a_local.backends import (
    AGENTS,
    EXECUTABLES,
    Backend,
    BackendError,
    CLIBackend,
    ScriptedBackend,
)
from a2a_local.coordinator import DEFAULT_TOPIC, DiscussionError, Turn, discuss
from a2a_local.runtime import local_agents
from a2a_local.server import create_app


def print_turn(turn: Turn) -> None:
    print(f"\n--- {turn.agent} | A2A task {turn.task_id} ---\n{turn.text}", flush=True)


async def run_discussion(topic: str, *, live: bool, timeout: float) -> None:
    token = secrets.token_urlsafe(32)
    backends: dict[str, Backend] = {
        agent: CLIBackend(agent, timeout) if live else ScriptedBackend(agent)
        for agent in AGENTS
    }
    mode = "LIVE CLIs (provider charges may apply)" if live else "SCRIPTED demo"
    print(f"Mode: {mode}", flush=True)
    async with local_agents(backends, token) as endpoints:
        for agent, endpoint in endpoints.items():
            print(f"{agent}: {endpoint}", flush=True)
        await discuss(
            topic, endpoints, token=token, timeout=timeout, on_turn=print_turn
        )
    print("\nDiscussion completed. All local A2A servers stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Three local CLI agents interacting over A2A 1.0 JSON-RPC."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="Run the full three-agent discussion.")
    run_parser.add_argument("--topic", default=DEFAULT_TOPIC)
    serve_parser = commands.add_parser("serve", help="Serve one local A2A agent.")
    serve_parser.add_argument("--agent", choices=AGENTS, required=True)
    serve_parser.add_argument("--port", type=int, default=9101)
    for subparser in (run_parser, serve_parser):
        subparser.add_argument(
            "--live",
            action="store_true",
            help="Invoke real CLIs, not scripted replies.",
        )
        subparser.add_argument("--timeout", type=float, default=120)
    commands.add_parser("doctor", help="Check executables without model requests.")
    args = parser.parse_args()
    if args.command == "doctor":
        missing = False
        for agent, executable in EXECUTABLES.items():
            path = shutil.which(executable)
            print(f"{agent}: {path or 'MISSING'}")
            missing |= path is None
        if missing:
            raise SystemExit(1)
        return
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    if args.command == "run":
        if not args.topic.strip():
            parser.error("--topic must not be blank")
        try:
            asyncio.run(
                run_discussion(args.topic, live=args.live, timeout=args.timeout)
            )
        except DiscussionError as error:
            parser.exit(1, f"Discussion failed: {error}\n")
        return
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    token = os.environ.get("A2A_LOCAL_TOKEN", "")
    if len(token) < 16:
        parser.error(
            "Export A2A_LOCAL_TOKEN with at least 16 characters for serve mode"
        )
    backend: Backend = (
        CLIBackend(args.agent, args.timeout)
        if args.live
        else ScriptedBackend(args.agent)
    )
    url = f"http://127.0.0.1:{args.port}"
    try:
        uvicorn.run(
            create_app(args.agent, backend, url, token),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=2,
        )
    except BackendError as error:
        parser.exit(1, f"Agent failed: {error}\n")


if __name__ == "__main__":
    main()
