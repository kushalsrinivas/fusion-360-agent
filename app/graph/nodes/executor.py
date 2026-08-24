"""Fusion Executor node: runs the plan through MCP with approval gating."""
from __future__ import annotations

import asyncio
from typing import Any

from app.models.events import EventBus, EventType
from app.models.cad_plan import CADPlan
from app.fusion.executor import FusionExecutor
from app.graph.approvals import ApprovalManager
from app.graph.state import AgentState

NODE = "executor"


async def executor_node(state: AgentState) -> dict[str, Any]:
    bus = EventBus.get()
    bus.emit(NODE, EventType.STATUS, "Executing CAD plan")

    plan = CADPlan(**state["plan"])
    bridge = state["bridge"]
    registry = state["registry"]
    session_id = state["session_id"]
    cfg = getattr(bridge, "config", None)
    timeout = getattr(cfg, "approval_timeout_s", 600.0) if cfg else 600.0

    approvals = ApprovalManager.get()

    async def request_approval(step) -> bool:
        bus.emit(NODE, EventType.APPROVAL,
                 f"⚠ Approval needed: {step.operation}\n  {step.description}\n"
                 f"  → /approve or /reject",
                 {"step_id": step.id})
        # Block this worker thread (not the UI) until the user answers.
        decision = await asyncio.to_thread(
            approvals.request, session_id, step.operation, step.description,
            step.id, timeout,
        )
        if decision.timed_out:
            bus.emit(NODE, EventType.WARNING, "Approval timed out — treating as reject")
        return decision.approved and not decision.timed_out

    executor = FusionExecutor(bridge, registry, node_name=NODE)
    records: list = list(state.get("tool_calls", []))
    await executor.execute(plan, records, request_approval=request_approval)

    plan_dict = plan.model_dump()
    status = "execution_failed" if plan.has_failures() else "execution_complete"

    executed = sum(1 for s in plan.steps if s.status.value in ("executed", "verified"))
    bus.emit(NODE, EventType.STATUS,
             f"Execution pass done: {executed}/{len(plan.steps)} steps executed",
             {"status": status})

    return {
        "plan": plan_dict,
        "tool_calls": [r.model_dump() for r in records[len(state.get("tool_calls", [])):]],
        "status": status,
    }
