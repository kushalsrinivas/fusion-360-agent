"""Adapter for the faust-machines fusion360-mcp-server.

Repo: https://github.com/faust-machines/fusion360-mcp-server (PyPI:
``fusion360-mcp-server``). Key traits this adapter handles:

- **Units are centimeters** (Fusion's internal unit). Our semantic plan speaks
  millimeters, so every length is converted here — once, centrally.
- Cuts are not a separate tool: ``cut_extrude`` maps to ``extrude`` with
  ``operation="cut"``.
- Sketches/dimensions/constraints target "the most recent sketch" via
  ``sketch_name`` params; our sketch names are carried through where accepted
  and used for internal tracking otherwise.
- Inspection composes ``get_scene_info`` + per-body ``get_bounding_box``
  (sizes reported in cm -> normalized to mm for the inspector).
"""
from __future__ import annotations

from typing import Any, Optional

MM_PER_CM = 10.0


class UnsupportedTranslation(Exception):
    """The semantic operation cannot be mapped onto this server's tools."""


def _cm(mm: float) -> float:
    return round(float(mm) / MM_PER_CM, 4)


def _deg(value: Any) -> float:
    return float(value)


class FaustTranslator:
    """Semantic plan operations -> faust-machines MCP tool arguments."""

    profile = "faust"

    # constraint types our planner emits that map cleanly
    CONSTRAINT_MAP = {
        "coincident": "coincident",
        "parallel": "parallel",
        "perpendicular": "perpendicular",
        "tangent": "tangent",
        "equal": "equal",
        "fix": "fix",
        "midpoint": "midpoint",
        "concentric": "concentric",
        "horizontal": "horizontal",
        "vertical": "vertical",
        "symmetric": "symmetry",
        "symmetry": "symmetry",
        "collinear": "collinear",
        "smooth": "smooth",
    }

    def tool_for(self, operation: str, tools: dict[str, dict]) -> Optional[str]:
        """Explicit op->tool resolution for this server (falls back to fuzzy)."""
        explicit = {
            "create_component": "create_component",
            "create_sketch": "create_sketch",
            "draw_rectangle": "draw_rectangle",
            "draw_circle": "draw_circle",
            "add_dimension": "add_dimension",
            "add_constraint": "add_constraint",
            "extrude": "extrude",
            "cut_extrude": "extrude",           # extrude(operation="cut")
            "fillet": "fillet",
            "chamfer": "chamfer",
            "rectangular_pattern": "rectangular_pattern",
            "inspect_model": "get_scene_info",
            "export_model": "export",
            "delete_body": "boolean_operation",  # never auto-approved; see translate()
        }
        target = explicit.get(operation)
        if target and target in tools:
            return target
        return None if target else None  # let registry fuzzy-match unknown ops

    def translate(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        """Convert semantic (mm-based) step args into faust tool args."""
        a = dict(args)

        if operation == "create_component":
            return {"name": a.get("name", "Component1")}

        if operation == "create_sketch":
            plane = str(a.get("plane", "xy")).lower().replace("plane", "")
            if plane not in ("xy", "yz", "xz"):
                plane = "xy"
            out: dict[str, Any] = {"plane": plane}
            if a.get("z_offset_mm") is not None:
                out["z_offset"] = _cm(a["z_offset_mm"])
            return out  # sketch name kept in plan state; server uses most-recent

        if operation == "draw_rectangle":
            return {
                "width": _cm(a["width_mm"]),
                "height": _cm(a.get("depth_mm", a.get("height_mm", a["width_mm"]))),
                **({"origin_x": _cm(a["x_mm"])} if a.get("x_mm") is not None else {}),
                **({"origin_y": _cm(a["y_mm"])} if a.get("y_mm") is not None else {}),
            }

        if operation == "draw_circle":
            diameter = a.get("diameter_mm")
            if diameter is None and a.get("radius_mm") is not None:
                diameter = float(a["radius_mm"]) * 2
            if diameter is None:
                raise UnsupportedTranslation("draw_circle needs diameter_mm or radius_mm")
            return {
                "radius": _cm(float(diameter) / 2),
                **({"center_x": _cm(a["x_mm"])} if a.get("x_mm") is not None else {}),
                **({"center_y": _cm(a["y_mm"])} if a.get("y_mm") is not None else {}),
                **({"center_z": _cm(a["z_mm"])} if a.get("z_mm") is not None else {}),
            }

        if operation == "add_dimension":
            name = str(a.get("name", ""))
            is_angle = name.endswith("_deg") or "angle" in name
            if is_angle:
                value = _deg(a.get("value_deg", a.get("value")))
                return {"dimension_type": "angular", "value": value,
                        **({"sketch_name": a["sketch"]} if a.get("sketch") else {})}
            value = a.get("value_mm", a.get("value"))
            dim_type = "diameter" if ("hole" in name or "diameter" in name) else "distance"
            return {"dimension_type": dim_type, "value": _cm(value),
                    **({"sketch_name": a["sketch"]} if a.get("sketch") else {})}

        if operation == "add_constraint":
            ctype = self.CONSTRAINT_MAP.get(str(a.get("type", "")).lower())
            if ctype is None:
                # e.g. generic "angle" constraints need entity indices we don't
                # track semantically; skip rather than corrupt the sketch.
                raise UnsupportedTranslation(
                    f"constraint type '{a.get('type')}' not mappable without entity indices")
            return {"constraint_type": ctype,
                    **({"sketch_name": a["sketch"]} if a.get("sketch") else {})}

        if operation == "extrude":
            return {"height": _cm(a["distance_mm"]), "operation": "new_body",
                    "direction": a.get("direction", "positive")}

        if operation == "cut_extrude":
            return {"height": _cm(a["distance_mm"]), "operation": "cut",
                    "direction": a.get("direction", "negative")}

        if operation == "fillet":
            return {"radius": _cm(a["radius_mm"]),
                    **({"body_name": a["body_name"]} if a.get("body_name") else {}),
                    "edge_selection": a.get("edge_selection", "all")}

        if operation == "chamfer":
            return {"distance": _cm(a["distance_mm"]),
                    **({"body_name": a["body_name"]} if a.get("body_name") else {}),
                    "edge_selection": a.get("edge_selection", "all")}

        if operation == "rectangular_pattern":
            if not a.get("body_name"):
                raise UnsupportedTranslation("rectangular_pattern needs body_name")
            return {"body_name": a["body_name"],
                    "x_count": int(a.get("count_x", 2)),
                    "x_spacing": _cm(a.get("spacing_mm", 10))}

        if operation == "delete_body":
            raise UnsupportedTranslation(
                "single-body delete unsupported on this server; use 'undo' or 'delete_all' "
                "(destructive, requires approval)")

        if operation == "export_model":
            fmt = str(a.get("format", "stl")).lower()
            return {"path": a.get("path", "./model"), "format": fmt}

        if operation in ("inspect_model",):
            return {}

        # Unknown op: pass through untouched; schema validation still applies.
        return a


async def parse_scene_info(scene: dict[str, Any], bbox_fetch) -> dict[str, Any]:
    """Normalize ``get_scene_info`` (+ bounding boxes) into our doc summary.

    ``bbox_fetch(name)`` is an async callable returning the raw
    ``get_bounding_box`` payload for one body. All sizes cm -> mm.
    """
    import asyncio

    bodies_raw = scene.get("bodies") or []
    names: list[str] = []
    for b in bodies_raw:
        if isinstance(b, str):
            names.append(b)
        elif isinstance(b, dict):
            n = b.get("name") or b.get("bodyName")
            if n:
                names.append(str(n))

    async def one(name: str) -> tuple[str, dict[str, Any] | None]:
        try:
            raw = await bbox_fetch(name)
        except Exception:
            # Server may not expose bbox (e.g. mock mode) — keep the name so
            # body-count checks still work.
            return name, {"bbox": None, "volume_mm3": None}
        size = raw.get("size") or {}
        w, d, h = size.get("x"), size.get("y"), size.get("z")
        if None in (w, d, h):
            mx, mn = raw.get("max"), raw.get("min")
            if mx and mn:
                w, d, h = (mx["x"] - mn["x"], mx["y"] - mn["y"], mx["z"] - mn["z"])
        if None in (w, d, h):
            return name, {"bbox": None, "volume_mm3": raw.get("volume_mm3")}
        return name, {"bbox": {"w": round(w * MM_PER_CM, 2),
                               "d": round(d * MM_PER_CM, 2),
                               "h": round(h * MM_PER_CM, 2)},
                      "volume_mm3": raw.get("volume_mm3")}

    results = await asyncio.gather(*(one(n) for n in names))
    bodies = {name: info for name, info in results if info}

    sketches_raw = scene.get("sketches") or []
    return {
        "components": [c if isinstance(c, str) else c.get("name", "?")
                       for c in (scene.get("components") or [])],
        "sketch_count": len(sketches_raw),
        "bodies": bodies,
        "dimension_count": len(scene.get("dimensions") or scene.get("parameters") or []),
        "constraint_count": len(scene.get("constraints") or []),
        # faust-machines servers tag responses "mode: mock|socket"; mock mode
        # returns static scene data, which inspectors should not treat as
        # evidence of modeling failure.
        "mode": scene.get("mode"),
    }
