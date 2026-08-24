"""TUI widgets: status header and agent pipeline panel."""
from __future__ import annotations

from textual.widgets import Static

from app.models.events import AgentEvent

NODE_LABELS = {
    "runtime": "Fusion MCP",
    "requirements": "Requirements",
    "planner": "CAD Planner",
    "review": "Design Review",
    "executor": "Executor",
    "inspector": "Inspector",
    "repair": "Repair",
    "finish": "Finish",
}
PIPELINE = ["runtime", "requirements", "planner", "review", "executor",
            "inspector", "repair", "finish"]


class StatusBar(Static):
    """Top panel: connection + agent status."""

    def update_status(self, fusion: str, mcp: str, agent: str) -> None:
        dot = lambda s: "[green]●[/]" if s == "connected" else (
            "[yellow]◐[/]" if s in ("running", "working") else
            ("[red]○[/]" if s == "disconnected" else f"[cyan]●[/] {s}"))
        fusion_line = f"Fusion 360: {dot(fusion)}"
        if fusion not in ("connected", "disconnected"):
            fusion_line = f"Fusion 360: [cyan]●[/] {fusion}"
        mcp_line = f"MCP:        {dot(mcp)}"
        if mcp not in ("connected", "disconnected"):
            mcp_line = f"MCP:        [cyan]●[/] {mcp}"
        self.update(
            f"{fusion_line}\n"
            f"{mcp_line}\n"
            f"Agent:      [cyan]●[/] {agent}"
        )


class PipelinePanel(Static):
    """Live per-node status list."""

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self.node_states: dict[str, str] = {}

    def mark(self, node: str, state: str) -> None:
        self.node_states[node] = state
        lines = []
        for n in PIPELINE:
            label = NODE_LABELS[n]
            s = self.node_states.get(n)
            if s == "running":
                icon = "[cyan]⟳[/]"
            elif s == "done":
                icon = "[green]✓[/]"
            elif s == "error":
                icon = "[red]✗[/]"
            elif s == "waiting":
                icon = "[yellow]⏸[/]"
            else:
                icon = " "
            lines.append(f" {icon} {label:<14}{self._suffix(n)}")
        self.update("\n".join(lines))

    def _suffix(self, node: str) -> str:
        s = self.node_states.get(node)
        if s == "running":
            return "[dim] working...[/]"
        if s == "waiting":
            return "[yellow] awaiting approval[/]"
        if s == "error":
            return "[red] failed[/]"
        return ""

    def reset_run(self) -> None:
        self.node_states = {}
        self.mark("none", "")
