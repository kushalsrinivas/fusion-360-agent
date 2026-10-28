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
    has_sketch = any(s.operation == "create_sketch" for s in plan.steps)
    has_primitive = any(s.operation in ("create_box", "create_cylinder", "create_sphere", "create_torus") for s in plan.steps)
    if not has_sketch and not has_primitive:
        issues.append("Plan does not create any sketches or solid primitives")
    has_solid = any(s.operation in ("extrude", "revolve", "loft", "sweep", "create_box", "create_cylinder", "create_sphere", "create_torus") for s in plan.steps)
    if not has_solid:
        issues.append("Plan never creates a solid body (requires extrude, revolve, loft, sweep, or primitive)")
    if not plan.steps[-1].operation == "inspect_model":
        issues.append("Plan must end with inspect_model")
    return issues


async def review_node(state: AgentState) -> dict[str, Any]:
    bus = EventBus.get()
    bus.emit(NODE, EventType.STATUS, "Reviewing modeling strategy")

    plan = CADPlan(**state["plan"])
    cfg = getattr(state.get("bridge"), "config", None)
    max_repairs = getattr(cfg, "max_repair_attempts", 3) if cfg else 3

    # Check review attempt count from state errors
    review_errors = [e for e in state.get("errors", []) if e.get("node") == NODE]
    issues = review_plan(plan, max_repairs)

    if not any(s.operation == "add_dimension" for s in plan.steps):
        bus.emit(NODE, EventType.STATUS, "Plan uses explicit sketch geometry dimensions")

    if issues and len(review_errors) < 2:
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
