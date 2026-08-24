"""Entry point.

    fusion-ai                     launch the TUI (mock Fusion by default)
    fusion-ai --mode stdio        attach to a real Fusion MCP server subprocess
    fusion-ai --headless "..."    one-shot run, events printed to stdout
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import get_config
from app.models.events import EventBus, EventType
from app.graph.graph import build_graph
from app.graph.state import initial_state
from app.fusion.mcp_client import create_bridge
from app.storage.history import HistoryStore
from app.storage.sessions import SessionStore


def cli() -> None:
    parser = argparse.ArgumentParser(prog="fusion-ai",
                                     description="Terminal AI CAD agent for Fusion 360 (via MCP)")
    parser.add_argument("--mode", choices=["mock", "faust", "faust-mock", "stdio", "http"],
                        help="Fusion bridge mode (default from FUSION_AI_MODE, else mock). "
                             "'faust' = faust-machines fusion360-mcp-server in socket mode; "
                             "'faust-mock' = same server in its own mock mode (no Fusion needed)")
    parser.add_argument("--mcp-command", help="command to launch an stdio MCP server")
    parser.add_argument("--mcp-url", help="streamable-HTTP MCP endpoint URL")
    parser.add_argument("--headless", metavar="REQUEST",
                        help="run a single request non-interactively and exit")
    parser.add_argument("--simulate-failures", action="store_true",
                        help="mock bridge: inject one deterministic failure (exercises repair loop)")
    args = parser.parse_args()

    config = get_config(
        fusion_mode=args.mode,
        mcp_command=args.mcp_command,
        mcp_url=args.mcp_url,
        simulate_failures=True if args.simulate_failures else None,
    )

    if args.headless:
        sys.exit(run_headless(config, args.headless))
    from app.tui.app import run_tui
    run_tui(config)


def run_headless(config, request: str) -> int:
    async def _run() -> tuple[int, str]:
        store = SessionStore.default()
        session = store.new_session()
        bridge = create_bridge(config)
        try:
            bridge.config = config  # type: ignore[attr-defined]
        except Exception:
            pass

        graph = build_graph(config)
        state = initial_state(session.session_id, request)
        state["bridge"] = bridge

        def print_event(event) -> None:
            style = {
                EventType.ERROR: "\033[31m",
                EventType.WARNING: "\033[33m",
                EventType.SUMMARY: "\033[32m",
            }.get(event.type, "")
            reset = "\033[0m" if style else ""
            print(f"{style}{event.line()}{reset}")

        unsub = EventBus.get().subscribe(print_event)
        try:
            final = await graph.ainvoke(state)
            if final.get("requirements"):
                session.requirements = final["requirements"]
                store.save(session)
            return (0 if final.get("status") == "success" else 1), \
                final.get("final_summary") or ""
        finally:
            unsub()
            try:
                await bridge.disconnect()
            except Exception:
                pass

    code, summary = asyncio.run(_run())
    if summary:
        print("\n" + summary)
    HistoryStore.default()  # ensure dirs exist
    return code


if __name__ == "__main__":
    cli()
