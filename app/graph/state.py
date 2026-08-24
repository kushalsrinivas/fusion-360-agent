"""Central LangGraph state for the CAD agent."""
from __future__ import annotations

import operator
from typing import Annotated, Any, Optional

from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    # --- conversation ---
    session_id: str
    user_request: str
    request_history: list[str]
    is_followup: bool

    # --- structured understanding ---
    requirements: Optional[dict[str, Any]]        # CADRequirements.model_dump()
    missing_info: list[str]

    # --- planning / execution ---
    plan: Optional[dict[str, Any]]                # CADPlan.model_dump()
    tool_calls: Annotated[list[dict[str, Any]], operator.add]
    inspection: Optional[dict[str, Any]]

    # --- control flow ---
    errors: Annotated[list[dict[str, Any]], operator.add]
    repair_attempts: int
    status: str                                   # running | awaiting_approval | success | failed
    final_summary: Optional[str]
    pending_approval: Optional[dict[str, Any]]
    approval_response: Optional[bool]

    # --- runtime handles (not persisted) ---
    bridge: Any                                   # FusionBridge instance
    registry: Any                                 # ToolRegistry instance


def initial_state(session_id: str, user_request: str,
                  prior_requirements: dict | None = None) -> AgentState:
    return AgentState(
        session_id=session_id,
        user_request=user_request,
        request_history=[user_request],
        is_followup=prior_requirements is not None,
        requirements=prior_requirements,
        missing_info=[],
        plan=None,
        tool_calls=[],
        inspection=None,
        errors=[],
        repair_attempts=0,
        status="running",
        final_summary=None,
        pending_approval=None,
        approval_response=None,
        bridge=None,
        registry=None,
    )
