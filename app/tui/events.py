"""Textual messages posted from worker threads onto the UI."""
from __future__ import annotations

from textual.message import Message

from app.models.events import AgentEvent


class EventPosted(Message):
    """An AgentEvent arrived from the graph (worker thread -> UI thread)."""

    def __init__(self, event: AgentEvent) -> None:
        super().__init__()
        self.event = event


class RunFinished(Message):
    """A full graph run completed; carries the final AgentState subset."""

    def __init__(self, result: dict) -> None:
        super().__init__()
        self.result = result


class ApprovalNeeded(Message):
    def __init__(self, operation: str, description: str, step_id: str) -> None:
        super().__init__()
        self.operation = operation
        self.description = description
        self.step_id = step_id
