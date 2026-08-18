"""
LangChain wrapper around Groq's chat models, function-based.
"""

import os
from langchain_groq import ChatGroq
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

from langchain_core.prompts import PromptTemplate

RAG_PROMPT = PromptTemplate.from_template(
    """Answer the question using ONLY the context below.
If the answer isn't in the context, say you don't know.

Context:
{context}

Question: {question}
Answer:"""
)





def get_llm(
    model: str = "llama-3.3-70b-versatile",
    api_key_env: str = "GROQ_AGENT_API_KEY",
    temperature: float = 0.0,
):
    api_key = os.getenv(api_key_env)

    if not api_key:
        raise ValueError(f"{api_key_env} not found")

    return ChatGroq(
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_retries=3,
    )

def generate_answer(llm: ChatGroq, question: str, contexts: list[str]) -> str:
    context_block = "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(contexts))
    prompt = RAG_PROMPT.format(context=context_block, question=question)
    response = llm.invoke(prompt)
    return response.content