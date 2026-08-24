"""Repair Agent: diagnose validation failures and produce corrective steps.

Bounded by ``max_repair_attempts`` — repeated failure surfaces to the user
instead of looping forever.
"""
from __future__ import annotations

from typing import Any

from app.models.events import EventBus, EventType
from app.models.cad_plan import CADPlan, InspectionReport, PlanStep, StepStatus
from app.graph.llm import LLMClient
from app.graph.state import AgentState

NODE = "repair"

REPAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "diagnosis": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string"},
                    "description": {"type": "string"},
                    "args": {"type": "object"},
                },
                "required": ["operation", "description", "args"],
            },
        },
    },
    "required": ["diagnosis", "steps"],
}


def heuristic_repairs(report: InspectionReport, plan: CADPlan) -> tuple[str, list[PlanStep]]:
    """Map known failure signatures to corrective operations."""
    repairs: list[PlanStep] = []
    diagnoses: list[str] = []

    for check in report.failures():
        if check.name == "slot_width" and check.expected is not None:
            target = float(check.expected)
            slot_step = next((s for s in plan.steps if s.operation == "add_dimension"
                              and s.args.get("name") == "slot_width"), None)
            sketch = (slot_step.args.get("sketch") if slot_step else None) or \
                     next((s.args.get("sketch") for s in plan.steps
                           if s.operation == "cut_extrude" and s.args.get("sketch")),
                          None) or f"{plan.object_name}_slot_sketch"
            diagnoses.append(
                f"Slot width {check.actual}mm != expected {target}mm; "
                f"modify constraint '{sketch}.slot_width' -> {target}mm and recompute"
            )
            repairs.extend([
                PlanStep(id="", operation="create_sketch",
                         description="Re-open slot sketch for repair",
                         args={"name": sketch, "plane": "XZ"}),
                PlanStep(id="", operation="draw_rectangle",
                         description=f"Redraw slot profile at {target} mm",
                         args={"sketch": sketch, "width_mm": target, "depth_mm": 15.0}),
                PlanStep(id="", operation="add_dimension",
                         description=f"Correct slot constraint to {target} mm",
                         args={"sketch": sketch, "name": "slot_width", "value": target}),
                PlanStep(id="", operation="cut_extrude",
                         description="Recompute slot cut",
                         args={"sketch": sketch, "distance_mm": 15.0,
                               "target_body": f"{plan.object_name}_support"}),
            ])
        elif check.name == "expected_bodies_exist":
            missing = check.actual if isinstance(check.actual, list) else []
            for body in missing:
                src = next((s for s in plan.steps if s.operation == "extrude"
                            and (s.args.get("body_name") == body)), None)
                if src:
                    diagnoses.append(f"Body '{body}' missing; re-running its extrude step")
                    repairs.append(PlanStep(
                        id="", operation="extrude",
                        description=f"Re-extrude {body}",
                        args=dict(src.args),
                    ))
        elif not check.passed and check.name == "all_steps_succeeded":
            failed = [s for s in plan.steps if s.status == StepStatus.FAILED]
            for s in failed:
                diagnoses.append(f"Retrying failed step [{s.id}] {s.operation}: {s.error}")
                retry = s.model_copy(deep=True)
                retry.status = StepStatus.PLANNED
                retry.error = None
                retry.description = f"[repair] {retry.description}"
                repairs.append(retry)

    return "; ".join(diagnoses) or "Unknown validation failure", repairs


async def repair_node(state: AgentState) -> dict[str, Any]:
    bus = EventBus.get()
    plan = CADPlan(**state["plan"])
    report = InspectionReport(**state["inspection"])
    attempts = int(state.get("repair_attempts", 0)) + 1

    cfg = getattr(state.get("bridge"), "config", None)
    max_attempts = getattr(cfg, "max_repair_attempts", 3) if cfg else 3

    bus.emit(NODE, EventType.STATUS,
             f"Repair attempt {attempts}/{max_attempts}")

    diagnosis: str
    repairs: list[PlanStep]

    llm = LLMClient.from_state(state)
    if llm.available:
        try:
            data = await llm.complete_json(
                system=(
                    "You are a CAD repair agent. Given a Fusion 360 model validation "
                    "report and the current plan, produce minimal corrective operations "
                    "(usually modify a constraint and recompute). Only use allowed ops."
                ),
                user=(f"Validation failures: {[c.model_dump() for c in report.failures()]}\n"
                      f"Current plan:\n{plan.to_prompt()}"),
                schema=REPAIR_SCHEMA,
            )
            diagnosis = data.get("diagnosis", "LLM-diagnosed")
            repairs = [PlanStep(id="", operation=s["operation"],
                                description=s.get("description", ""),
                                args=s.get("args", {}))
                       for s in data.get("steps", []) if s.get("operation")]
            bus.emit(NODE, EventType.STATUS, "LLM repair plan generated")
        except Exception as exc:
            bus.emit(NODE, EventType.WARNING, f"LLM repair failed ({exc}); using heuristics")
            diagnosis, repairs = heuristic_repairs(report, plan)
    else:
        diagnosis, repairs = heuristic_repairs(report, plan)

    # Reset previously executed steps so the corrected sequence re-runs cleanly.
    for s in plan.steps:
        if s.status in (StepStatus.FAILED, StepStatus.SKIPPED):
            s.status = StepStatus.PLANNED
            s.error = None

    base = len(plan.steps)
    for i, r in enumerate(repairs):
        r.id = f"r{attempts}_{i + 1:02d}"
        r.index = base + i
        if r.is_destructive():
            r.requires_approval = True
    plan.steps.extend(repairs)
    plan.revision += 1
    plan.reindex()

    exhausted = attempts >= max_attempts
    if not repairs:
        # Nothing actionable — looping would just burn retries.
        exhausted = True
    status = "repair_exhausted" if exhausted else "repair_planned"

    bus.emit(NODE, EventType.STATUS,
             ("No corrective steps possible — surfacing failure"
              if not repairs else
              ("Repair limit reached — surfacing failure" if exhausted
               else f"Repair planned: {len(repairs)} corrective step(s)")),
             {"diagnosis": diagnosis})
    if exhausted:
        bus.emit(NODE, EventType.ERROR,
                 f"Giving up after {attempts} repair attempts. Last diagnosis: {diagnosis}")

    return {
        "plan": plan.model_dump(),
        "repair_attempts": attempts,
        "status": status,
        "final_summary": (
            f"✗ Model could not be validated after {attempts} repair attempt(s).\n"
            f"Last diagnosis: {diagnosis}\n"
            "Inspect /history for full tool-call trace."
        ) if exhausted else None,
    }
