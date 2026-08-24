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
            "sketch_name": {"type": "string"}},
            "required": ["dimension_type", "value"]}},
        {"name": "add_constraint", "inputSchema": {"type": "object", "properties": {
            "constraint_type": {"type": "string"}, "sketch_name": {"type": "string"}},
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


def test_dimension_types(tr):
    dist = tr.translate("add_dimension", {"sketch": "s1", "name": "slot_width", "value": 20})
    assert dist == {"dimension_type": "distance", "value": 2.0, "sketch_name": "s1"}

    ang = tr.translate("add_dimension", {"sketch": "s1", "name": "viewing_angle_deg",
                                         "value_deg": 20})
    assert ang["dimension_type"] == "angular" and ang["value"] == 20.0  # degrees untouched

    dia = tr.translate("add_dimension", {"sketch": "s1", "name": "hole_diameter", "value": 5})
    assert dia["dimension_type"] == "diameter" and dia["value"] == 0.5


def test_constraint_symmetric_maps_to_symmetry(tr):
    out = tr.translate("add_constraint", {"sketch": "s1", "type": "symmetric"})
    assert out["constraint_type"] == "symmetry"

    with pytest.raises(UnsupportedTranslation):
        tr.translate("add_constraint", {"sketch": "s1", "type": "angle"})


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
        return {"size": {"x": 8.0, "y": 0.5, "z": 12.0}}  # cm

    scene = {"bodies": [{"name": "Body1"}, "Body2"], "sketches": ["a", "b"],
             "components": [], "constraints": [1, 2]}
    summary = await parse_scene_info(scene, bbox_fetch)
    assert summary["sketch_count"] == 2
    assert summary["bodies"]["Body1"]["bbox"]["w"] == 80.0   # cm -> mm
    assert summary["bodies"]["Body2"]["bbox"]["h"] == 120.0
