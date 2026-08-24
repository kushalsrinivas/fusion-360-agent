"""Requirement Agent: natural language -> structured CADRequirements."""
from __future__ import annotations

import re
from typing import Any

from app.models.events import EventBus, EventType
from app.models.requirements import CADRequirements, REQUIREMENTS_SCHEMA, Dimension
from app.graph.llm import LLMClient
from app.graph.state import AgentState

NODE = "requirements"

_LENGTH_RE = re.compile(
    r"(?P<name>\b[a-z_]{2,20})\s*(?:of|=|:)?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mm|millimet(?:er|re)s?|deg(?:rees?)?)\b",
    re.IGNORECASE,
)
_BARE_DIM_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(mm|deg)\b", re.IGNORECASE)

_FEATURE_KEYWORDS = {
    "rounded_edges": ["rounded", "fillet", "round edges", "rounded edge"],
    "chamfer": ["chamfer"],
    "screw_holes": ["screw hole", "mounting hole", "screw holes"],
    "phone_slot": ["slot", "groove"],
    "cable_cutout": ["cable", "cutout"],
    "vent_holes": ["vent"],
}

_MOUNTING_WORDS = ["wall mount", "wall-mounted", "mounted", "mounting holes", "hangs on"]
_OBJECT_HINTS = [
    ("phone stand", ["phone stand", "phone holder"]),
    ("wall phone holder", ["wall-mounted phone"]),
    ("bracket", ["bracket"]),
    ("enclosure", ["enclosure", "case"]),
    ("gear", ["gear"]),
    ("spacer", ["spacer", "washer"]),
]

# Dimensions genuinely required before we can plan most mechanical parts.
CRITICAL_DIMS = {"width": "overall width (mm)"}


def _extract_heuristics(text: str) -> CADRequirements:
    req = CADRequirements(description=text)
    lowered = text.lower()

    # Object detection
    for obj, hints in _OBJECT_HINTS:
        if any(h in lowered for h in hints):
            req.object = obj
            break

    # Named dimensions: e.g. "width 80mm", "5 mm thick"
    for m in _LENGTH_RE.finditer(text):
        name = m.group("name").lower()
        value = float(m.group("value"))
        unit = m.group("unit").lower()
        canonical = _canonical_dim_name(name)
        if unit.startswith("deg"):
            req.set_dim(canonical, value_deg=value, source="user")
        else:
            req.set_dim(canonical, value_mm=value, source="user")

    # Bare values with positional hints: "80mm wide", "20-degree viewing angle"
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*mm\s+(wide|long|deep|tall|high|thick)", text, re.IGNORECASE):
        word = m.group(2).lower()
        mapping = {"wide": "width", "long": "length", "deep": "depth",
                   "tall": "height", "high": "height", "thick": "thickness"}
        req.set_dim(mapping[word], value_mm=float(m.group(1)), source="user")

    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:-|\s)?degree", text, re.IGNORECASE):
        req.set_dim(_angle_name(lowered), value_deg=float(m.group(1)), source="user")
        break

    # Features
    for feature, keywords in _FEATURE_KEYWORDS.items():
        if any(k in lowered for k in keywords):
            if feature not in req.features:
                req.features.append(feature)

    # Mounting + screw hole diameter
    req.mounting = any(w in lowered for w in _MOUNTING_WORDS)
    if req.mounting and not req.has_feature("screw_holes"):
        req.features.append("screw_holes")

    hole = re.search(r"[øØphi ]*\(?(\d+(?:\.\d+)?)\s*mm\)?\s*(?:screw|mounting)?\s*holes?", text, re.IGNORECASE)
    if ("hole" in lowered) and hole:
        req.set_dim("hole_diameter", value_mm=float(hole.group(1)), source="user")

    # Material
    mat = re.search(r"\b(PLA|PETG|ABS|ASA|nylon|aluminum|steel)\b", text, re.IGNORECASE)
    if mat:
        req.material = mat.group(1).upper() if mat.group(1).upper() in ("PLA", "PETG", "ABS", "ASA") \
            else mat.group(1).lower()

    return req


def _canonical_dim_name(name: str) -> str:
    if "thick" in name:
        return "thickness"
    if "angle" in name or "tilt" in name:
        return "viewing_angle"
    if "slot" in name:
        return "slot_width"
    if "hole" in name or "screw" in name:
        return "hole_diameter"
    if "radius" in name:
        return "fillet_radius"
    return name


def _angle_name(lowered: str) -> str:
    if "view" in lowered or "tilt" in lowered:
        return "viewing_angle"
    return "angle"


def apply_defaults(req: CADRequirements) -> None:
    """Sensible engineering defaults so planning can always proceed."""
    defaults_mm = {
        "width": 80.0,
        "depth": 100.0,
        "height": 120.0,
        "thickness": 5.0,
        "backplate_thickness": 5.0,
        "slot_width": 20.0,
        "hole_diameter": 5.0,
        "fillet_radius": 2.0,
    }
    for name, value in defaults_mm.items():
        if req.dim(name) is None:
            req.set_dim(name, value_mm=value, source="default")
    if req.dim("viewing_angle") is None:
        req.set_dim("viewing_angle", value_deg=20.0, source="default")


async def requirements_node(state: AgentState) -> dict[str, Any]:
    bus = EventBus.get()
    bus.emit(NODE, EventType.STATUS, "Analyzing request")

    text = state["user_request"]
    llm = LLMClient.from_state(state)

    extracted: CADRequirements | None = None
    if llm.available:
        try:
            prior = state.get("requirements")
            context = ""
            if state.get("is_followup") and prior:
                from app.models.requirements import CADRequirements as R
                context = f"Current design context: {R(**prior).to_prompt()}\n"
            data = await llm.complete_json(
                system=(
                    "You are a CAD requirements analyst. Convert the user's natural-language "
                    "request into structured CAD requirements. Use millimeters. Only mark "
                    "dimensions 'user' when explicitly stated; use 'default' otherwise. "
                    "List genuinely required missing information in open_questions."
                ),
                user=context + f"Request: {text}",
                schema=REQUIREMENTS_SCHEMA,
            )
            extracted = CADRequirements(**data)
            bus.emit(NODE, EventType.STATUS, "LLM extraction complete")
        except Exception as exc:
            bus.emit(NODE, EventType.WARNING, f"LLM extraction failed ({exc}); using heuristics")

    if extracted is None:
        extracted = _extract_heuristics(text)

    merged = extracted
    if state.get("is_followup") and state.get("requirements"):
        base = CADRequirements(**state["requirements"])
        merged = base.merge(extracted)
        bus.emit(NODE, EventType.STATUS,
                 f"Merged follow-up into existing design (rev {merged.revision})")

    apply_defaults(merged)

    # Critical missing info check — only ask when truly blocking.
    blocking: list[str] = []
    for dim, label in CRITICAL_DIMS.items():
        d = merged.dim(dim)
        if d is None or (d.value_mm is None and d.value_deg is None):
            blocking.append(f"Specify {label}")
    merged.open_questions = [q for q in merged.open_questions if q] or blocking

    bus.emit(NODE, EventType.STATUS, "Requirements extracted",
             {"object": merged.object, "features": merged.features,
              "dims": {d.name: (d.value_deg if d.value_deg is not None else d.value_mm)
                       for d in merged.dimensions},
              "revision": merged.revision})

    status = "needs_input" if (blocking and not state.get("is_followup")) else "requirements_ok"
    return {
        "requirements": merged.model_dump(),
        "missing_info": blocking,
        "status": status,
    }
