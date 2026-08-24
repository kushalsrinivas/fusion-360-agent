"""Mock bridge behavior + plan template sanity."""
import pytest

from app.fusion.mcp_client import MockFusionClient, BridgeError
from app.graph.nodes.planner import build_plan
from app.models.requirements import CADRequirements
from app.models.cad_plan import StepStatus


@pytest.mark.asyncio
async def test_mock_bridge_full_document_flow():
    bridge = MockFusionClient()
    await bridge.connect()
    tools = await bridge.list_tools()
    assert any(t["name"] == "extrude" for t in tools)

    r = await bridge.call_tool("unknown_tool", {})
    assert r["success"] is False

    await bridge.call_tool("create_sketch", {"name": "s1", "plane": "XY"})
    await bridge.call_tool("draw_rectangle", {"sketch": "s1", "width_mm": 80, "depth_mm": 5})
    r = await bridge.call_tool("extrude", {"sketch": "s1", "distance_mm": 5, "body_name": "plate"})
    assert r["success"] is True
    inspect = await bridge.call_tool("inspect_model", {})
    assert inspect["bodies"]["plate"]["volume_mm3"] > 0

    # Missing required args rejected
    r = await bridge.call_tool("extrude", {"sketch": "s1"})
    assert r["success"] is False

    await bridge.disconnect()
    with pytest.raises(BridgeError):
        await bridge.call_tool("inspect_model", {})


def test_template_plan_structure_and_defaults():
    req = CADRequirements(description="phone stand")
    from app.graph.nodes.requirements import _extract_heuristics, apply_defaults
    req = _extract_heuristics("wall-mounted phone holder with rounded edges and screw holes")
    apply_defaults(req)
    plan = build_plan(req)
    ops = [s.operation for s in plan.steps]
    assert ops[0] == "create_component"
    assert "create_sketch" in ops and "add_dimension" in ops
    assert ops.count("extrude") >= 2                    # backplate + support
    assert ops[-1] == "inspect_model"
    assert all(s.status == StepStatus.PLANNED for s in plan.steps)
    bodies = plan.body_names()
    assert len(bodies) >= 2
    # Mounting holes -> cut operations present
    assert "cut_extrude" in ops
    # Rounded edges -> fillet present
    assert "fillet" in ops


def test_plan_step_destructive_flagged():
    from app.models.cad_plan import PlanStep
    step = PlanStep(id="x", operation="delete_body", args={"body_name": "b"})
    assert step.is_destructive() is True
