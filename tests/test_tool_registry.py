"""ToolRegistry: operation->tool mapping and argument validation."""
import pytest

from app.fusion.tool_registry import ToolRegistry, ArgumentValidationError


def make_registry() -> ToolRegistry:
    tools = [
        {"name": "fusion360_extrude", "inputSchema": {
            "type": "object",
            "properties": {"sketch": {"type": "string"},
                           "distance_mm": {"type": "number"},
                           "body_name": {"type": "string"}},
            "required": ["sketch", "distance_mm"]}},
        {"name": "inspect_model", "inputSchema": {"type": "object", "properties": {}}},
    ]
    return ToolRegistry(tools)


def test_fuzzy_resolution():
    reg = make_registry()
    assert reg.resolve("extrude") == "fusion360_extrude"
    assert reg.resolve("inspect_model") == "inspect_model"
    supported, missing = reg.coverage_report()
    assert "extrude" in supported
    assert "fillet" in missing  # not offered by this server
    with pytest.raises(Exception):
        reg.resolve("fillet") or (_ for _ in ()).throw(RuntimeError())


def test_validation_strips_unknown_and_coerces():
    reg = make_registry()
    cleaned = reg.validate_args("extrude", {
        "sketch": "s1",
        "distance_mm": "5",       # string -> number coercion
        "evil_key": "drop me",    # unknown key stripped
    })
    assert cleaned["distance_mm"] == 5.0
    assert "evil_key" not in cleaned


def test_validation_missing_required():
    reg = make_registry()
    with pytest.raises(ArgumentValidationError):
        reg.validate_args("extrude", {"sketch": "s1"})


def test_unresolvable_operation_raises():
    reg = make_registry()
    from app.fusion.tool_registry import ToolResolutionError
    with pytest.raises(ToolResolutionError):
        reg.validate_args("teleport_body", {})
