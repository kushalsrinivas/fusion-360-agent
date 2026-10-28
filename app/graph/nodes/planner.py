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
        "strategy": {"type": "string", "description": "High-level modeling strategy summary"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": sorted(KNOWN_OPERATIONS),
                        "description": "Fusion CAD operation",
                    },
                    "description": {"type": "string", "description": "What this step does with dimensions"},
                    "args": {
                        "type": "object",
                        "description": "Operation arguments with explicit millimeter dimensions",
                        "properties": {
                            "name": {"type": "string", "description": "Name of component or sketch"},
                            "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"], "description": "Sketch or construction plane"},
                            "sketch": {"type": "string", "description": "Target sketch name"},
                            "width_mm": {"type": "number", "description": "Width dimension in mm"},
                            "depth_mm": {"type": "number", "description": "Depth or length dimension in mm"},
                            "height_mm": {"type": "number", "description": "Height or thickness dimension in mm"},
                            "distance_mm": {"type": "number", "description": "Extrusion distance, cut depth, or chamfer in mm"},
                            "diameter_mm": {"type": "number", "description": "Hole or circle diameter in mm"},
                            "radius_mm": {"type": "number", "description": "Fillet, circle, or polygon radius in mm"},
                            "body_name": {"type": "string", "description": "Name for the extruded/revolved/modified solid body"},
                            "target_body": {"type": "string", "description": "Target body to cut or boolean modify"},
                            "tool_body": {"type": "string", "description": "Tool body for boolean operation"},
                            "operation": {"type": "string", "enum": ["new_body", "join", "cut", "intersect"]},
                            "direction": {"type": "string", "enum": ["positive", "negative", "symmetric"]},
                            "angle_deg": {"type": "number", "description": "Revolve or sweep angle in degrees"},
                            "axis": {"type": "string", "enum": ["x", "y", "z"], "description": "Rotation or pattern axis"},
                            "count": {"type": "integer", "description": "Pattern instance count"},
                            "count_x": {"type": "integer", "description": "Rectangular pattern column count"},
                            "spacing_mm": {"type": "number", "description": "Pattern spacing in mm"},
                            "thickness_mm": {"type": "number", "description": "Shell wall thickness in mm"},
                            "face_selection": {"type": "string", "enum": ["top", "bottom", "all"]},
                            "edge_selection": {"type": "string", "enum": ["all", "top", "bottom", "vertical"]},
                            "mirror_plane": {"type": "string", "enum": ["xy", "yz", "xz"]},
                            "offset_mm": {"type": "number", "description": "Construction plane offset in mm"},
                            "profile_sketch_names": {"type": "array", "items": {"type": "string"}, "description": "Sketches for loft"},
                            "path_sketch_name": {"type": "string", "description": "Path sketch for sweep"},
                            "x_mm": {"type": "number", "description": "X position offset in mm"},
                            "y_mm": {"type": "number", "description": "Y position offset in mm"},
                            "start_x_mm": {"type": "number"},
                            "start_y_mm": {"type": "number"},
                            "end_x_mm": {"type": "number"},
                            "end_y_mm": {"type": "number"},
                            "center_x_mm": {"type": "number"},
                            "center_y_mm": {"type": "number"},
                            "sweep_angle_deg": {"type": "number"},
                            "sides": {"type": "integer", "description": "Number of polygon sides"},
                            "appearance_name": {"type": "string", "description": "Appearance material name"},
                            "value": {"type": "number", "description": "Dimension value"},
                            "type": {"type": "string", "description": "Constraint type"}
                        },
                    },
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


async def llm_plan(req: CADRequirements, llm: LLMClient, max_steps: int, feedback: str = "") -> CADPlan | None:
    try:
        user_prompt = f"Requirements: {req.to_prompt()}"
        if feedback:
            user_prompt += f"\n\nReview feedback to fix in the new plan:\n{feedback}"
        data = await llm.complete_json(
            system=(
                "You are an expert Principal CAD Modeling Engineer for Autodesk Fusion 360.\n"
                "Your objective is to generate an optimal, logically ordered sequence of parametric CAD operations.\n\n"
                "=== CAD MODELING STRATEGIES ===\n"
                "1. Prismatic Parts (brackets, cubes, enclosures, bases, plates):\n"
                "   create_sketch on plane -> draw_rectangle / draw_circle / draw_polygon -> extrude into solid -> secondary sketch cuts (cut_extrude or create_hole) -> fillet/chamfer.\n"
                "2. Rotational / Turned Parts (shafts, bottles, vases, wheels, pulleys, cones):\n"
                "   create_sketch on 'XZ' or 'YZ' plane -> draw cross-section profile (draw_rectangle, draw_line, draw_arc) -> revolve (axis='z' or 'x', angle_deg=360.0).\n"
                "3. Hollow Containers (cups, mugs, electronic housings, vases, boxes):\n"
                "   Create solid body first (via extrude or revolve) -> apply shell (thickness_mm=..., face_selection='top').\n"
                "4. Patterned Features (gear teeth, bolt hole circles, vent slots, spokes):\n"
                "   Create 1 feature cut/boss -> circular_pattern (axis='z', count=N, total_angle_deg=360) or rectangular_pattern.\n"
                "5. Complex Lofts / Multi-Section Parts:\n"
                "   create_sketch (sketch 1 on XY) -> create_construction_plane (offset_mm=...) -> create_sketch (sketch 2 on offset plane) -> loft (profile_sketch_names=['sketch1', 'sketch2']).\n"
                "6. Direct Primitives (when appropriate for fast solids):\n"
                "   create_cylinder, create_box, create_sphere, create_torus, combined with boolean_operation (join/cut/intersect).\n\n"
                "=== OPERATIONS VOCABULARY & ARGS (ALL DIMENSIONS IN MM) ===\n"
                "- create_component: {\"name\": str}\n"
                "- create_sketch: {\"name\": str, \"plane\": \"XY\"|\"XZ\"|\"YZ\", \"z_offset_mm\": float}\n"
                "- create_construction_plane: {\"method\": \"offset\", \"plane\": \"XY\"|\"XZ\"|\"YZ\", \"offset_mm\": float}\n"
                "- draw_rectangle: {\"sketch\": str, \"width_mm\": float, \"depth_mm\": float, \"x_mm\": float, \"y_mm\": float}\n"
                "- draw_circle: {\"sketch\": str, \"diameter_mm\": float, \"x_mm\": float, \"y_mm\": float}\n"
                "- draw_line: {\"sketch\": str, \"start_x_mm\": float, \"start_y_mm\": float, \"end_x_mm\": float, \"end_y_mm\": float}\n"
                "- draw_arc: {\"sketch\": str, \"center_x_mm\": float, \"center_y_mm\": float, \"start_x_mm\": float, \"start_y_mm\": float, \"sweep_angle_deg\": float}\n"
                "- create_polygon: {\"sketch\": str, \"sides\": int, \"radius_mm\": float, \"center_x_mm\": float, \"center_y_mm\": float}\n"
                "- extrude: {\"sketch\": str, \"distance_mm\": float, \"body_name\": str, \"operation\": \"new_body\"|\"join\"|\"cut\", \"direction\": \"positive\"|\"negative\"|\"symmetric\"}\n"
                "- cut_extrude: {\"sketch\": str, \"distance_mm\": float, \"target_body\": str}\n"
                "- revolve: {\"sketch\": str, \"angle_deg\": float, \"axis\": \"z\"|\"x\"|\"y\", \"body_name\": str, \"operation\": \"new_body\"|\"join\"|\"cut\"}\n"
                "- loft: {\"profile_sketch_names\": [str], \"body_name\": str, \"operation\": \"new_body\"}\n"
                "- sweep: {\"path_sketch_name\": str, \"profile_index\": 0, \"path_curve_index\": 0, \"body_name\": str}\n"
                "- shell: {\"body_name\": str, \"thickness_mm\": float, \"face_selection\": \"top\"|\"bottom\"}\n"
                "- create_hole: {\"body_name\": str, \"diameter_mm\": float, \"depth_mm\": float, \"center_x_mm\": float, \"center_y_mm\": float, \"face_selection\": \"top\"}\n"
                "- fillet: {\"body_name\": str, \"radius_mm\": float, \"edge_selection\": \"all\"|\"top\"|\"bottom\"|\"vertical\"}\n"
                "- chamfer: {\"body_name\": str, \"distance_mm\": float, \"edge_selection\": \"all\"|\"top\"|\"bottom\"|\"vertical\"}\n"
                "- circular_pattern: {\"body_name\": str, \"count\": int, \"axis\": \"z\"|\"x\"|\"y\", \"total_angle_deg\": float}\n"
                "- rectangular_pattern: {\"body_name\": str, \"count_x\": int, \"spacing_mm\": float}\n"
                "- mirror: {\"body_name\": str, \"mirror_plane\": \"xz\"|\"yz\"|\"xy\"}\n"
                "- boolean_operation: {\"operation\": \"join\"|\"cut\"|\"intersect\", \"target_body\": str, \"tool_body\": str}\n"
                "- create_box: {\"length_mm\": float, \"width_mm\": float, \"height_mm\": float, \"body_name\": str}\n"
                "- create_cylinder: {\"radius_mm\": float, \"height_mm\": float, \"axis\": \"z\"|\"x\"|\"y\", \"body_name\": str}\n"
                "- create_sphere: {\"radius_mm\": float, \"body_name\": str}\n"
                "- create_torus: {\"major_radius_mm\": float, \"minor_radius_mm\": float, \"axis\": \"z\", \"body_name\": str}\n"
                "- set_appearance: {\"target_name\": str, \"appearance_name\": str}\n"
                "- inspect_model: {}\n\n"
                "=== MANDATORY RULES ===\n"
                "1. Provide a non-empty 'args' dictionary for every single step with concrete numeric millimeter dimensions.\n"
                "2. Assign a descriptive 'body_name' for every solid body creation.\n"
                "3. Always conclude the plan with 'inspect_model'."
            ),
            user=user_prompt,
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

    errors = state.get("errors", [])
    feedback = "\n".join(str(e.get("issues", e)) for e in errors if isinstance(e, dict))

    if llm.available:
        plan = await llm_plan(req, llm, max_steps, feedback=feedback)
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
