"""CAD plan model: an ordered, validated sequence of Fusion operations."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class StepStatus(str, Enum):
    PLANNED = "planned"
    EXECUTED = "executed"
    VERIFIED = "verified"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_APPROVAL = "awaiting_approval"


# Operations the planner may emit. Anything outside this set is rejected
# before it can reach Fusion (safety: no free-form tool invocation).
KNOWN_OPERATIONS = {
    # Component & Sketches
    "create_component",
    "create_sketch",
    "create_construction_plane",
    # Sketch Curves & Primitives
    "draw_rectangle",
    "draw_circle",
    "draw_line",
    "draw_arc",
    "draw_spline",
    "create_polygon",
    # Constraints & Dimensions
    "add_dimension",
    "add_constraint",
    # 3D Features & Solids
    "extrude",
    "cut_extrude",
    "revolve",
    "loft",
    "sweep",
    "shell",
    "create_hole",
    "fillet",
    "chamfer",
    # Patterns, Symmetry & Transforms
    "circular_pattern",
    "rectangular_pattern",
    "mirror",
    "boolean_operation",
    "move_body",
    "scale_body",
    # Direct 3D Primitives
    "create_box",
    "create_cylinder",
    "create_sphere",
    "create_torus",
    # Inspection & Management
    "set_appearance",
    "delete_body",          # destructive -> requires approval
    "delete_all",           # destructive -> requires approval
    "inspect_model",
    "export_model",
}

DESTRUCTIVE_OPERATIONS = {"delete_body", "delete_all"}


class PlanStep(BaseModel):
    id: str
    index: int = 0
    operation: str
    description: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    status: StepStatus = StepStatus.PLANNED
    requires_approval: bool = False
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    def is_destructive(self) -> bool:
        return self.operation in DESTRUCTIVE_OPERATIONS


class ToolCallRecord(BaseModel):
    step_id: str
    operation: str
    mcp_tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = False
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""
    expected: Optional[Any] = None
    actual: Optional[Any] = None


class InspectionReport(BaseModel):
    passed: bool
    checks: list[CheckResult] = Field(default_factory=list)
    summary: str = ""

    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


class CADPlan(BaseModel):
    object_name: str = "part"
    strategy: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    revision: int = 0

    def reindex(self) -> None:
        for i, step in enumerate(self.steps):
            step.index = i

    def next_planned(self) -> PlanStep | None:
        for step in self.steps:
            if step.status == StepStatus.PLANNED:
                return step
        return None

    def all_done(self) -> bool:
        return all(s.status in (StepStatus.VERIFIED, StepStatus.SKIPPED) for s in self.steps)

    def has_failures(self) -> bool:
        return any(s.status == StepStatus.FAILED for s in self.steps)

    def body_names(self) -> list[str]:
        """Expected solid bodies implied by solid creation steps."""
        names = []
        body_creators = {
            "extrude", "revolve", "loft", "sweep",
            "create_box", "create_cylinder", "create_sphere", "create_torus",
            "boolean_operation",
        }
        for s in self.steps:
            if s.operation in body_creators:
                name = s.args.get("body_name") or s.args.get("name") or s.args.get("target_body")
                if name and str(name) not in names:
                    names.append(str(name))
        return names

    def to_prompt(self) -> str:
        lines = [f"Plan for '{self.object_name}' (rev {self.revision}): {self.strategy}", ""]
        for s in self.steps:
            marker = {
                StepStatus.PLANNED: " ",
                StepStatus.EXECUTED: "*",
                StepStatus.VERIFIED: "+",
                StepStatus.FAILED: "x",
                StepStatus.SKIPPED: "-",
                StepStatus.AWAITING_APPROVAL: "?",
            }[s.status]
            lines.append(f"{marker} [{s.id}] {s.operation}: {s.description} args={s.args}")
        return "\n".join(lines)
