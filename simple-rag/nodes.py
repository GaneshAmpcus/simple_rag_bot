"""
Graph node functions. Each node is a plain function of
(state) -> partial state update, per LangGraph convention.
"""

import json
import re

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.exceptions import OutputParserException
from groq import BadRequestError as GroqBadRequestError
from llm import get_llm
from prompts import (
    INTENT_SYSTEM_PROMPT,
    INTENT_USER_TEMPLATE,
    build_chat_system_prompt,
    build_agent_system_prompt,
)
from guardrails_layer import check_input_sync, scan_tool_output_sync
from memory_layer import memory
from config.logging_config import get_logger

log = get_logger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _last_human_text(messages) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content
    return ""


# def build_pre_rails_node():
#     """Step 1+2 of the guardrails plan: normalize obfuscated input and
#     run it through NeMo's input rails (self-check + persona-override
#     flow), before anything reaches intent/agent/chat. Runs first in the
#     graph -- everything downstream only ever sees traffic that passed
#     this gate."""

#     def pre_rails_node(state: dict) -> dict:
#         user_text = _last_human_text(state["messages"])
#         if not user_text:
#             return {"blocked": False}

#         allowed, refusal = check_input_sync(user_text)
#         if not allowed:
#             log.info("pre_rails_node: blocked user_text=%r", user_text)
#             return {"blocked": True, "messages": [AIMessage(content=refusal)]}

#         return {"blocked": False}

#     return pre_rails_node

def build_pre_rails_node():
    """Normalize, mask PII, and run NeMo input rails before anything
    reaches memory/intent/agent/chat."""

    def pre_rails_node(state: dict) -> dict:
        messages = state["messages"]
        user_text = _last_human_text(messages)

        if not user_text:
            return {"blocked": False}

        allowed, refusal, sanitized_text = check_input_sync(user_text)

        if not allowed:
            log.info("pre_rails_node: blocked user_text=%r", user_text)
            return {
                "blocked": True,
                "messages": [AIMessage(content=refusal)],
            }

        # Find the latest HumanMessage and replace it with
        # the sanitized version using the same message ID.
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                if message.content != sanitized_text:
                    sanitized_message = HumanMessage(
                        content=sanitized_text
                    )

                    # Preserve the original message ID so
                    # add_messages replaces rather than duplicates it.
                    sanitized_message.id = message.id

                    log.info(
                        "pre_rails_node: PII was masked before downstream nodes"
                    )

                    return {
                        "blocked": False,
                        "messages": [sanitized_message],
                    }

                break

        return {"blocked": False}

    return pre_rails_node

def route_after_pre_rails(state: dict) -> str:
    return "blocked" if state.get("blocked") else "ok"


def build_memory_recall_node():
    """Reads relevant long-term facts about this user from mem0 before
    intent/agent/chat run. Placed after pre_rails -- a blocked message
    never triggers a memory lookup. Needs state["user_id"], set by
    main.py at invoke() time."""

    def memory_recall_node(state: dict) -> dict:
        user_text = _last_human_text(state["messages"])
        user_id = state.get("user_id")
        if not user_text or not user_id:
            return {"user_memories": ""}

        try:
            result = memory.search(query=user_text, filters={"user_id": user_id}, limit=5)
            entries = result.get("results", []) if isinstance(result, dict) else result
            memories_str = "\n".join(f"- {e['memory']}" for e in entries)
        except Exception:
            log.exception("memory_recall_node: mem0 search failed, continuing without memory")
            memories_str = ""

        log.info("memory_recall_node: user_id=%s recalled_chars=%d", user_id, len(memories_str))
        return {"user_memories": memories_str}

    return memory_recall_node


def build_intent_node():
    """Cheap classification step: does this message need tools, or is
    it plain conversation? Falls back to 'tool_needed' on any parse
    failure -- see prompts.py for rationale."""
    intent_llm = intent_llm = get_llm(
            model="llama-3.1-8b-instant",
            api_key_env="GROQ_INTENT_API_KEY",
        ) # small/fast model recommended if you have one

    def intent_node(state: dict) -> dict:
        user_text = _last_human_text(state["messages"])
        response = intent_llm.invoke([
            SystemMessage(content=INTENT_SYSTEM_PROMPT),
            HumanMessage(content=INTENT_USER_TEMPLATE.format(message=user_text)),
        ])

        intent = "tool_needed"  # safe default
        match = _JSON_RE.search(response.content or "")
        if match:
            try:
                parsed = json.loads(match.group(0))
                if parsed.get("intent") in ("tool_needed", "chat"):
                    intent = parsed["intent"]
            except (json.JSONDecodeError, AttributeError):
                log.warning("intent_node: failed to parse JSON, defaulting to tool_needed. raw=%r", response.content)
        else:
            log.warning("intent_node: no JSON found, defaulting to tool_needed. raw=%r", response.content)

        log.info("intent_node: user_text=%r -> intent=%s", user_text, intent)
        return {"intent": intent}

    return intent_node


def route_by_intent(state: dict) -> str:
    """Conditional-edge function reading the intent set by intent_node."""
    return state.get("intent", "tool_needed")



def build_agent_node(tools: list):
    # NOTE: system prompt is no longer built once here -- it now depends
    # on state["user_memories"], which changes per user/turn, so it has
    # to be rebuilt inside agent_node() on every call. Only the
    # tool-bound LLM clients stay fixed at build time (tools don't
    # change per request).
    llm_with_tools = llm_with_tools = get_llm(
                    model="llama-3.3-70b-versatile",
                    api_key_env="GROQ_AGENT_API_KEY",
                ).bind_tools(tools, parallel_tool_calls=False)
    
    llm_no_tools = llm_no_tools = get_llm(
                model="llama-3.3-70b-versatile",
                api_key_env="GROQ_AGENT_API_KEY",
            ) # fallback, same model, no tools bound

    def agent_node(state: dict) -> dict:
        messages = state["messages"]
        system_prompt = SystemMessage(
            content=build_agent_system_prompt(tools, memories=state.get("user_memories", ""))
        )

        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [system_prompt] + messages

        try:
            response = llm_with_tools.invoke(messages)

        except OutputParserException as e:
            log.warning(
                "agent_node: malformed tool-call generation, retrying once. err=%s",
                e,
            )

            try:
                response = llm_with_tools.invoke(messages)

            except OutputParserException as e2:
                log.error(
                    "agent_node: retry also failed, falling back to no-tools answer. err=%s",
                    e2,
                )
                response = llm_no_tools.invoke(messages)

        except GroqBadRequestError as e:
            # Groq's own tool-call parser occasionally rejects a
            # malformed function-call the model generated (e.g. the
            # non-JSON "<function=name{...}</function>" pythonic form
            # instead of a structured tool call) as a 400
            # "tool_use_failed". This is a stochastic generation
            # hiccup, not a permanent error -- retry once, same
            # pattern as the OutputParserException branch above,
            # before giving up on tools for this turn.
            log.warning(
                "agent_node: Groq rejected malformed tool call, retrying once. err=%s",
                e,
            )

            try:
                response = llm_with_tools.invoke(messages)

            except Exception as e2:
                log.error(
                    "agent_node: retry also failed, falling back to no-tools answer. err=%s",
                    e2,
                )
                response = llm_no_tools.invoke(messages)

        except Exception as e:
            log.exception(
                "agent_node: tool-enabled invocation failed, falling back to no-tools answer"
            )
            response = llm_no_tools.invoke(messages)

        log.info(
            "agent_node: tool_calls=%s",
            [tc["name"] for tc in getattr(response, "tool_calls", [])] or "none",
        )

        return {"messages": [response]}

    return agent_node


def build_tool_scan_node():
    """Step 4 of the guardrails plan: sits on the tools -> agent edge.
    Scans whatever the tool node just produced (search snippets,
    retrieved docs) for embedded instructions before the agent node
    reads it back as message history. Tool outputs are already wrapped
    as <untrusted_data> in tools_registry.py -- this is a second,
    content-aware layer on top of that static wrapper."""

    def tool_scan_node(state: dict) -> dict:
        messages = state["messages"]

        trailing_tool_msgs = []
        for m in reversed(messages):
            if isinstance(m, ToolMessage):
                trailing_tool_msgs.append(m)
            else:
                break
        trailing_tool_msgs.reverse()

        if not trailing_tool_msgs:
            return {}

        updated = []
        for m in trailing_tool_msgs:
            safe_content = scan_tool_output_sync(m.content, source_label=m.name or "tool")
            if safe_content != m.content:
                new_msg = ToolMessage(
                    content=safe_content, tool_call_id=m.tool_call_id, name=m.name
                )
                new_msg.id = m.id  # same id -> add_messages replaces in place, no dupe
                updated.append(new_msg)

        if not updated:
            return {}

        log.info("tool_scan_node: replaced %d flagged tool result(s)", len(updated))
        return {"messages": updated}

    return tool_scan_node


def build_chat_node():
    """Plain conversational fallback -- no tools bound. Used when
    intent_node decides no lookup is needed."""
    # System prompt is rebuilt inside chat_node() per call, same reason
    # as agent_node -- it now depends on state["user_memories"].
    llm = get_llm(
            model="llama-3.3-70b-versatile",
            api_key_env="GROQ_AGENT_API_KEY",
        )

    def chat_node(state: dict) -> dict:
        messages = state["messages"]
        chat_system_prompt = SystemMessage(
            content=build_chat_system_prompt(memories=state.get("user_memories", ""))
        )
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [chat_system_prompt] + messages
        response = llm.invoke(messages)
        log.info("chat_node: answered directly (no tools)")
        return {"messages": [response]}

    return chat_node