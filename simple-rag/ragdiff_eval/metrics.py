"""RagDiff metrics that judge the same way evals/evaluate.py does.

Ragas 0.3.x with the app's own Groq LLM as judge and local FastEmbed
embeddings, instead of RagDiff's built-in Ragas adapter (which targets 0.1.x
and an OpenAI judge). Plug in with ``metrics_factory`` in the eval config.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

SUPPORTED = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


@lru_cache(maxsize=1)
def _judge() -> tuple[Any, Any]:
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    from embeddings import get_embedder
    from llm import get_llm

    return LangchainLLMWrapper(get_llm()), LangchainEmbeddingsWrapper(get_embedder())


class RagasGroqMetric:
    def __init__(self, name: str) -> None:
        self.name = name

    def score(self, case: Any, output: Any) -> float:
        from datasets import Dataset
        from ragas import evaluate
        from ragas import metrics as ragas_metrics

        llm, embeddings = _judge()
        dataset = Dataset.from_list(
            [
                {
                    "question": case.question,
                    "answer": output.answer,
                    "contexts": output.contexts,
                    "ground_truth": case.reference or "",
                }
            ]
        )
        result = evaluate(
            dataset,
            metrics=[getattr(ragas_metrics, self.name)],
            llm=llm,
            embeddings=embeddings,
            raise_exceptions=False,
            show_progress=False,
        )
        value = result.to_pandas()[self.name].iloc[0]
        if value is None or math.isnan(float(value)):
            raise RuntimeError(
                f"Ragas returned no score for {self.name!r} on {case.question!r} "
                "(judge call failed or was rate limited)"
            )
        return float(value)


def build_metrics(names: list[str]) -> list[RagasGroqMetric]:
    return [RagasGroqMetric(name) for name in names if name in SUPPORTED]
