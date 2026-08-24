"""Session store: keeps conversational CAD context across requests."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Optional


class Session:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.requirements: dict[str, Any] | None = None
        self.request_history: list[str] = []
        self.summaries: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "requirements": self.requirements,
            "request_history": self.request_history,
            "summaries": self.summaries[-20:],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        s = cls(data["session_id"])
        s.requirements = data.get("requirements")
        s.request_history = data.get("request_history", [])
        s.summaries = data.get("summaries", [])
        return s


class SessionStore:
    def __init__(self, base_dir: Path) -> None:
        self.base = Path(base_dir) / "sessions"
        self.base.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Session] = {}

    @classmethod
    def default(cls) -> "SessionStore":
        from app.config import get_config
        return cls(get_config().data_dir)

    def new_session(self) -> Session:
        session = Session(uuid.uuid4().hex[:10])
        self._cache[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        if session_id in self._cache:
            return self._cache[session_id]
        path = self.base / f"{session_id}.json"
        if path.exists():
            session = Session.from_dict(json.loads(path.read_text()))
        else:
            session = Session(session_id)
        self._cache[session.session_id] = session
        return session

    def save(self, session: Session) -> None:
        path = self.base / f"{session.session_id}.json"
        path.write_text(json.dumps(session.to_dict(), indent=2, default=str))
