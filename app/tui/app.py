"""The Fusion AI CAD Agent TUI (Textual)."""
from __future__ import annotations

import asyncio
import threading

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, RichLog

from app.config import get_config
from app.models.events import AgentEvent, EventBus, EventType
from app.fusion.mcp_client import create_bridge, MockFusionClient
from app.graph.approvals import ApprovalManager
from app.graph.graph import build_graph
from app.graph.state import initial_state
from app.storage.history import HistoryStore
from app.storage.sessions import Session, SessionStore
from app.tui.events import ApprovalNeeded, EventPosted, RunFinished
from app.tui.widgets.panels import PipelinePanel, StatusBar

EVENT_STYLE = {
    EventType.STATUS: ("dim white", "::"),
    EventType.TOOL_CALL: ("bold cyan", "→"),
    EventType.TOOL_RESULT: ("cyan", "←"),
    EventType.INSPECTION: ("magenta", "?"),
    EventType.APPROVAL: ("yellow", "!"),
    EventType.WARNING: ("yellow", "▲"),
    EventType.ERROR: ("bold red", "✗"),
    EventType.SUMMARY: ("bold green", "★"),
    EventType.USER: ("bright_white", "❯"),
}


class CadAgentApp(App):
    TITLE = "FUSION AI CAD AGENT"
    CSS = """
    #statusbar {
        height: 5;
        border: round $accent;
        padding: 0 2;
        background: $surface;
    }
    #pipeline {
        height: 12;
        border: round $primary;
        padding: 0 2;
        background: $surface;
    }
    #log {
        border: round $panel;
        padding: 0 1;
    }
    #prompt {
        dock: bottom;
        border: tall $accent;
    }
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear_log", "Clear", show=False),
    ]

    def __init__(self, config=None) -> None:
        super().__init__()
        self.cfg = config or get_config()
        self.session: Session | None = None
        self.bridge = None
        self._compiled_graph = None
        self._busy = threading.Event()
        self._unsub = None

    # ---- layout ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield StatusBar(id="statusbar")
            yield PipelinePanel(id="pipeline")
            yield RichLog(id="log", wrap=True, highlight=False, markup=True)
        yield Input(placeholder="Describe the part to model  (or /help /model /history /status …)",
                    id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        log.write("[bold cyan]FUSION AI CAD AGENT[/] — LangGraph orchestration + Fusion MCP")
        if not self.cfg.llm_available:
            log.write("[yellow]No OPENAI_API_KEY set — running in offline heuristic mode.[/]")
        mode_note = {
            "mock": "[yellow]mock bridge[/] (built-in simulation — FUSION_AI_MODE=faust for real Fusion)",
            "faust": f"[green]faust-machines socket:[/] {escape(self.cfg.mcp_command or 'uvx fusion360-mcp-server --mode socket')}",
            "faust-mock": f"[green]faust-machines mock:[/] {escape(self.cfg.mcp_command or 'uvx fusion360-mcp-server --mode mock')}",
            "stdio": f"[green]stdio:[/] {escape(self.cfg.mcp_command)}",
            "http": f"[green]http:[/] {escape(self.cfg.mcp_url)}",
        }.get(self.cfg.fusion_mode.lower(), self.cfg.fusion_mode)
        log.write(f"Bridge: {mode_note}")
        log.write("Type a CAD request, or /help for commands.\n")

        self.session = SessionStore.default().new_session()
        self.bridge = create_bridge(self.cfg)
        try:
            self.bridge.config = self.cfg  # carried into graph for limits
        except Exception:
            pass

        statusbar = self.query_one("#statusbar", StatusBar)
        fusion_status = "connected" if isinstance(self.bridge, MockFusionClient) \
            else f"{self.cfg.fusion_mode} configured"
        statusbar.update_status(fusion_status, "disconnected", "idle")
        self.query_one("#pipeline", PipelinePanel).mark("none", "")

        self._unsub = EventBus.get().subscribe(
            lambda ev: self.call_from_thread(self._on_event_threadsafe, ev)
        )
        self.query_one("#prompt", Input).focus()

    async def on_unmount(self) -> None:
        if self._unsub:
            self._unsub()
        if self.bridge is not None:
            try:
                await self.bridge.disconnect()
            except Exception:
                pass

    # ---- event marshalling ----------------------------------------------

    def _on_event_threadsafe(self, event: AgentEvent) -> None:
        self.post_message(EventPosted(event))

    def on_event_posted(self, message: EventPosted) -> None:
        event = message.event
        style, icon = EVENT_STYLE.get(event.type, ("white", "::"))
        log = self.query_one("#log", RichLog)
        line = Text()
        line.append(f"[{event.clock()}] ", style="dim")
        line.append(f"{icon} ", style=style)
        line.append(f"{event.node:<12}", style="bold")
        chunks = event.message.split("\n")
        line.append(" " + chunks[0], style=style)
        for chunk in chunks[1:]:
            line.append("\n             " + chunk, style=style)
        log.write(line)

        pipeline = self.query_one("#pipeline", PipelinePanel)
        node = event.node
        if event.type == EventType.STATUS and node != "finish":
            pipeline.mark(node, "running")
        elif event.type in (EventType.TOOL_CALL, EventType.TOOL_RESULT) and node == "executor":
            pipeline.mark(node, "running")
        elif event.type == EventType.APPROVAL:
            pipeline.mark(node, "waiting")
            details = event.details or {}
            self.post_message(ApprovalNeeded(
                details.get("operation", ""), event.message, details.get("step_id", "")))
        elif event.type == EventType.WARNING and node in ("review",):
            pipeline.mark(node, "running")

        statusbar = self.query_one("#statusbar", StatusBar)
        if event.node == "runtime" and event.type == EventType.STATUS and "Discovered" in event.message:
            statusbar.update_status("connected", "connected", "ready")

    def on_approval_needed(self, message: ApprovalNeeded) -> None:
        log = self.query_one("#log", RichLog)
        log.write(Text(f"⚠ APPROVAL REQUIRED: {message.operation}\n"
                       f"  {message.description}\n"
                       "  Type /approve or /reject", style="bold yellow"))

    # ---- input handling ---------------------------------------------------

    async def on_input_submitted(self, message: Input.Submitted) -> None:
        text = message.value.strip()
        self.query_one("#prompt", Input).value = ""
        if not text:
            return
        log = self.query_one("#log", RichLog)

        if text.startswith("/"):
            await self._handle_command(text, log)
            return

        if self._busy.is_set():
            log.write(Text("⏳ A run is already in progress — wait or /pause.", style="yellow"))
            return

        log.write(Text(f"\n❯ {text}", style="bold bright_white"))
        self.session.request_history.append(text)
        self._run_agent(text)

    @work(thread=True, exclusive=False)
    def _run_agent(self, request: str) -> None:
        """Run one full graph invocation on a worker thread."""
        self._busy.set()
        bus = EventBus.get()
        try:
            if self._compiled_graph is None:
                self._compiled_graph = build_graph(self.cfg)

            state = initial_state(
                session_id=self.session.session_id,
                user_request=request,
                prior_requirements=self.session.requirements,
            )
            state["bridge"] = self.bridge

            final = asyncio.run(self._compiled_graph.ainvoke(state))

            # Persist conversational context.
            if final.get("requirements"):
                self.session.requirements = final["requirements"]
            if final.get("final_summary"):
                self.session.summaries.append(final["final_summary"])
            SessionStore.default().save(self.session)

            statusbar = self.query_one("#statusbar", StatusBar)
            self.call_from_thread(
                statusbar.update_status,
                "connected" if self.bridge.connected else "disconnected",
                "connected", final.get("status", "done"),
            )
            pipeline = self.query_one("#pipeline", PipelinePanel)
            ok = final.get("status") == "success"
            self.call_from_thread(pipeline.mark, "inspector", "done" if ok else "error")
            self.call_from_thread(pipeline.mark, "finish", "done" if ok else "error")
            self.call_from_thread(lambda: self.query_one("#log", RichLog).write(""))
        except Exception as exc:
            bus.emit("system", EventType.ERROR, f"Run crashed: {exc}")
        finally:
            self._busy.clear()

    # ---- slash commands ----------------------------------------------------

    async def _handle_command(self, text: str, log: RichLog) -> None:
        cmd, *args = text.split(maxsplit=1)
        approvals = ApprovalManager.get()

        if cmd in ("/approve", "/reject"):
            approved = cmd == "/approve"
            ok = approvals.respond(self.session.session_id, approved)
            log.write(Text(f"{'✓ Approved' if approved else '✗ Rejected'}"
                           if ok else "No pending approval.",
                           style="green" if approved else "red"))

        elif cmd == "/pause":
            log.write(Text("Pause/resume: approval gates act as natural pause points; "
                           "a run between gates cannot be paused safely.", style="dim"))

        elif cmd == "/resume":
            log.write(Text("Nothing to resume — no paused gate." if not approvals.pending(
                self.session.session_id) else "Pending approval awaiting /approve or /reject.",
                style="dim"))

        elif cmd == "/retry":
            if self.session.request_history:
                last = self.session.request_history[-1]
                log.write(Text(f"Retrying: {last}", style="cyan"))
                if not self._busy.is_set():
                    self._run_agent(last)
            else:
                log.write(Text("Nothing to retry.", style="dim"))

        elif cmd == "/undo":
            if self.session.requirements:
                rev = self.session.requirements.get("revision", 0)
                if rev > 0:
                    self.session.requirements["revision"] = rev - 1
                    log.write(Text(f"Reverted design context to revision {rev - 1} "
                                   "(Fusion undo via Ctrl+Z in-app for geometry).",
                                   style="yellow"))
                else:
                    self.session.requirements = None
                    log.write(Text("Cleared design context.", style="yellow"))
            else:
                log.write(Text("Nothing to undo.", style="dim"))

        elif cmd == "/status":
            reqs = self.session.requirements
            if reqs:
                from app.models.requirements import CADRequirements
                log.write(Text.escape(CADRequirements(**reqs).to_prompt()))
            else:
                log.write(Text("No active design. Describe a part to begin.", style="dim"))

        elif cmd == "/model":
            runs = HistoryStore.default().list_runs()
            if runs:
                records = HistoryStore.default().load_run(runs[0])
                for r in records:
                    if r["kind"] == "inspection":
                        log.write(Text.escape(str(r)))
                        break
                else:
                    log.write(Text("Latest run has no inspection record.", style="dim"))
            else:
                log.write(Text("No runs recorded yet.", style="dim"))

        elif cmd == "/history":
            store = HistoryStore.default()
            runs = store.list_runs()[:10]
            if not runs:
                log.write(Text("No history yet.", style="dim"))
            for path in runs:
                records = store.load_run(path)
                status = next((r.get("status") for r in reversed(records) if r["kind"] == "run_end"), "?")
                first = next((r for r in records if r["kind"] == "run_start"), {})
                requests = (first.get("requests") or ["?"])[0]
                log.write(Text.escape(f"{path.stem}  [{status}]  {requests[:70]}"))

        elif cmd == "/help":
            log.write(Text(
                "Commands:\n"
                "  /approve /reject   answer a pending destructive-op approval\n"
                "  /retry             re-run your last request\n"
                "  /undo              revert design-context revision\n"
                "  /status            show current structured requirements\n"
                "  /model             latest model inspection report\n"
                "  /history           list persisted run history\n"
                "  /help              this help\n"
                "Anything else is treated as a CAD request (follow-ups keep context).",
                style="cyan"))
        else:
            log.write(Text(f"Unknown command {cmd} — /help lists commands.", style="red"))

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()


def run_tui(config=None) -> None:
    CadAgentApp(config).run()
