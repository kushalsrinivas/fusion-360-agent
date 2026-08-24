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
    "create_component",
    "create_sketch",
    "draw_rectangle",
    "draw_circle",
    "add_dimension",
    "add_constraint",
    "extrude",
    "cut_extrude",
    "fillet",
    "chamfer",
    "rectangular_pattern",
    "delete_body",          # destructive -> requires approval
    "inspect_model",
    "export_model",
}

DESTRUCTIVE_OPERATIONS = {"delete_body"}


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
        """Expected solid bodies implied by extrude steps."""
        names = []
        for s in self.steps:
            if s.operation == "extrude":
                name = s.args.get("body_name") or s.args.get("name")
                if name:
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
