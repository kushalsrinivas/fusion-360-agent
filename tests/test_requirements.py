"""Unit tests for the requirement-extraction heuristics."""
from app.graph.nodes.requirements import _extract_heuristics, apply_defaults
from app.models.requirements import CADRequirements


def test_full_request_extraction():
    text = ("Create a wall-mounted phone holder with a 5 mm thick backplate, "
            "two screw holes, rounded edges, and a 20-degree viewing angle.")
    req = _extract_heuristics(text)
    assert req.mounting is True
    assert "screw_holes" in req.features
    assert "rounded_edges" in req.features
    thick = req.dim("thickness")
    assert thick and thick.value_mm == 5.0
    angle = req.dim("viewing_angle")
    assert angle and angle.value_deg == 20.0


def test_bare_dimension_words():
    req = _extract_heuristics("phone stand 80mm wide with a slot")
    width = req.dim("width")
    assert width and width.value_mm == 80.0
    assert "phone_slot" in req.features or req.has_feature("slot")


def test_defaults_fill_missing_dims():
    req = _extract_heuristics("make me a bracket")
    apply_defaults(req)
    for name in ("width", "depth", "height", "thickness", "hole_diameter"):
        d = req.dim(name)
        assert d is not None and d.value_mm is not None, name
    assert req.dim("viewing_angle").value_deg == 20.0
    for d in req.dimensions:
        if d.source == "default":
            continue


def test_merge_followup():
    base = _extract_heuristics("create a phone stand 80mm wide")
    apply_defaults(base)
    followup = _extract_heuristics("add two mounting holes")
    merged = base.merge(followup)
    assert merged.revision == 1
    assert merged.mounting is True
    assert merged.dim("width").value_mm == 80.0  # context preserved
