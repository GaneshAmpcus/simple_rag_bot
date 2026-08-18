"""
Evaluate the RAG pipeline with RAGAS.

Flow:
  1. Load an eval testset (question + ground_truth pairs) -- either the
     CSV produced by testset_generation.py, or one you wrote by hand.
  2. Run every question through the actual pipeline (rag.query) to get
     the real answer + retrieved contexts -- RAGAS needs to see what
     your pipeline *actually* produces, not the synthetic reference.
  3. Hand all of it to ragas.evaluate() using our own Groq LLM + local
     HF embeddings as the judge (instead of RAGAS's OpenAI default).

Run with:
    python evaluate.py data/eval_testset.csv
"""

import sys

import pandas as pd
from datasets import Dataset
from ragas import evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

from embeddings import get_embedder
from llm import get_llm
from rag import build_pipeline_state, query
import logging

logging.basicConfig(level=logging.DEBUG)

METRICS = [faithfulness, answer_relevancy, context_precision, context_recall]


def load_testset(csv_path: str) -> pd.DataFrame:
    """Load a testset CSV and normalize column names to what the rest of
    this script expects, regardless of whether it came from
    testset_generation.py (user_input/reference) or was hand-written
    (question/ground_truth)."""
    df = pd.read_csv(csv_path)
    df = df.rename(columns={"user_input": "question", "reference": "ground_truth"})
    required = {"question", "ground_truth"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Testset is missing required columns: {missing}")
    return df[["question", "ground_truth"]]


def run_pipeline_over_testset(state: dict, df: pd.DataFrame, top_k: int = 3) -> pd.DataFrame:
    """Run every question through the real pipeline to collect the
    answer + contexts it actually produces."""
    answers, contexts = [], []
    for question in df["question"]:
        result = query(state, question, top_k=top_k)
        answers.append(result["answer"])
        contexts.append(result["contexts"])

    df = df.copy()
    df["answer"] = answers
    df["contexts"] = contexts
    return df


def run_evaluation(csv_path: str, top_k: int = 3):
    testset = load_testset(csv_path)

    state = build_pipeline_state()
    full = run_pipeline_over_testset(state, testset, top_k=top_k)

    dataset = Dataset.from_pandas(full)

    # Judge using our own Groq LLM + local HF embeddings, same as the
    # rest of the pipeline -- RAGAS defaults to OpenAI otherwise, which
    # would need a key we don't have configured.
    judge_llm = LangchainLLMWrapper(get_llm())
    judge_embeddings = LangchainEmbeddingsWrapper(get_embedder())

    results = evaluate(
        dataset,
        metrics=METRICS,
        llm=judge_llm,
        embeddings=judge_embeddings,
    )
    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python evaluate.py <path-to-testset.csv>")
        sys.exit(1)

    results = run_evaluation(sys.argv[1])
    results_df = results.to_pandas()

    print(results_df)
    results_df.to_csv("eval_results.csv", index=False)
    print("\nSaved per-question scores to eval_results.csv")
    print("\nAggregate scores:") 
    print(results)