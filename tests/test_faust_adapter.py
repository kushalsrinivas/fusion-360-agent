"""Faust Machines adapter: unit conversion + operation translation."""
import pytest

from app.fusion.faust_adapter import FaustTranslator, UnsupportedTranslation

# Minimal subset of faust-machines/fusion360-mcp-server tool schemas
# (from src/fusion360_mcp/tools.py on the upstream repo).
FAUST_TOOLS = {
    t["name"]: {"name": t["name"], "inputSchema": t["inputSchema"]}
    for t in [
        {"name": "create_component", "inputSchema": {"type": "object",
            "properties": {"name": {"type": "string"}}, "required": ["name"]}},
        {"name": "create_sketch", "inputSchema": {"type": "object", "properties": {
            "plane": {"type": "string"}, "z_offset": {"type": "number"}}}},
        {"name": "draw_rectangle", "inputSchema": {"type": "object",
            "properties": {"width": {"type": "number"}, "height": {"type": "number"}},
            "required": ["width", "height"]}},
        {"name": "draw_circle", "inputSchema": {"type": "object",
            "properties": {"radius": {"type": "number"},
                           "center_x": {"type": "number"}, "center_y": {"type": "number"}},
            "required": ["radius"]}},
        {"name": "add_dimension", "inputSchema": {"type": "object", "properties": {
            "dimension_type": {"type": "string"}, "value": {"type": "number"},
            "entity_one": {"type": "integer"}, "entity_two": {"type": "integer"},
            "sketch_name": {"type": "string"}},
            "required": ["dimension_type", "value"]}},
        {"name": "add_constraint", "inputSchema": {"type": "object", "properties": {
            "constraint_type": {"type": "string"},
            "entity_one": {"type": "integer"}, "entity_two": {"type": "integer"},
            "sketch_name": {"type": "string"}},
            "required": ["constraint_type"]}},
        {"name": "extrude", "inputSchema": {"type": "object", "properties": {
            "height": {"type": "number"},
            "operation": {"type": "string", "enum": ["new_body", "join", "cut", "intersect"]},
            "direction": {"type": "string"}}, "required": ["height"]}},
        {"name": "fillet", "inputSchema": {"type": "object", "properties": {
            "radius": {"type": "number"}, "body_name": {"type": "string"},
            "edge_selection": {"type": "string"}}, "required": ["radius"]}},
        {"name": "chamfer", "inputSchema": {"type": "object", "properties": {
            "distance": {"type": "number"}, "body_name": {"type": "string"}},
            "required": ["distance"]}},
        {"name": "get_scene_info", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "get_bounding_box", "inputSchema": {"type": "object",
            "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    ]
}


@pytest.fixture()
def tr():
    return FaustTranslator()


def test_mm_to_cm_conversions(tr):
    out = tr.translate("extrude", {"sketch": "s1", "distance_mm": 5.0, "body_name": "b"})
    assert out == {"height": 0.5, "operation": "new_body", "direction": "positive"}

    rect = tr.translate("draw_rectangle", {"sketch": "s1", "width_mm": 80, "depth_mm": 5})
    assert rect["width"] == 8.0 and rect["height"] == 0.5


def test_circle_diameter_to_radius(tr):
    out = tr.translate("draw_circle", {"sketch": "h1", "diameter_mm": 5, "x_mm": 10, "y_mm": 0})
    assert out["radius"] == pytest.approx(0.25)   # Ø5mm -> r0.25cm
    assert out["center_x"] == 1.0


def test_cut_extrude_maps_to_extrude_cut(tr):
    out = tr.translate("cut_extrude", {"sketch": "slot", "distance_mm": 15,
                                       "target_body": "support"})
    assert out["operation"] == "cut"
    assert out["height"] == 1.5


def test_dimensions_and_constraints_skip_without_entity_indices(tr):
    # The real add-in resolves sketch entities by index and dereferences them
    # (e1.startSketchPoint); without tracked indices these must be skipped
    # cleanly (UnsupportedTranslation), never sent to Fusion.
    with pytest.raises(UnsupportedTranslation):
        tr.translate("add_dimension", {"sketch": "s1", "name": "slot_width",
                                       "value": 20})
    with pytest.raises(UnsupportedTranslation):
        tr.translate("add_constraint", {"sketch": "s1", "type": "symmetric"})


def test_unsupported_delete_body_raises(tr):
    with pytest.raises(UnsupportedTranslation):
        tr.translate("delete_body", {"body_name": "b"})


def test_registry_with_translator_end_to_end():
    from app.fusion.tool_registry import ToolRegistry
    tr = FaustTranslator()
    reg = ToolRegistry(list(FAUST_TOOLS.values()), translator=tr)

    # cut_extrude explicitly resolves to the extrude tool
    assert reg.resolve("cut_extrude") == "extrude"
    tool, args = reg.prepare_call("cut_extrude",
                                  {"sketch": "slot", "distance_mm": 15})
    assert tool == "extrude"
    assert args == {"height": 1.5, "operation": "cut", "direction": "negative"}

    # semantic mm args are stripped after translation to schema-valid cm args
    tool, args = reg.prepare_call("draw_rectangle",
                                  {"sketch": "s1", "width_mm": 80, "depth_mm": 5})
    assert tool == "draw_rectangle"
    assert set(args) == {"width", "height"}
    assert args["width"] == 8.0

    tool, args = reg.prepare_call("inspect_model", {})
    assert tool == "get_scene_info"


@pytest.mark.asyncio
async def test_parse_scene_info_normalizes_units():
    from app.fusion.faust_adapter import parse_scene_info

    async def bbox_fetch(name: str) -> dict:
        # Real add-in payload shape: size/min/max are LISTS [x, y, z] in cm.
        return {"size": [8.0, 0.5, 12.0]}

    scene = {"bodies": [{"name": "Body1"}, "Body2"], "sketches": ["a", "b"],
             "components": [], "constraints": [1, 2]}
    summary = await parse_scene_info(scene, bbox_fetch)
    assert summary["sketch_count"] == 2
    assert summary["bodies"]["Body1"]["bbox"]["w"] == 80.0   # cm -> mm
    assert summary["bodies"]["Body2"]["bbox"]["h"] == 120.0


@pytest.mark.asyncio
async def test_parse_scene_info_handles_min_max_fallback():
    from app.fusion.faust_adapter import parse_scene_info

    async def bbox_fetch(name: str) -> dict:
        return {"min": [0.0, 0.0, 0.0], "max": [4.0, 3.0, 2.5]}  # cm lists

    scene = {"bodies": ["Body1"]}
    summary = await parse_scene_info(scene, bbox_fetch)
    assert summary["bodies"]["Body1"]["bbox"] == {"w": 40.0, "d": 30.0, "h": 25.0}


def test_export_uses_file_path_arg(tr):
    out = tr.translate("export_model", {"format": "stl", "path": "/tmp/part"})
    assert out == {"file_path": "/tmp/part", "format": "stl"}
