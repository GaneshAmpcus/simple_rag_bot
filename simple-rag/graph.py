"""
Graph wiring only -- no prompt text, no tool definitions, no LLM
config live here. That all comes from prompts.py / tools_registry.py /
llm.py so this file stays generic and short.
"""

from typing import Annotated, TypedDict, Literal

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from tools_registry import build_default_tools
from nodes import (
    build_pre_rails_node,
    route_after_pre_rails,
    build_memory_recall_node,
    build_intent_node,
    route_by_intent,
    build_agent_node,
    build_tool_scan_node,
    build_chat_node,
    build_output_rails_node,
)
from config.logging_config import get_logger

log = get_logger(__name__)


class GraphState(TypedDict):
    messages: Annotated[list, add_messages]
    intent: Literal["tool_needed", "chat"]
    blocked: bool
    user_id: str
    user_memories: str


# def build_graph(state: dict):
#     """state: the existing RAG pipeline state dict (embedder/llm/store)
#     from rag.build_pipeline_state(), shared with /ingest and /query so
#     there's a single vector store, not a duplicate one."""

#     tools = build_default_tools(state)
#     log.info("build_graph: registered tools=%s", [t.name for t in tools])

#     pre_rails_node = build_pre_rails_node()
#     memory_recall_node = build_memory_recall_node()
#     intent_node = build_intent_node()
#     agent_node = build_agent_node(tools)
#     tool_scan_node = build_tool_scan_node()
#     chat_node = build_chat_node()
#     output_rails_node = build_output_rails_node()
#     tool_node = ToolNode(tools)

#     builder = StateGraph(GraphState)
#     builder.add_node("pre_rails", pre_rails_node)
#     builder.add_node("memory_recall", memory_recall_node)
#     builder.add_node("intent_classify", intent_node)
#     builder.add_node("agent", agent_node)
#     builder.add_node("chat", chat_node)
#     builder.add_node("tools", tool_node)
#     builder.add_node("tool_scan", tool_scan_node)
#     builder.add_node("output_rails", output_rails_node)

#     builder.add_edge(START, "pre_rails")
#     builder.add_conditional_edges(
#         "pre_rails", route_after_pre_rails, {"blocked": END, "ok": "memory_recall"}
#     )
#     builder.add_edge("memory_recall", "intent_classify")
#     builder.add_conditional_edges(
#         "intent_classify", route_by_intent, {"tool_needed": "agent", "chat": "chat"}
#     )

#     # tools_condition normally routes straight to END when the agent's
#     # response has no tool_calls. Overriding that branch to route to
#     # output_rails instead -- this only fires once, when the loop is
#     # truly finished, not on every tools->agent iteration (that's
#     # tool_scan_node's job, on the "tools" branch below, unchanged).
#     builder.add_conditional_edges(
#         "agent", tools_condition, {"tools": "tools", END: "output_rails"}
#     )
#     builder.add_edge("tools", "tool_scan")
#     builder.add_edge("tool_scan", "agent")

#     # chat_node's direct answer also needs the same final-output pass.
#     builder.add_edge("chat", "output_rails")
#     builder.add_edge("output_rails", END)

#     return builder.compile()


# ============================================================
# Configuration
# ============================================================

SKIP_PRE_RAILS = True
# True  -> skip pre_rails
# False -> run pre_rails


def build_graph(state: dict):
    """Build and compile the LangGraph workflow.

    `state` is the existing RAG pipeline state containing the
    embedder/llm/store shared with /ingest and /query.
    """

    tools = build_default_tools(state)
    log.info("build_graph: registered tools=%s", [t.name for t in tools])

    # --------------------------------------------------------
    # Nodes
    # --------------------------------------------------------

    pre_rails_node = build_pre_rails_node()
    memory_recall_node = build_memory_recall_node()
    intent_node = build_intent_node()
    agent_node = build_agent_node(tools)
    tool_scan_node = build_tool_scan_node()
    chat_node = build_chat_node()
    output_rails_node = build_output_rails_node()

    tool_node = ToolNode(tools)

    # --------------------------------------------------------
    # Graph
    # --------------------------------------------------------

    builder = StateGraph(GraphState)

    builder.add_node("pre_rails", pre_rails_node)
    builder.add_node("memory_recall", memory_recall_node)
    builder.add_node("intent_classify", intent_node)
    builder.add_node("agent", agent_node)
    builder.add_node("chat", chat_node)
    builder.add_node("tools", tool_node)
    builder.add_node("tool_scan", tool_scan_node)
    builder.add_node("output_rails", output_rails_node)

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if SKIP_PRE_RAILS:
        # Skip pre-rails completely
        builder.add_edge(START, "memory_recall")

        log.warning(
            "build_graph: PRE_RAILS IS DISABLED (SKIP_PRE_RAILS=True)"
        )

    else:
        # Normal flow through pre-rails
        builder.add_edge(START, "pre_rails")

        builder.add_conditional_edges(
            "pre_rails",
            route_after_pre_rails,
            {
                "blocked": END,
                "ok": "memory_recall",
            },
        )

    # --------------------------------------------------------
    # Memory -> Intent
    # --------------------------------------------------------

    builder.add_edge("memory_recall", "intent_classify")

    builder.add_conditional_edges(
        "intent_classify",
        route_by_intent,
        {
            "tool_needed": "agent",
            "chat": "chat",
        },
    )

    # --------------------------------------------------------
    # Agent -> Tools / Output Rails
    # --------------------------------------------------------

    # tools_condition normally routes:
    #
    #   agent -> tools
    #
    # when tool calls exist, otherwise:
    #
    #   agent -> END
    #
    # We override END so the final response goes through
    # output_rails.

    builder.add_conditional_edges(
        "agent",
        tools_condition,
        {
            "tools": "tools",
            END: "output_rails",
        },
    )

    # --------------------------------------------------------
    # Tool loop
    # --------------------------------------------------------

    builder.add_edge("tools", "tool_scan")
    builder.add_edge("tool_scan", "agent")

    # --------------------------------------------------------
    # Direct chat -> Output Rails
    # --------------------------------------------------------

    builder.add_edge("chat", "output_rails")

    # --------------------------------------------------------
    # Final output
    # --------------------------------------------------------

    builder.add_edge("output_rails", END)

    # --------------------------------------------------------
    # Compile
    # --------------------------------------------------------

    return builder.compile()