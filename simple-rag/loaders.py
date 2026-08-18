"""
Load documents from disk using LangChain's document loaders, dispatched
by file extension. Returns langchain_core.documents.Document objects,
each carrying page_content plus metadata (source path, page number for
PDFs, etc.) -- handy later if you want to cite which file/page an
answer came from.

Add more entries to LOADERS as you need more file types.
"""

from pathlib import Path

from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader, TextLoader
from langchain_core.documents import Document

LOADERS = {
    ".pdf": PyPDFLoader,
    ".docx": Docx2txtLoader,
    ".txt": TextLoader,
    ".md": TextLoader,
}


def load_document(file_path: str) -> list[Document]:
    """Load a single file into one or more Document objects."""
    ext = Path(file_path).suffix.lower()
    loader_cls = LOADERS.get(ext)
    if loader_cls is None:
        raise ValueError(f"Unsupported file type: {ext}. Supported: {list(LOADERS)}")
    return loader_cls(file_path).load()


def load_documents(file_paths: list[str]) -> list[Document]:
    """Load multiple files into a flat list of Document objects."""
    docs = []
    for path in file_paths:
        docs.extend(load_document(path))
    return docs