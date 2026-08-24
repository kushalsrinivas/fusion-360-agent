"""CAD Planning Agent: structured requirements -> ordered Fusion operations.

Prefers parametric, constraint-driven modeling: sketches + driving dimensions
+ constraints before any 3D operation. The plan is a strict, reviewable
artifact — never free-form tool invocation.
"""
from __future__ import annotations

from typing import Any

from app.models.events import EventBus, EventType
from app.models.cad_plan import CADPlan, KNOWN_OPERATIONS, PlanStep
from app.models.requirements import CADRequirements
from app.graph.llm import LLMClient
from app.graph.state import AgentState

NODE = "planner"

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "strategy": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string",
                                  "enum": sorted(KNOWN_OPERATIONS)},
                    "description": {"type": "string"},
                    "args": {"type": "object"},
                },
                "required": ["operation", "description", "args"],
            },
        },
    },
    "required": ["strategy", "steps"],
}


def build_plan(req: CADRequirements, max_steps: int = 40) -> CADPlan:
    """Deterministic template planner (offline mode / LLM fallback)."""
    steps: list[PlanStep] = []

    def add(operation: str, description: str, **args: Any) -> None:
        step = PlanStep(id=f"s{len(steps) + 1:02d}", operation=operation,
                        description=description, args=args)
        if step.is_destructive():
            step.requires_approval = True
        steps.append(step)

    obj = req.object.replace(" ", "_")
    width = _mm(req, "width", 80)
    depth = _mm(req, "depth", 100)
    height = _mm(req, "height", 120)
    thickness = _mm(req, "backplate_thickness", _mm(req, "thickness", 5))
    angle = _deg(req, "viewing_angle", 20)

    add("create_component", f"Create root component '{obj}'", name=obj)

    # --- backplate (wall side) ---
    add("create_sketch", "Backplate profile on XY plane", name=f"{obj}_base_sketch", plane="XY")
    add("draw_rectangle", f"Backplate rectangle {width} x {depth} mm",
        sketch=f"{obj}_base_sketch", width_mm=width, depth_mm=thickness)
    add("add_dimension", "Drive backplate thickness constraint",
        sketch=f"{obj}_base_sketch", name="thickness", value=thickness)
    add("add_constraint", "Constrain backplate profile symmetric to origin",
        sketch=f"{obj}_base_sketch", type="symmetric")
    add("extrude", f"Extrude backplate {thickness} mm",
        sketch=f"{obj}_base_sketch", distance_mm=thickness, body_name=f"{obj}_backplate")

    # --- angled support arm ---
    support_h = round(height * 0.8, 2)
    add("create_sketch", "Support arm profile on XZ plane", name=f"{obj}_support_sketch", plane="XZ")
    add("draw_rectangle", f"Support arm {width} x {support_h} mm",
        sketch=f"{obj}_support_sketch", width_mm=width, depth_mm=support_h)
    add("add_dimension", "Viewing angle constraint", sketch=f"{obj}_support_sketch",
        name="viewing_angle_deg", value=angle)
    add("add_constraint", "Angle constraint between arm and base", sketch=f"{obj}_support_sketch",
        type="angle")
    add("extrude", f"Extrude support arm {thickness} mm at {angle} deg",
        sketch=f"{obj}_support_sketch", distance_mm=thickness, body_name=f"{obj}_support")

    # --- lip / slot on the front face ---
    if req.has_feature("phone_slot") or "stand" in req.object or "holder" in req.object:
        slot_w = _mm(req, "slot_width", 20)
        add("create_sketch", "Phone slot profile", name=f"{obj}_slot_sketch", plane="XZ")
        add("draw_rectangle", f"Phone slot {slot_w} mm wide",
            sketch=f"{obj}_slot_sketch", width_mm=slot_w, depth_mm=thickness * 3)
        add("add_dimension", "Slot width constraint", sketch=f"{obj}_slot_sketch",
            name="slot_width", value=slot_w)
        add("cut_extrude", f"Cut phone slot ({slot_w} mm) through support",
            sketch=f"{obj}_slot_sketch", distance_mm=thickness * 3,
            target_body=f"{obj}_support")

    # --- mounting holes ---
    if req.mounting or req.has_feature("screw_holes"):
        hole_d = _mm(req, "hole_diameter", 5)
        margin = min(width * 0.15, 15.0)
        for i, x in enumerate((-width / 2 + margin, width / 2 - margin)):
            add("create_sketch", f"Screw hole {i + 1} sketch", name=f"{obj}_hole{i + 1}_sketch", plane="XY")
            add("draw_circle", f"Ø{hole_d} hole at x={x:.1f}",
                sketch=f"{obj}_hole{i + 1}_sketch", diameter_mm=hole_d, x_mm=round(x, 2), y_mm=0.0)
            add("cut_extrude", f"Cut screw hole {i + 1} through backplate",
                sketch=f"{obj}_hole{i + 1}_sketch", distance_mm=thickness,
                target_body=f"{obj}_backplate")

    # --- finishing features ---
    fillet_r = _mm(req, "fillet_radius", None)
    if req.has_feature("rounded_edges") and fillet_r:
        add("fillet", f"Fillet exposed edges R{fillet_r}", body_name=f"{obj}_backplate",
            radius_mm=fillet_r)
    if req.has_feature("chamfer"):
        chamfer = _mm(req, "chamfer_distance", 1.0)
        add("chamfer", f"Chamfer edges {chamfer} mm", body_name=f"{obj}_backplate",
            distance_mm=chamfer)

    add("inspect_model", "Inspect resulting model")

    plan = CADPlan(
        object_name=obj,
        strategy=(
            f"Parametric {obj}: constrained sketches with driving dimensions -> "
            f"{thickness}mm backplate extrude -> {angle}° support arm"
            + (" -> slot cut" if any(s.operation == 'cut_extrude' for s in steps) else "")
            + (" -> mounting holes" if req.mounting or req.has_feature('screw_holes') else "")
            + (" -> edge fillets" if req.has_feature('rounded_edges') else "")
        ),
        steps=steps[:max_steps],
    )
    plan.reindex()
    return plan


async def llm_plan(req: CADRequirements, llm: LLMClient, max_steps: int) -> CADPlan | None:
    try:
        data = await llm.complete_json(
            system=(
                "You are a CAD planning agent for Fusion 360. Produce an ordered list of "
                "parametric modeling operations. Prefer constrained sketches with driving "
                "dimensions over arbitrary geometry. Only use the allowed operations. "
                "Always end with inspect_model. Give every extruded body a descriptive "
                "body_name."
            ),
            user=f"Requirements: {req.to_prompt()}",
            schema=PLAN_SCHEMA,
        )
        steps = []
        for i, s in enumerate(data.get("steps", [])[:max_steps]):
            op = s["operation"]
            if op not in KNOWN_OPERATIONS:
                continue
            step = PlanStep(id=f"s{i + 1:02d}", operation=op,
                            description=s.get("description", ""), args=s.get("args", {}))
            if step.is_destructive():
                step.requires_approval = True
            steps.append(step)
        if not any(s.operation == "inspect_model" for s in steps):
            steps.append(PlanStep(id=f"s{len(steps) + 1:02d}", operation="inspect_model",
                                  description="Inspect resulting model"))
        plan = CADPlan(object_name=req.object.replace(" ", "_"),
                       strategy=data.get("strategy", "LLM-generated plan"), steps=steps)
        plan.reindex()
        return plan
    except Exception:
        return None


async def planner_node(state: AgentState) -> dict[str, Any]:
    bus = EventBus.get()
    bus.emit(NODE, EventType.STATUS, "Generating modeling strategy")

    req = CADRequirements(**state["requirements"])
    cfg = getattr(state.get("bridge"), "config", None)
    max_steps = getattr(cfg, "max_plan_steps", 40) if cfg else 40

    llm = LLMClient.from_state(state)
    plan: CADPlan | None = None
    source = "template"

    if llm.available:
        plan = await llm_plan(req, llm, max_steps)
        if plan is not None:
            source = "llm"
        else:
            bus.emit(NODE, EventType.WARNING, "LLM planning failed; using template planner")
    if plan is None:
        plan = build_plan(req, max_steps=max_steps)

    # Reset stale statuses from any previous run of this plan object.
    for s in plan.steps:
        if s.status not in (s.status.PLANNED,):
            s.status = s.status.PLANNED

    bus.emit(NODE, EventType.STATUS, f"Plan ready ({source}, rev {plan.revision})",
             {"steps": len(plan.steps), "strategy": plan.strategy})

    return {"plan": plan.model_dump(), "status": "plan_ready"}


def _mm(req: CADRequirements, name: str, default: float | None) -> float:
    d = req.dim(name)
    return float(d.value_mm) if d and d.value_mm is not None else float(default)


def _deg(req: CADRequirements, name: str, default: float) -> float:
    d = req.dim(name)
    return float(d.value_deg) if d and d.value_deg is not None else float(default)
