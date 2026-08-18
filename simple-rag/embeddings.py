"""
LangChain HuggingFace embeddings, function-based.

We no longer need to track `dim` ourselves -- FAISS.from_texts()
figures that out from the embedder internally.
"""

# from langchain_huggingface import HuggingFaceEmbeddings
# import os

# EMBEDDER = HuggingFaceEmbeddings(
#     model_name="sentence-transformers/all-MiniLM-L6-v2"
# )

# def get_embedder():
#     return EMBEDDER


from langchain_community.embeddings import FastEmbedEmbeddings

EMBEDDER = FastEmbedEmbeddings(
    model_name="BAAI/bge-small-en-v1.5"
)

def get_embedder():
    return EMBEDDER