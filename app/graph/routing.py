"""Conditional edge routing for the CAD agent graph."""
from __future__ import annotations

from app.graph.state import AgentState

MAX_TOTAL_PASSES = 8  # hard bound on plan→execute→inspect→repair cycles


def route_after_requirements(state: AgentState) -> str:
    if state.get("status") == "needs_input":
        return "finish"          # surface open questions to user
    return "planner"


def route_after_planner(state: AgentState) -> str:
    if state.get("status") == "plan_rejected":
        return "planner"
    return "review"


def route_after_review(state: AgentState) -> str:
    if state.get("status") == "plan_rejected":
        return "planner"
    return "executor"


def route_after_inspector(state: AgentState) -> str:
    status = state.get("status")
    if status == "inspection_passed":
        return "finish"
    if status == "inspection_failed":
        attempts = int(state.get("repair_attempts", 0))
        cfg = getattr(state.get("bridge"), "config", None)
        max_attempts = getattr(cfg, "max_repair_attempts", 3) if cfg else 3
        if attempts >= max_attempts or _passes_exhausted(state):
            return "finish"
        return "repair"
    return "finish"


def route_after_repair(state: AgentState) -> str:
    if state.get("status") == "repair_exhausted" or _passes_exhausted(state):
        return "finish"
    return "executor"


def _passes_exhausted(state: AgentState) -> bool:
    """Detect repeated failure loops via accumulated error count."""
    return len(state.get("errors", [])) >= MAX_TOTAL_PASSES * 3
