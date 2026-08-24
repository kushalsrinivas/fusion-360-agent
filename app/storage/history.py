"""Persisted execution history (JSONL per run) under the data dir."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class HistoryStore:
    def __init__(self, base_dir: Path) -> None:
        self.base = Path(base_dir) / "history"
        self.base.mkdir(parents=True, exist_ok=True)

    @classmethod
    def default(cls) -> "HistoryStore":
        from app.config import get_config
        return cls(get_config().data_dir)

    def save_run(
        self,
        session_id: str,
        request_history: list[str],
        requirements: dict[str, Any] | None,
        plan: dict[str, Any] | None,
        tool_calls: list[dict[str, Any]],
        inspection: dict[str, Any] | None,
        summary: str,
        status: str,
    ) -> Path:
        run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        path = self.base / f"{run_id}.jsonl"

        def write(kind: str, payload: dict[str, Any]) -> None:
            record = {"ts": time.time(), "kind": kind, **payload}
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")

        write("run_start", {"session_id": session_id, "requests": request_history})
        if requirements:
            write("requirements", requirements)
        if plan:
            write("plan", plan)
        for call in tool_calls:
            write("tool_call", call)
        if inspection:
            write("inspection", inspection)
        write("run_end", {"status": status, "summary": summary})
        return path

    def list_runs(self) -> list[Path]:
        return sorted(self.base.glob("*.jsonl"), reverse=True)

    def load_run(self, path: Path) -> list[dict[str, Any]]:
        records = []
        with Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def latest_run(self) -> Optional[list[dict[str, Any]]]:
        runs = self.list_runs()
        return self.load_run(runs[0]) if runs else None
