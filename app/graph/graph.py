"""Graph assembly: the central LangGraph state machine.

    requirements -> planner -> review -> executor -> inspector
                        ^         |          |          |
                        |   (rejected)  (approval)   valid -> finish
                        |                      \      invalid -> repair
                        +-------- repair <-----/        (bounded)
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.config import Config, get_config
from app.fusion.mcp_client import create_bridge
from app.fusion.tool_registry import ToolRegistry
from app.graph.llm import set_runtime_config
from app.graph.state import AgentState
from app.graph.routing import (
    route_after_inspector,
    route_after_planner,
    route_after_requirements,
    route_after_repair,
    route_after_review,
)
from app.graph.nodes.requirements import requirements_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.review import review_node
from app.graph.nodes.executor import executor_node
from app.graph.nodes.inspector import inspector_node
from app.graph.nodes.repair import repair_node
from app.graph.nodes.finish import finish_node


async def prepare_runtime(state: AgentState) -> dict:
    """Connect to Fusion MCP and discover tools before the pipeline starts."""
    bridge = state.get("bridge")
    if bridge is None:
        raise RuntimeError("No Fusion bridge attached to state")
    await bridge.connect()

    translator = None
    if getattr(bridge, "profile", None) == "faust":
        from app.fusion.faust_adapter import FaustTranslator
        translator = FaustTranslator()

    registry = state.get("registry")
    if registry is None:
        registry = await ToolRegistry.discover(bridge, translator=translator)
    return {"bridge": bridge, "registry": registry}


def build_graph(config: Config | None = None):
    """Build the compiled graph. Caller supplies the bridge per-run."""
    cfg = config or get_config()
    set_runtime_config(cfg)

    graph = StateGraph(AgentState)

    graph.add_node("runtime", prepare_runtime)
    graph.add_node("requirements", requirements_node)
    graph.add_node("planner", planner_node)
    graph.add_node("review", review_node)
    graph.add_node("executor", executor_node)
    graph.add_node("inspector", inspector_node)
    graph.add_node("repair", repair_node)
    graph.add_node("finish", finish_node)

    graph.set_entry_point("runtime")
    graph.add_edge("runtime", "requirements")

    graph.add_conditional_edges(
        "requirements", route_after_requirements,
        {"planner": "planner", "finish": "finish"},
    )
    # planner/review can loop on rejected plans; guard via review routing.
    graph.add_conditional_edges(
        "planner", route_after_planner,
        {"review": "review", "planner": "planner"},
    )
    graph.add_conditional_edges(
        "review", route_after_review,
        {"executor": "executor", "planner": "planner"},
    )
    graph.add_edge("executor", "inspector")
    graph.add_conditional_edges(
        "inspector", route_after_inspector,
        {"finish": "finish", "repair": "repair"},
    )
    graph.add_conditional_edges(
        "repair", route_after_repair,
        {"executor": "executor", "finish": "finish"},
    )
    graph.add_edge("finish", END)

    return graph.compile()
