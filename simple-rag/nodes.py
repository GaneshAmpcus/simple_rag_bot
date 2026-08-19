"""
Graph node functions. Each node is a plain function of
(state) -> partial state update, per LangGraph convention.
"""

import json
import re
import uuid

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
from guardrails_layer import check_input_sync, scan_tool_output_sync, mask_pii
from memory_layer import memory
from config.logging_config import get_logger

log = get_logger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _last_human_text(messages) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content
    return ""


def build_pre_rails_node():
    """Step 1+2 of the guardrails plan: normalize obfuscated input and
    run it through NeMo's input rails (self-check + persona-override
    flow), before anything reaches intent/agent/chat. Runs first in the
    graph -- everything downstream only ever sees traffic that passed
    this gate."""

    def pre_rails_node(state: dict) -> dict:
        user_text = _last_human_text(state["messages"])
        if not user_text:
            return {"blocked": False}

        allowed, refusal = check_input_sync(user_text)
        if not allowed:
            log.info("pre_rails_node: blocked user_text=%r", user_text)
            return {"blocked": True, "messages": [AIMessage(content=refusal)]}

        masked_text = mask_pii(user_text)
        if masked_text != user_text:
            log.info("pre_rails_node: masked PII in user input")
            last_human_msg = next(
                m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)
            )
            replacement = HumanMessage(content=masked_text)
            replacement.id = last_human_msg.id  # same id -> add_messages replaces in place
            return {"blocked": False, "messages": [replacement]}

        return {"blocked": False}

    return pre_rails_node


def build_output_rails_node():
    """Runs once, after the tool-calling loop has fully finished --
    NOT on every agent/tools/tool_scan iteration. Masks PII in the
    final assistant response only. Wired in graph.py so both the
    agent's no-more-tool-calls branch and chat_node's direct answer
    route through here before reaching END; tool_scan_node stays
    per-iteration since it scans retrieved content, a different job."""

    def output_rails_node(state: dict) -> dict:
        messages = state["messages"]
        if not messages:
            return {}

        last = messages[-1]
        if not isinstance(last, AIMessage) or not last.content:
            return {}

        masked = mask_pii(last.content)
        if masked == last.content:
            return {}

        log.info("output_rails_node: masked PII in final response")
        replacement = AIMessage(content=masked)
        replacement.id = last.id
        return {"messages": [replacement]}

    return output_rails_node


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
            result = memory.search(query=user_text, user_id=user_id, limit=5)
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
    intent_llm = get_llm(model="openai/gpt-oss-120b",api_key_env="GROQ_INTENT_API_KEY")  # small/fast model recommended if you have one

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


# Recovers a malformed tool call from Groq's error payload. Llama
# occasionally emits its tool call in a non-standard pythonic form --
# "<function=name{...json...}></function>" -- instead of the structured
# format Groq's parser expects. The call itself is usually well-formed
# intent, just wrapped wrong; Groq's 400 response includes that string
# verbatim in `failed_generation`, so we can recover the intended tool
# name + args ourselves rather than silently falling back to a
# no-tools answer that never actually used the tool the model meant to.
_FAILED_TOOL_CALL_RE = re.compile(r"<function=([A-Za-z_][A-Za-z0-9_]*)(\{.*\})\s*>", re.DOTALL)


def _recover_tool_call_from_error(e: Exception):
    raw = None
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        raw = body.get("error", {}).get("failed_generation")
    if not raw:
        raw = str(e)

    match = _FAILED_TOOL_CALL_RE.search(raw or "")
    if not match:
        return None

    tool_name, args_json = match.group(1), match.group(2)
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError:
        log.warning("_recover_tool_call_from_error: matched but args weren't valid JSON: %r", args_json)
        return None

    return tool_name, args


def build_agent_node(tools: list):
    # NOTE: system prompt is no longer built once here -- it now depends
    # on state["user_memories"], which changes per user/turn, so it has
    # to be rebuilt inside agent_node() on every call. Only the
    # tool-bound LLM clients stay fixed at build time (tools don't
    # change per request).
    llm_with_tools = get_llm(model="openai/gpt-oss-20b",
                    api_key_env="GROQ_AGENT_API_KEY").bind_tools(tools, parallel_tool_calls=False)
    llm_no_tools = get_llm(model="openai/gpt-oss-20b",
                    api_key_env="GROQ_AGENT_API_KEY")  # fallback, same model, no tools bound

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
            # non-JSON "<function=name{...}></function>" pythonic form
            # instead of a structured tool call) as a 400
            # "tool_use_failed". This is a stochastic generation
            # hiccup, not a permanent error -- retry once first.
            log.warning(
                "agent_node: Groq rejected malformed tool call, retrying once. err=%s",
                e,
            )

            try:
                response = llm_with_tools.invoke(messages)

            except GroqBadRequestError as e2:
                # Retry also failed the same way. Before giving up on
                # tools entirely, try to recover the intended call from
                # the error payload itself -- often more reliable than
                # a third stochastic attempt, since the payload usually
                # contains a well-formed, parseable tool name + args.
                recovered = _recover_tool_call_from_error(e2)
                if recovered:
                    tool_name, args = recovered
                    log.warning(
                        "agent_node: recovered malformed tool call from error payload: %s(%s)",
                        tool_name, args,
                    )
                    response = AIMessage(
                        content="",
                        tool_calls=[{
                            "name": tool_name,
                            "args": args,
                            "id": f"recovered-{uuid.uuid4().hex[:8]}",
                        }],
                    )
                else:
                    log.error(
                        "agent_node: retry failed and could not recover tool call, "
                        "falling back to no-tools answer. err=%s",
                        e2,
                    )
                    response = llm_no_tools.invoke(messages)

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
            model="openai/gpt-oss-120b",
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