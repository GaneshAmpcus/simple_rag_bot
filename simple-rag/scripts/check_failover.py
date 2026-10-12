"""
Failover smoke test: Groq (deliberately broken key) -> Bedrock.

Run from the simple-rag folder:
    python scripts\\check_failover.py

Needs in .env: AWS_REGION, AWS credentials, BEDROCK_MODEL_ID.
Nothing here touches your real GROQ keys -- the bad key is set in-process only.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

os.environ["LLM_PROVIDER"] = "groq_bedrock"
os.environ["GROQ_AGENT_API_KEY"] = "invalid-key-for-failover-test"  # force Groq to fail

from langchain_core.tools import tool
from llm import get_llm, bind_agent_tools, answered_by


@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return "sunny, 30C"


print("1) plain chat, Groq broken -> expect Bedrock to answer")
llm = get_llm(role="chat")
r = llm.invoke("Say hi in one word.")
print("   reply:", r.content, "| model:", answered_by(r))

print("2) tool call, Groq broken -> expect a get_weather tool call from Bedrock")
agent = bind_agent_tools(get_llm(role="agent"), [get_weather])
r = agent.invoke("What's the weather in Mumbai? Use the tool.")
print("   tool_calls:", r.tool_calls, "| model:", answered_by(r))

ok = bool(r.tool_calls)
print("\nRESULT:", "PASS" if ok else "CHECK: no tool call returned")
