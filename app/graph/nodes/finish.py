"""Finish node: produce the final, honest model summary.

Distinguishes planned vs executed vs verified. Never claims success unless
the inspector confirmed it against the live Fusion document.
"""
from __future__ import annotations

from typing import Any

from app.models.events import EventBus, EventType
from app.models.cad_plan import CADPlan, InspectionReport, StepStatus
from app.models.requirements import CADRequirements
from app.graph.state import AgentState
from app.storage.history import HistoryStore

NODE = "finish"


def summarize(plan: CADPlan, requirements: CADRequirements | None,
              inspection: dict[str, Any] | None,
              repair_attempts: int) -> str:
    verified = [s for s in plan.steps if s.status == StepStatus.VERIFIED]
    executed = [s for s in plan.steps if s.status == StepStatus.EXECUTED]
    failed = [s for s in plan.steps if s.status == StepStatus.FAILED]

    lines: list[str] = []
    if inspection and inspection.get("passed"):
        lines.append(f"✓ Model '{plan.object_name}' created and VERIFIED in Fusion 360.")
    elif failed:
        lines.append(f"✗ Model '{plan.object_name}' NOT completed — execution failures remain.")
    else:
        lines.append(f"? Model '{plan.object_name}' could not be fully verified.")

    if requirements:
        lines.append(f"Design (rev {requirements.revision}): {requirements.to_prompt()}")
    lines.append(f"Strategy: {plan.strategy}")
    lines.append(f"Steps: {len(verified)} verified / {len(executed)} executed-unverified "
                 f"/ {len(failed)} failed / {len(plan.steps)} total"
                 + (f", {repair_attempts} repair attempt(s)" if repair_attempts else ""))

    report = InspectionReport(**(inspection or {"passed": False}))
    if report.checks:
        lines.append("Validation:")
        for c in report.checks:
            mark = "+" if c.passed else "x"
            lines.append(f"  [{mark}] {c.name}: {c.detail}")

    bodies = plan.body_names()
    if inspection and inspection.get("passed") and bodies:
        lines.append(f"Bodies: {', '.join(bodies)}")
    return "\n".join(lines)


async def finish_node(state: AgentState) -> dict[str, Any]:
    bus = EventBus.get()
    plan = CADPlan(**state["plan"]) if state.get("plan") else None
    requirements = CADRequirements(**state["requirements"]) if state.get("requirements") else None

    if state.get("final_summary"):
        summary = state["final_summary"]
    elif state.get("status") == "needs_input":
        questions = state.get("missing_info") or ["Could not determine requirements."]
        summary = "? More information needed:\n" + "\n".join(f"  - {q}" for q in questions)
    elif plan is None:
        summary = "✗ Nothing was planned — no model was created."
    else:
        summary = summarize(plan, requirements, state.get("inspection"),
                            int(state.get("repair_attempts", 0)))

    final_status = "success" if summary.startswith("✓") else \
                   ("needs_input" if summary.startswith("?") and state.get("status") == "needs_input" else "failed")

    bus.emit(NODE, EventType.SUMMARY, summary)
    bus.emit(NODE, EventType.STATUS, f"Run complete ({final_status})")

    # Persist session artifacts.
    store = HistoryStore.default()
    store.save_run(
        session_id=state.get("session_id", "unknown"),
        request_history=state.get("request_history", []),
        requirements=requirements.model_dump() if requirements else None,
        plan=plan.model_dump() if plan else None,
        tool_calls=state.get("tool_calls", []),
        inspection=state.get("inspection"),
        summary=summary,
        status=final_status,
    )

    return {
        "final_summary": summary,
        "status": final_status,
        "requirements": requirements.model_dump() if requirements else None,
    }
