"""Sequential, verified execution of a CADPlan through the Fusion bridge."""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from app.models.cad_plan import (
    CADPlan,
    PlanStep,
    StepStatus,
    ToolCallRecord,
)
from app.models.events import EventBus, EventType
from app.fusion.mcp_client import FusionBridge
from app.fusion.tool_registry import ArgumentValidationError, ToolRegistry
from app.fusion.faust_adapter import UnsupportedTranslation

# Callback used for human-in-the-loop approval of destructive steps.
ApprovalFn = Callable[[PlanStep], Awaitable[bool]]


class ExecutionAborted(Exception):
    pass


class FusionExecutor:
    def __init__(
        self,
        bridge: FusionBridge,
        registry: ToolRegistry,
        node_name: str = "executor",
    ) -> None:
        self.bridge = bridge
        self.registry = registry
        self.node = node_name

    async def execute(
        self,
        plan: CADPlan,
        records: list[ToolCallRecord],
        request_approval: Optional[ApprovalFn] = None,
    ) -> None:
        """Execute all PLANNED steps in order.

        Never blindly continues past a failed step: the first failure stops
        execution; remaining steps stay PLANNED so the repair agent can act.
        """
        bus = EventBus.get()
        pending = [s for s in plan.steps if s.status == StepStatus.PLANNED]
        if not pending:
            return

        start_idx = plan.steps.index(pending[0])
        for step in plan.steps[start_idx:]:
            if step.status != StepStatus.PLANNED:
                continue

            # --- destructive operation gate -----------------------------
            if step.is_destructive():
                if request_approval is None:
                    step.status = StepStatus.SKIPPED
                    step.error = "Destructive op rejected: no approval channel"
                    bus.emit(self.node, EventType.WARNING,
                             f"Skipped destructive step [{step.id}] (no approval channel)")
                    continue
                bus.emit(self.node, EventType.APPROVAL,
                         f"Requesting approval for '{step.operation}': {step.description}")
                approved = await request_approval(step)
                if not approved:
                    step.status = StepStatus.SKIPPED
                    step.error = "Rejected by user"
                    bus.emit(self.node, EventType.WARNING,
                             f"Step [{step.id}] rejected by user — skipping")
                    continue

            await self._run_step(step, records, bus)

            if step.status == StepStatus.FAILED:
                bus.emit(self.node, EventType.ERROR,
                         f"Stopping at failed step [{step.id}]: {step.error}",
                         {"operation": step.operation})
                remaining = [s for s in plan.steps if s.status == StepStatus.PLANNED]
                for s in remaining:
                    s.status = StepStatus.SKIPPED
                    s.error = "Skipped due to earlier failure"
                return

    async def _run_step(self, step: PlanStep, records: list[ToolCallRecord],
                        bus: EventBus) -> None:
        bus.emit(self.node, EventType.TOOL_CALL,
                 f"[{step.id}] {step.operation}: {step.description}",
                 {"args": _compact(step.args)})

        try:
            tool_name = self.registry.resolve(step.operation)
            if tool_name is None:
                raise ToolResolutionErrorStub(step.operation)
            tool_name, clean_args = self.registry.prepare_call(step.operation, dict(step.args))
        except (ToolResolutionErrorStub, ArgumentValidationError) as exc:
            step.status = StepStatus.FAILED
            step.error = str(exc)
            records.append(ToolCallRecord(
                step_id=step.id, operation=step.operation, mcp_tool="<unresolved>",
                args=step.args, ok=False, error=str(exc),
            ))
            bus.emit(self.node, EventType.ERROR, str(exc))
            return
        except UnsupportedTranslation as exc:
            # Adapter cannot map this semantic op onto the server's tools —
            # skip (not fail); the inspector will catch missing features.
            step.status = StepStatus.SKIPPED
            step.error = f"Unsupported on this MCP server: {exc}"
            records.append(ToolCallRecord(
                step_id=step.id, operation=step.operation, mcp_tool="<skipped>",
                args=step.args, ok=False, error=step.error,
            ))
            bus.emit(self.node, EventType.WARNING,
                     f"[{step.id}] {step.operation} skipped: {exc}")
            return

        try:
            result = await self.bridge.call_tool(tool_name, clean_args)
        except Exception as exc:  # transport-level failure
            result = {"success": False, "error": f"MCP transport error: {exc}"}

        ok = bool(result.get("success", False))
        record = ToolCallRecord(
            step_id=step.id, operation=step.operation, mcp_tool=tool_name,
            args=clean_args, ok=ok, result=result if ok else None,
            error=result.get("error") if not ok else None,
        )
        records.append(record)

        if ok:
            # "executed" ≠ "verified": verification happens in the inspector.
            step.status = StepStatus.EXECUTED
            step.result = result
            bus.emit(self.node, EventType.TOOL_RESULT, f"MCP OK {tool_name}",
                     {"result": _compact(result)})
        else:
            step.status = StepStatus.FAILED
            step.result = result
            step.error = str(result.get("error", "unknown MCP error"))
            bus.emit(self.node, EventType.TOOL_RESULT,
                     f"MCP FAIL {tool_name}", {"error": step.error})


class ToolResolutionErrorStub(Exception):
    pass


def _compact(d: dict | None, limit: int = 220) -> dict | None:
    if not d:
        return d
    text = str(d)
    if len(text) > limit:
        return {"_preview": text[:limit] + "..."}
    return d
