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
            "create_construction_plane": "create_construction_plane",
            "draw_rectangle": "draw_rectangle",
            "draw_circle": "draw_circle",
            "draw_line": "draw_line",
            "draw_arc": "draw_arc",
            "draw_spline": "draw_spline",
            "create_polygon": "create_polygon",
            "add_dimension": "add_dimension",
            "add_constraint": "add_constraint",
            "extrude": "extrude",
            "cut_extrude": "extrude",           # extrude(operation="cut")
            "revolve": "revolve",
            "loft": "loft",
            "sweep": "sweep",
            "shell": "shell",
            "create_hole": "create_hole",
            "fillet": "fillet",
            "chamfer": "chamfer",
            "circular_pattern": "circular_pattern",
            "rectangular_pattern": "rectangular_pattern",
            "mirror": "mirror",
            "boolean_operation": "boolean_operation",
            "create_box": "create_box",
            "create_cylinder": "create_cylinder",
            "create_sphere": "create_sphere",
            "create_torus": "create_torus",
            "move_body": "move_body",
            "scale_body": "scale_body",
            "set_appearance": "set_appearance",
            "delete_all": "delete_all",
            "inspect_model": "get_scene_info",
            "export_model": "export_stl" if "export_stl" in tools else "export_model",
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
            if a.get("z_offset_mm") is not None or a.get("z_offset") is not None:
                out["z_offset"] = _cm(float(a.get("z_offset_mm", a.get("z_offset", 0.0))))
            return out  # sketch name kept in plan state; server uses most-recent

        if operation == "create_construction_plane":
            method = a.get("method", "offset")
            plane = str(a.get("plane", "xy")).lower().replace("plane", "")
            if plane not in ("xy", "yz", "xz"):
                plane = "xy"
            offset = _cm(float(a.get("offset_mm", a.get("offset", 0.0))))
            angle = float(a.get("angle_deg", a.get("angle", 0.0)))
            return {
                "method": method,
                "plane": plane,
                "offset": offset,
                **({"angle": angle} if "angle" in a or "angle_deg" in a else {}),
            }

        if operation == "draw_rectangle":
            width = (a.get("width_mm") or a.get("width") or a.get("length_mm") or
                     a.get("length") or a.get("w") or a.get("size_mm") or a.get("size") or 10.0)
            height = (a.get("depth_mm") or a.get("depth") or a.get("height_mm") or
                      a.get("height") or a.get("d") or a.get("h") or a.get("length_mm") or
                      a.get("length") or width)
            out = {
                "width": _cm(float(width)),
                "height": _cm(float(height)),
            }
            if any(k in a for k in ("x_mm", "origin_x", "x")):
                out["origin_x"] = _cm(float(a.get("x_mm", a.get("origin_x", a.get("x", 0.0)))))
            if any(k in a for k in ("y_mm", "origin_y", "y")):
                out["origin_y"] = _cm(float(a.get("y_mm", a.get("origin_y", a.get("y", 0.0)))))
            return out

        if operation == "draw_circle":
            diameter = (a.get("diameter_mm") or a.get("diameter") or a.get("dia") or a.get("d") or
                        (a.get("value") if a.get("type") in ("diameter", "circle", None) else None))
            if diameter is None and (a.get("radius_mm") or a.get("radius") or a.get("r")) is not None:
                diameter = float(a.get("radius_mm") or a.get("radius") or a.get("r")) * 2
            if diameter is None:
                diameter = 10.0
            return {
                "radius": _cm(float(diameter) / 2),
                **({"center_x": _cm(float(a.get("center_x_mm", a.get("center_x", a.get("x_mm", a.get("x", 0.0))))))} if any(k in a for k in ("center_x_mm", "center_x", "x_mm", "x")) else {}),
                **({"center_y": _cm(float(a.get("center_y_mm", a.get("center_y", a.get("y_mm", a.get("y", 0.0))))))} if any(k in a for k in ("center_y_mm", "center_y", "y_mm", "y")) else {}),
                **({"center_z": _cm(float(a.get("center_z_mm", a.get("center_z", a.get("z_mm", a.get("z", 0.0))))))} if any(k in a for k in ("center_z_mm", "center_z", "z_mm", "z")) else {}),
            }

        if operation == "draw_line":
            return {
                "start_x": _cm(float(a.get("start_x_mm", a.get("start_x", 0.0)))),
                "start_y": _cm(float(a.get("start_y_mm", a.get("start_y", 0.0)))),
                "end_x": _cm(float(a.get("end_x_mm", a.get("end_x", 0.0)))),
                "end_y": _cm(float(a.get("end_y_mm", a.get("end_y", 0.0)))),
                **({"start_z": _cm(float(a["start_z_mm"]))} if "start_z_mm" in a or "start_z" in a else {}),
                **({"end_z": _cm(float(a["end_z_mm"]))} if "end_z_mm" in a or "end_z" in a else {}),
            }

        if operation == "draw_arc":
            return {
                "center_x": _cm(float(a.get("center_x_mm", a.get("center_x", 0.0)))),
                "center_y": _cm(float(a.get("center_y_mm", a.get("center_y", 0.0)))),
                "start_x": _cm(float(a.get("start_x_mm", a.get("start_x", 0.0)))),
                "start_y": _cm(float(a.get("start_y_mm", a.get("start_y", 0.0)))),
                "sweep_angle": float(a.get("sweep_angle_deg", a.get("sweep_angle", a.get("angle", 90.0)))),
            }

        if operation == "draw_spline":
            raw_pts = a.get("points", [])
            cm_pts = []
            for p in raw_pts:
                if isinstance(p, (list, tuple)):
                    cm_pts.append([_cm(float(val)) for val in p])
            return {
                "spline_type": a.get("spline_type", "fit_points"),
                "points": cm_pts,
                **({"degree": str(a["degree"])} if "degree" in a else {}),
            }

        if operation == "create_polygon":
            radius = a.get("radius_mm") or a.get("radius") or 10.0
            return {
                "sides": int(a.get("sides", 6)),
                "radius": _cm(float(radius)),
                **({"center_x": _cm(float(a.get("center_x_mm", a.get("center_x", 0.0))))} if any(k in a for k in ("center_x_mm", "center_x")) else {}),
                **({"center_y": _cm(float(a.get("center_y_mm", a.get("center_y", 0.0))))} if any(k in a for k in ("center_y_mm", "center_y")) else {}),
            }

        if operation == "add_dimension":
            # Their add-in dereferences entity_one/entity_two (e.g.
            # e1.startSketchPoint); without tracked sketch-entity indices any
            # call fails inside Fusion and halts the plan. Skip cleanly.
            raise UnsupportedTranslation(
                "dimensions require sketch-entity indices we don't track "
                "semantically; the sketch stays under-constrained but valid")

        if operation == "add_constraint":
            ctype = self.CONSTRAINT_MAP.get(str(a.get("type", "")).lower())
            # Same entity-index problem as dimensions: the add-in resolves
            # entities by index and would error on None. Skip rather than
            # halt the plan.
            raise UnsupportedTranslation(
                "constraints require sketch-entity indices we don't track "
                "semantically"
                + (f" (type '{ctype}' would map)" if ctype else ""))

        if operation == "extrude":
            distance = (a.get("distance_mm") or a.get("distance") or a.get("height_mm") or
                        a.get("height") or a.get("depth_mm") or a.get("thickness_mm") or
                        a.get("thickness") or a.get("length_mm") or a.get("size_mm") or
                        a.get("value") or 10.0)
            op = a.get("operation", "new_body")
            if op not in ("new_body", "join", "cut", "intersect"):
                op = "new_body"
            return {"height": _cm(float(distance)), "operation": op,
                    "direction": a.get("direction", "positive")}

        if operation == "cut_extrude":
            distance = (a.get("distance_mm") or a.get("distance") or a.get("height_mm") or
                        a.get("height") or a.get("depth_mm") or a.get("thickness_mm") or
                        a.get("value") or 10.0)
            return {"height": _cm(float(distance)), "operation": "cut",
                    "direction": a.get("direction", "negative")}

        if operation == "revolve":
            angle = float(a.get("angle_deg", a.get("angle", 360.0)))
            axis = str(a.get("axis", "z")).lower()
            dir_map = {
                "x": (1.0, 0.0, 0.0),
                "y": (0.0, 1.0, 0.0),
                "z": (0.0, 0.0, 1.0),
            }
            dx, dy, dz = dir_map.get(axis, (0.0, 0.0, 1.0))
            if "axis_direction_x" in a:
                dx, dy, dz = float(a["axis_direction_x"]), float(a["axis_direction_y"]), float(a["axis_direction_z"])
            return {
                "angle": angle,
                "axis_direction_x": dx,
                "axis_direction_y": dy,
                "axis_direction_z": dz,
                "axis_origin_x": _cm(float(a.get("axis_origin_x", 0.0))),
                "axis_origin_y": _cm(float(a.get("axis_origin_y", 0.0))),
                "axis_origin_z": _cm(float(a.get("axis_origin_z", 0.0))),
                "operation": a.get("operation", "new_body"),
            }

        if operation == "loft":
            sketches = a.get("profile_sketch_names") or a.get("sketches") or []
            return {
                "profile_sketch_names": [str(s) for s in sketches],
                "operation": a.get("operation", "new_body"),
            }

        if operation == "sweep":
            return {
                "profile_index": int(a.get("profile_index", 0)),
                "path_sketch_name": str(a.get("path_sketch_name", "PathSketch")),
                "path_curve_index": int(a.get("path_curve_index", 0)),
                "operation": a.get("operation", "new_body"),
            }

        if operation == "shell":
            thick = a.get("thickness_mm") or a.get("thickness") or a.get("wall_thickness_mm") or a.get("value") or 2.0
            out = {
                "thickness": _cm(float(thick)),
                "face_selection": a.get("face_selection", "top"),
            }
            if a.get("body_name") and str(a["body_name"]).startswith("Body"):
                out["body_name"] = a["body_name"]
            else:
                out["body_index"] = int(a.get("body_index", 0))
            return out

        if operation == "create_hole":
            dia = a.get("diameter_mm") or a.get("diameter") or 5.0
            depth = a.get("depth_mm") or a.get("depth") or 10.0
            out = {
                "diameter": _cm(float(dia)),
                "depth": _cm(float(depth)),
                "face_selection": a.get("face_selection", "top"),
            }
            if any(k in a for k in ("center_x_mm", "center_x", "x_mm", "x")):
                out["center_x"] = _cm(float(a.get("center_x_mm", a.get("center_x", a.get("x_mm", a.get("x", 0.0))))))
            if any(k in a for k in ("center_y_mm", "center_y", "y_mm", "y")):
                out["center_y"] = _cm(float(a.get("center_y_mm", a.get("center_y", a.get("y_mm", a.get("y", 0.0))))))
            if a.get("body_name") and str(a["body_name"]).startswith("Body"):
                out["body_name"] = a["body_name"]
            else:
                out["body_index"] = int(a.get("body_index", 0))
            return out

        if operation == "fillet":
            radius = a.get("radius_mm") or a.get("radius") or a.get("r") or a.get("value") or 1.0
            out = {
                "radius": _cm(float(radius)),
                "edge_selection": a.get("edge_selection", "all"),
            }
            if a.get("body_name") and str(a["body_name"]).startswith("Body"):
                out["body_name"] = a["body_name"]
            else:
                out["body_index"] = int(a.get("body_index", 0))
            return out

        if operation == "chamfer":
            distance = a.get("distance_mm") or a.get("distance") or a.get("chamfer_mm") or a.get("d") or a.get("value") or 1.0
            out = {
                "distance": _cm(float(distance)),
                "edge_selection": a.get("edge_selection", "all"),
            }
            if a.get("body_name") and str(a["body_name"]).startswith("Body"):
                out["body_name"] = a["body_name"]
            else:
                out["body_index"] = int(a.get("body_index", 0))
            return out

        if operation == "circular_pattern":
            return {
                "body_name": a.get("body_name", "Body1"),
                "count": int(a.get("count", 4)),
                "axis": str(a.get("axis", "z")).lower(),
                "total_angle": float(a.get("total_angle_deg", a.get("total_angle", 360.0))),
            }

        if operation == "rectangular_pattern":
            if not a.get("body_name"):
                raise UnsupportedTranslation("rectangular_pattern needs body_name")
            return {"body_name": a["body_name"],
                    "x_count": int(a.get("count_x", a.get("x_count", 2))),
                    "x_spacing": _cm(float(a.get("spacing_mm", a.get("x_spacing_mm", a.get("x_spacing", 10.0))))),
                    **({"y_count": int(a["y_count"])} if "y_count" in a else {}),
                    **({"y_spacing": _cm(float(a["y_spacing_mm"]))} if "y_spacing_mm" in a else {})}

        if operation == "mirror":
            plane = str(a.get("mirror_plane", a.get("plane", "xz"))).lower().replace("plane", "")
            return {
                "body_name": a.get("body_name", "Body1"),
                "mirror_plane": plane if plane in ("xy", "yz", "xz") else "xz",
            }

        if operation == "boolean_operation":
            return {
                "operation": a.get("operation", "join"),
                "target_body": a.get("target_body", "Body1"),
                "tool_body": a.get("tool_body", "Body2"),
            }

        if operation == "create_box":
            return {
                "length": _cm(float(a.get("length_mm", a.get("length", 10.0)))),
                "width": _cm(float(a.get("width_mm", a.get("width", 10.0)))),
                "height": _cm(float(a.get("height_mm", a.get("height", 10.0)))),
                "center_x": _cm(float(a.get("center_x_mm", a.get("center_x", 0.0)))),
                "center_y": _cm(float(a.get("center_y_mm", a.get("center_y", 0.0)))),
                "center_z": _cm(float(a.get("center_z_mm", a.get("center_z", 0.0)))),
            }

        if operation == "create_cylinder":
            radius = a.get("radius_mm") or a.get("radius") or (float(a.get("diameter_mm", 20.0)) / 2)
            return {
                "radius": _cm(float(radius)),
                "height": _cm(float(a.get("height_mm", a.get("height", 10.0)))),
                "axis": str(a.get("axis", "z")).lower(),
                "base_x": _cm(float(a.get("base_x_mm", a.get("base_x", 0.0)))),
                "base_y": _cm(float(a.get("base_y_mm", a.get("base_y", 0.0)))),
                "base_z": _cm(float(a.get("base_z_mm", a.get("base_z", 0.0)))),
            }

        if operation == "create_sphere":
            radius = a.get("radius_mm") or a.get("radius") or (float(a.get("diameter_mm", 20.0)) / 2)
            return {
                "radius": _cm(float(radius)),
                "center_x": _cm(float(a.get("center_x_mm", a.get("center_x", 0.0)))),
                "center_y": _cm(float(a.get("center_y_mm", a.get("center_y", 0.0)))),
                "center_z": _cm(float(a.get("center_z_mm", a.get("center_z", 0.0)))),
            }

        if operation == "create_torus":
            major_r = a.get("major_radius_mm") or a.get("major_radius") or 20.0
            minor_r = a.get("minor_radius_mm") or a.get("minor_radius") or 5.0
            return {
                "major_radius": _cm(float(major_r)),
                "minor_radius": _cm(float(minor_r)),
                "axis": str(a.get("axis", "z")).lower(),
                "center_x": _cm(float(a.get("center_x_mm", a.get("center_x", 0.0)))),
                "center_y": _cm(float(a.get("center_y_mm", a.get("center_y", 0.0)))),
                "center_z": _cm(float(a.get("center_z_mm", a.get("center_z", 0.0)))),
            }

        if operation == "move_body":
            return {
                "body_name": a.get("body_name", "Body1"),
                "x": _cm(float(a.get("x_mm", a.get("x", 0.0)))),
                "y": _cm(float(a.get("y_mm", a.get("y", 0.0)))),
                "z": _cm(float(a.get("z_mm", a.get("z", 0.0)))),
            }

        if operation == "scale_body":
            return {
                "body_name": a.get("body_name", "Body1"),
                "scale": float(a.get("scale", 1.0)),
                "anchor_x": _cm(float(a.get("anchor_x_mm", a.get("anchor_x", 0.0)))),
                "anchor_y": _cm(float(a.get("anchor_y_mm", a.get("anchor_y", 0.0)))),
                "anchor_z": _cm(float(a.get("anchor_z_mm", a.get("anchor_z", 0.0)))),
            }

        if operation == "set_appearance":
            return {
                "target_name": a.get("target_name", a.get("body_name", "Body1")),
                "target_type": a.get("target_type", "body"),
                "appearance_name": a.get("appearance_name", "Aluminum - Anodized Red"),
            }

        if operation == "delete_all":
            return {}

        if operation == "delete_body":
            raise UnsupportedTranslation(
                "single-body delete unsupported on this server; use 'undo' or 'delete_all' "
                "(destructive, requires approval)")

        if operation == "export_model":
            fmt = str(a.get("format", "stl")).lower()
            return {"file_path": a.get("path", a.get("file_path", "./model")), "format": fmt}

        if operation in ("inspect_model",):
            return {}

        # Unknown op: pass through untouched; schema validation still applies.
        return a


def _xyz(triple: Any) -> tuple[Any, Any, Any]:
    """Extract (x, y, z) from a list [x,y,z] or dict {x,y,z} payload.

    The real add-in and mock return lists (e.g. ``"size": [8.0, 0.5, 12.0]``);
    dicts are accepted defensively.
    """
    if isinstance(triple, (list, tuple)) and len(triple) == 3:
        return triple[0], triple[1], triple[2]
    if isinstance(triple, dict):
        return triple.get("x"), triple.get("y"), triple.get("z")
    return None, None, None


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
        w, d, h = _xyz(raw.get("size"))
        if None in (w, d, h):
            mx, mn = _xyz(raw.get("max")), _xyz(raw.get("min"))
            if None not in mx and None not in mn:
                w, d, h = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
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
