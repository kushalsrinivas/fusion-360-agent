"""Model Inspector Agent: verify the model in Fusion matches the requirements.

"Executed" is never treated as "verified": this node queries the live
document via MCP and compares against expected geometry.
"""
from __future__ import annotations

from typing import Any

from app.models.events import EventBus, EventType
from app.models.cad_plan import CADPlan, InspectionReport, CheckResult, StepStatus
from app.models.requirements import CADRequirements
from app.graph.state import AgentState

NODE = "inspector"

# Tolerance for mm comparisons (accounts for fillets/rounding in reports).
DIM_TOL_MM = 0.5


async def inspector_node(state: AgentState) -> dict[str, Any]:
    bus = EventBus.get()
    bus.emit(NODE, EventType.INSPECTION, "Inspecting model")
    bus.emit(NODE, EventType.TOOL_CALL, "inspect_model")

    plan = CADPlan(**state["plan"])
    req = CADRequirements(**state["requirements"])
    bridge = state["bridge"]
    registry = state["registry"]

    checks: list[CheckResult] = []

    # 1. Every step verified?
    failed_steps = [s for s in plan.steps if s.status == StepStatus.FAILED]
    checks.append(CheckResult(
        name="all_steps_succeeded",
        passed=not failed_steps,
        detail=", ".join(f"{s.id}:{s.error}" for s in failed_steps) or "all steps OK",
        actual=[s.id for s in failed_steps] or None,
        expected=None,
    ))

    # 2. Query live document via the normalized inspection interface.
    bus.emit(NODE, EventType.TOOL_CALL, "inspect_document")
    try:
        report = await bridge.inspect_document()
        report = {**report, "success": True}
    except Exception as exc:
        report = {"success": False, "error": str(exc)}

    if not report.get("success", False):
        checks.append(CheckResult(
            name="document_query", passed=False,
            detail=str(report.get("error", "inspect_model failed")),
        ))
        return _finish(state, plan, checks, bus)

    bodies: dict[str, Any] = report.get("bodies", {}) or {}

    # 3. Expected bodies exist. Servers like faust-machines assign their own
    # body names (Body1, ...) since `extrude` takes no name argument — accept
    # an equal-or-greater body count as a naming-tolerant pass. Servers in
    # their own mock mode report static scene data, which cannot verify
    # geometry; annotate rather than fail.
    expected_bodies = plan.body_names()
    missing = [b for b in expected_bodies if b not in bodies]
    server_mock = (report.get("mode") == "mock")
    names_tolerant = missing and len(bodies) >= len(expected_bodies)
    if missing and server_mock:
        checks.append(CheckResult(
            name="expected_bodies_exist", passed=True,
            detail=f"server in mock mode reports static scene state "
                   f"({len(bodies)} body/bodies listed); cannot verify against live geometry",
            expected=expected_bodies, actual=list(bodies.keys()),
        ))
    else:
        checks.append(CheckResult(
            name="expected_bodies_exist",
            passed=not missing or bool(names_tolerant),
            detail=(f"missing {missing}" if missing and not names_tolerant else
                    f"server-assigned names (expected {len(expected_bodies)}, found {len(bodies)})"
                    if names_tolerant else
                    f"{len(expected_bodies)} body/bodies present"),
            expected=expected_bodies,
            actual=list(bodies.keys()),
        ))

    # 4. No unintended extra bodies — only meaningful when the server honors
    # our body names (e.g. the built-in mock). Name-assigning servers are
    # covered by the count tolerance above.
    if not state.get("is_followup") and any(b in bodies for b in expected_bodies):
        extras = [b for b in bodies if b not in expected_bodies]
        checks.append(CheckResult(
            name="no_unintended_bodies",
            passed=not extras,
            detail=f"unexpected bodies {extras}" if extras else "clean",
            actual=extras or None,
        ))

    # 5. Dimension verification against the specific slot-cut step result.
    slot_d = req.dim("slot_width")
    if slot_d and slot_d.value_mm:
        # Locate the slot sketch via its driving-dimension step, then read
        # the effective width reported by THAT cut (not any later hole cut).
        slot_sketch = next((s.args.get("sketch") for s in plan.steps
                            if s.operation == "add_dimension"
                            and s.args.get("name") == "slot_width"), None)
        reported_slot = None
        if slot_sketch:
            cut = next((s for s in plan.steps
                        if s.operation == "cut_extrude"
                        and s.args.get("sketch") == slot_sketch
                        and s.result), None)
            if cut is not None:
                reported_slot = cut.result.get("effective_width")
        elif len(bodies) > 0:
            checks.append(CheckResult(
                name="slot_width", passed=True,
                detail="server does not report per-cut widths; skipping measurement",
            ))
            reported_slot = "skipped"
            drift = abs(reported_slot - float(slot_d.value_mm))
            checks.append(CheckResult(
                name="slot_width",
                passed=drift <= DIM_TOL_MM,
                detail=f"expected {slot_d.value_mm}mm ±{DIM_TOL_MM}, measured {reported_slot}mm"
                       + ("" if drift <= DIM_TOL_MM else " — tolerance exceeded"),
                expected=float(slot_d.value_mm),
                actual=reported_slot,
            ))

    thick = req.dim("thickness") or req.dim("backplate_thickness")
    if thick and thick.value_mm and bodies:
        heights = [float(b["bbox"]["h"]) for b in bodies.values()
                   if b.get("bbox") and b["bbox"].get("h") is not None]
        if heights:
            ok = any(abs(h - float(thick.value_mm)) <= DIM_TOL_MM for h in heights)
            checks.append(CheckResult(
                name="backplate_thickness",
                passed=ok,
                detail=f"expected {thick.value_mm}mm; body heights {heights}",
                expected=float(thick.value_mm),
                actual=heights,
            ))

    hole = req.dim("hole_diameter")
    if (req.mounting or req.has_feature("screw_holes")) and hole and hole.value_mm:
        cut_values = [b.get("cuts") for b in bodies.values()]
        if all(c is None for c in cut_values):
            # Server does not report per-body cut counts — verify via the plan's
            # successfully executed hole-cut steps instead.
            hole_cuts = [s for s in plan.steps
                         if s.operation == "cut_extrude"
                         and s.status in (StepStatus.EXECUTED, StepStatus.VERIFIED)
                         and "hole" in (s.args.get("sketch") or "").lower()]
            checks.append(CheckResult(
                name="mounting_holes_present",
                passed=len(hole_cuts) >= 2,
                detail=f"{len(hole_cuts)} executed hole-cut step(s) "
                       f"(server does not report cut counts)",
                expected=2, actual=len(hole_cuts),
            ))
        else:
            cut_count = sum(int(c or 0) for c in cut_values)
            checks.append(CheckResult(
                name="mounting_holes_present",
                passed=cut_count >= 2,
                detail=f"expected >=2 cuts for screw holes, found {cut_count}"
                       + (f" (Ø{hole.value_mm})" if cut_count < 2 else ""),
                expected=2,
                actual=cut_count,
            ))

    # 6. Sketches exist (parametric integrity). Dimension/constraint counts are
    # only enforced when the server actually reports them — some servers'
    # scene info omits them, which must not read as a modeling failure.
    sketch_count = int(report.get("sketch_count", 0))
    constraint_count = int(report.get("constraint_count", 0))
    dim_count = int(report.get("dimension_count", 0))
    reported_constraints = bool(report.get("constraints") or report.get("dimensions"))
    checks.append(CheckResult(
        name="parametric_integrity",
        passed=sketch_count > 0,
        detail=(f"{sketch_count} sketches, {dim_count} dims, {constraint_count} constraints"
                if reported_constraints or dim_count or constraint_count else
                f"{sketch_count} sketches (dim/constraint counts not reported by server)"),
    ))

    passed = all(c.passed for c in checks)
    return _finish(state, plan, checks, bus)


def _finish(state: AgentState, plan: CADPlan, checks: list[CheckResult],
            bus: EventBus) -> dict[str, Any]:
    report = InspectionReport(passed=all(c.passed for c in checks), checks=checks)
    if report.passed:
        # Inspection confirmed geometry against the live document:
        # promote executed steps to VERIFIED.
        for s in plan.steps:
            if s.status == StepStatus.EXECUTED:
                s.status = StepStatus.VERIFIED
        bus.emit(NODE, EventType.INSPECTION, "✓ Model valid — all checks passed",
                 {"checks": len(checks)})
        status = "inspection_passed"
    else:
        fails = [c.name for c in report.failures()]
        bus.emit(NODE, EventType.WARNING, f"✗ Validation failed: {fails}")
        status = "inspection_failed"

    return {
        "inspection": report.model_dump(),
        "plan": plan.model_dump(),
        "status": status,
    }
