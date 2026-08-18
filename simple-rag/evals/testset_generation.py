"""
Generate a synthetic evaluation dataset from your ingested documents,
using RAGAS's TestsetGenerator instead of hand-writing every
question/ground_truth pair.

How it works: RAGAS chunks your documents into a small knowledge graph,
then has an LLM synthesize questions against that graph -- some answerable
from a single chunk, some requiring it to combine info across chunks -- and
writes a reference answer for each. That reference answer is your
`ground_truth`, used later by context_recall / answer_correctness.

Run this once and save the CSV. Each run costs real LLM calls (one
generator call per question, roughly), so don't regenerate on every
evaluate.py run -- reuse the saved file.
"""

from embeddings import get_embedder
from llm import get_llm
from loaders import load_documents
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.testset import TestsetGenerator


def build_testset_generator() -> TestsetGenerator:
    """Wrap our existing Groq LLM + local HF embeddings so RAGAS generates
    questions using the same models the rest of the pipeline uses, instead
    of silently defaulting to OpenAI (which needs an OPENAI_API_KEY you
    don't have configured)."""
    generator_llm = LangchainLLMWrapper(get_llm())
    generator_embeddings = LangchainEmbeddingsWrapper(get_embedder())
    return TestsetGenerator(llm=generator_llm, embedding_model=generator_embeddings)


def generate_testset(file_paths: list[str], testset_size: int = 5):
    """Load documents, generate a synthetic Q&A testset, return it as a
    pandas DataFrame. Columns (ragas >= 0.2): user_input, reference,
    reference_contexts, synthesizer_name."""
    documents = load_documents(file_paths)
    generator = build_testset_generator()
    dataset = generator.generate_with_langchain_docs(documents, testset_size=testset_size)
    return dataset.to_pandas()


if __name__ == "__main__":
    df = generate_testset(["data/sample_docs/AWS_Prsentation_Script.docx"], testset_size=10)
    df.to_csv("eval_testset.csv", index=False)
    print(df.head())