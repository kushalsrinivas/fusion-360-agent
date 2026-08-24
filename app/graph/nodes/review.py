"""Design Review / Validator: static sanity checks on the plan before execution."""
from __future__ import annotations

from typing import Any

from app.models.events import EventBus, EventType
from app.models.cad_plan import CADPlan
from app.graph.state import AgentState

NODE = "review"


def review_plan(plan: CADPlan, max_repair_attempts: int) -> list[str]:
    issues: list[str] = []
    if not plan.steps:
        issues.append("Plan contains no steps")
        return issues
    if len(plan.steps) > 60:
        issues.append(f"Plan too long ({len(plan.steps)} steps)")
    if not any(s.operation == "create_sketch" for s in plan.steps):
        issues.append("Plan does not create any sketches (non-parametric)")
    if not any(s.operation == "extrude" for s in plan.steps):
        issues.append("Plan never extrudes a solid body")
    if not plan.steps[-1].operation == "inspect_model":
        issues.append("Plan must end with inspect_model")
    if not any(s.operation == "add_dimension" for s in plan.steps):
        issues.append("Plan has no driving dimensions (should be constraint-driven)")
    return issues


async def review_node(state: AgentState) -> dict[str, Any]:
    bus = EventBus.get()
    bus.emit(NODE, EventType.STATUS, "Reviewing modeling strategy")

    plan = CADPlan(**state["plan"])
    cfg = getattr(state.get("bridge"), "config", None)
    max_repairs = getattr(cfg, "max_repair_attempts", 3) if cfg else 3

    issues = review_plan(plan, max_repairs)
    if issues:
        for issue in issues:
            bus.emit(NODE, EventType.WARNING, f"Review issue: {issue}")
        # Send back to planner with the issues attached as errors.
        return {
            "status": "plan_rejected",
            "errors": [{"node": NODE, "issues": issues}],
        }

    bus.emit(NODE, EventType.STATUS,
             f"Plan approved ({len(plan.steps)} steps, parametric)",
             {"bodies": plan.body_names()})
    return {"status": "plan_approved"}
