"""Human-in-the-loop approval gate.

LangGraph state must stay serializable, so pending approvals live in a
process-wide manager keyed by session id. The executor node blocks on a
threading.Event until the TUI answers (/approve or /reject).
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PendingApproval(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    session_id: str
    step_id: str = ""
    operation: str = ""
    description: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    resolved: bool = False
    approved: bool = False
    timed_out: bool = False


class ApprovalManager:
    _instance: Optional["ApprovalManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._pending: dict[str, tuple[PendingApproval, threading.Event]] = {}

    @classmethod
    def get(cls) -> "ApprovalManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def request(self, session_id: str, operation: str, description: str,
                step_id: str = "", timeout_s: float = 600.0) -> PendingApproval:
        event = threading.Event()
        req = PendingApproval(session_id=session_id, operation=operation,
                              description=description, step_id=step_id)
        self._pending[session_id] = (req, event)
        try:
            signaled = event.wait(timeout=timeout_s)
            if not signaled:
                req.timed_out = True
        finally:
            self._pending.pop(session_id, None)
            req.resolved = True
        return req

    def respond(self, session_id: str, approved: bool) -> bool:
        entry = self._pending.get(session_id)
        if not entry:
            return False
        req, event = entry
        req.approved = approved
        event.set()
        return True

    def pending(self, session_id: str) -> Optional[PendingApproval]:
        entry = self._pending.get(session_id)
        return entry[0] if entry else None
