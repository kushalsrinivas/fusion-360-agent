"""Structured agent events + a thread-safe event bus connecting LangGraph nodes to the TUI."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field


class EventType(str, Enum):
    STATUS = "status"            # node lifecycle (started / finished)
    TOOL_CALL = "tool_call"      # executor -> MCP
    TOOL_RESULT = "tool_result"  # MCP result
    INSPECTION = "inspection"
    APPROVAL = "approval"
    WARNING = "warning"
    ERROR = "error"
    SUMMARY = "summary"
    USER = "user"


class AgentEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    ts: datetime = Field(default_factory=datetime.now)
    node: str = "system"
    type: EventType = EventType.STATUS
    message: str = ""
    details: Optional[dict[str, Any]] = None

    def clock(self) -> str:
        return self.ts.strftime("%H:%M:%S")

    def line(self) -> str:
        """Single-line rendering used by headless mode and history export."""
        icon = {
            EventType.STATUS: "::",
            EventType.TOOL_CALL: "->",
            EventType.TOOL_RESULT: "<-",
            EventType.INSPECTION: "??",
            EventType.APPROVAL: "!!",
            EventType.WARNING: "/\\",
            EventType.ERROR: "XX",
            EventType.SUMMARY: "==",
            EventType.USER: ">>",
        }[self.type]
        base = f"[{self.clock()}] {self.node:<12} {icon} {self.message}"
        if self.details:
            compact = {k: v for k, v in self.details.items() if v is not None}
            if compact:
                base += f"  {compact}"
        return base


Subscriber = Callable[[AgentEvent], None]


class EventBus:
    """Process-wide publish/subscribe channel.

    Graph nodes run in worker threads; the Textual app subscribes and marshals
    updates onto the UI thread via ``app.call_from_thread``.
    """

    _instance: Optional["EventBus"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._mutex = threading.Lock()

    @classmethod
    def get(cls) -> "EventBus":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        with self._mutex:
            self._subs.append(fn)

        def unsubscribe() -> None:
            with self._mutex:
                if fn in self._subs:
                    self._subs.remove(fn)

        return unsubscribe

    def emit(
        self,
        node: str,
        type_: EventType,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> AgentEvent:
        event = AgentEvent(node=node, type=type_, message=message, details=details)
        with self._mutex:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(event)
            except Exception:  # never let a broken subscriber kill a graph node
                pass
        return event


def emit_status(node: str, msg: str, **details: Any) -> AgentEvent:
    return EventBus.get().emit(node, EventType.STATUS, msg, details or None)


def emit_error(node: str, msg: str, **details: Any) -> AgentEvent:
    return EventBus.get().emit(node, EventType.ERROR, msg, details or None)


def format_elapsed(seconds: float) -> str:
    return str(timedelta(seconds=int(seconds)))
