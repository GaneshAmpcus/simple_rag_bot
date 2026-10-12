"""
LangChain wrapper around chat models, function-based.

Provider selection (backward compatible -- with NO new env vars set,
every get_llm() call behaves exactly as before: ChatGroq):

    LLM_PROVIDER=groq|groq_bedrock|bedrock|litellm   global default (default: groq)
    LLM_<ROLE>_PROVIDER=...                          per-role override
    LLM_<ROLE>_MODEL=...                             per-role model override

<ROLE> is the `role=` argument passed to get_llm(): AGENT, INTENT, CHAT,
RAG, GUARDRAILS. Callers that pass no role always use LLM_PROVIDER.

Providers:
    groq          -- direct Groq (original behaviour).
    groq_bedrock  -- Groq primary, AWS Bedrock as automatic failover
                     (in-process, no gateway). Failover happens only on
                     transient/availability errors (rate limit, timeout,
                     connection, 5xx, bad key) -- NOT on 400 bad-request, so
                     the malformed-tool-call recovery in nodes.py still runs
                     against Groq exactly as before.
    bedrock       -- direct AWS Bedrock via ChatBedrockConverse.
    litellm       -- OpenAI-compatible LiteLLM proxy (optional, separate
                     service; LITELLM_BASE_URL / LITELLM_API_KEY).

Bedrock needs `pip install "langchain-aws<1.0"`, AWS credentials (env keys
or an IAM role), AWS_REGION, and a model / inference-profile id:
    LLM_<ROLE>_BEDROCK_MODEL, else BEDROCK_MODEL_ID
    (for provider=bedrock, LLM_<ROLE>_MODEL is also honoured).
"""

import os
import groq
from langchain_groq import ChatGroq
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableWithFallbacks

RAG_PROMPT = PromptTemplate.from_template(
    """Answer the question using ONLY the context below.
If the answer isn't in the context, say you don't know.

Context:
{context}

Question: {question}
Answer:"""
)

# Errors that mean "Groq is unavailable / throttled", i.e. worth failing
# over to Bedrock. Deliberately excludes BadRequestError (400) so a
# malformed tool call is still retried/recovered against Groq by nodes.py.
_GROQ_FAILOVER_ERRORS = (
    groq.RateLimitError,
    groq.APIConnectionError,   # includes APITimeoutError
    groq.InternalServerError,
    groq.AuthenticationError,
)


def _resolve_provider(role: str | None) -> str:
    provider = None
    if role:
        provider = os.getenv(f"LLM_{role.upper()}_PROVIDER")
    provider = provider or os.getenv("LLM_PROVIDER") or "groq"
    return provider.strip().lower()


def _groq_llm(model: str, api_key_env: str, temperature: float, max_retries: int = 3):
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise ValueError(f"{api_key_env} not found")
    return ChatGroq(
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_retries=max_retries,
    )


def _bedrock_llm(role: str | None, temperature: float, *, allow_role_model: bool):
    try:
        from langchain_aws import ChatBedrockConverse
    except ImportError as e:
        raise ImportError('Bedrock needs: pip install "langchain-aws<1.0"') from e

    r = role.upper() if role else None
    model_id = None
    if r:
        model_id = os.getenv(f"LLM_{r}_BEDROCK_MODEL")
        if not model_id and allow_role_model:
            model_id = os.getenv(f"LLM_{r}_MODEL")
    model_id = model_id or os.getenv("BEDROCK_MODEL_ID")
    if not model_id:
        raise ValueError(
            "Bedrock model id missing: set BEDROCK_MODEL_ID "
            f"(or LLM_{r or 'DEFAULT'}_BEDROCK_MODEL) to a Bedrock model / inference-profile id"
        )
    return ChatBedrockConverse(
        model=model_id,
        region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
        temperature=temperature,
    )


def get_llm(
    model: str = "openai/gpt-oss-20b",
    api_key_env: str = "GROQ_AGENT_API_KEY",
    temperature: float = 0.0,
    role: str | None = None,
):
    provider = _resolve_provider(role)
    model_override = os.getenv(f"LLM_{role.upper()}_MODEL") if role else None

    if provider == "groq_bedrock":
        # Fewer Groq-side retries than the default so failover happens
        # quickly instead of after several backoff cycles.
        primary = _groq_llm(
            model_override or model,
            api_key_env,
            temperature,
            max_retries=int(os.getenv("LLM_FAILOVER_GROQ_RETRIES", "1")),
        )
        fallback = _bedrock_llm(role, temperature, allow_role_model=False)
        return primary.with_fallbacks(
            [fallback], exceptions_to_handle=_GROQ_FAILOVER_ERRORS
        )

    if provider == "bedrock":
        return _bedrock_llm(role, temperature, allow_role_model=True)

    if provider == "litellm":
        from langchain_openai import ChatOpenAI  # already in requirements.txt

        base_url = os.getenv("LITELLM_BASE_URL")
        api_key = os.getenv("LITELLM_API_KEY")
        if not base_url or not api_key:
            raise ValueError("LITELLM_BASE_URL and LITELLM_API_KEY are required for provider 'litellm'")

        alias = model_override or (f"{role.lower()}-llm" if role else "default-llm")
        return ChatOpenAI(
            model=alias,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            max_retries=2,
            timeout=float(os.getenv("LITELLM_TIMEOUT", "60")),
        )

    if provider != "groq":
        raise ValueError(
            f"Unknown LLM provider {provider!r} (expected groq|groq_bedrock|bedrock|litellm)"
        )

    # ---- original Groq path, unchanged ----
    return _groq_llm(model_override or model, api_key_env, temperature, max_retries=3)


def _bind_tools_one(llm, tools: list):
    # ChatBedrockConverse.bind_tools doesn't accept parallel_tool_calls.
    if type(llm).__name__ == "ChatBedrockConverse":
        return llm.bind_tools(tools)
    return llm.bind_tools(tools, parallel_tool_calls=False)


def bind_agent_tools(llm, tools: list):
    """bind_tools() with parallel_tool_calls=False where supported.

    A fallback-wrapped model (groq_bedrock) has no bind_tools of its own,
    so tools are bound to the primary and to every fallback separately and
    the failover wrapper is rebuilt around them."""
    if isinstance(llm, RunnableWithFallbacks):
        primary = _bind_tools_one(llm.runnable, tools)
        fallbacks = [_bind_tools_one(f, tools) for f in llm.fallbacks]
        return primary.with_fallbacks(
            fallbacks, exceptions_to_handle=llm.exceptions_to_handle
        )
    return _bind_tools_one(llm, tools)


def answered_by(response) -> str:
    """Best-effort name of the model that produced `response`
    (useful to see Groq vs Bedrock after a failover)."""
    md = getattr(response, "response_metadata", None) or {}
    return str(md.get("model_name") or md.get("model_id") or md.get("model") or "unknown")


def generate_answer(llm, question: str, contexts: list[str]) -> str:
    context_block = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    prompt = RAG_PROMPT.format(context=context_block, question=question)
    response = llm.invoke(prompt)
    return response.content
