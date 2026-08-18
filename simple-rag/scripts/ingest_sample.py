"""
Convenience script: reads every .txt file in data/sample_docs and
posts them to the running FastAPI server's /ingest endpoint.

Run the server first (uvicorn main:app --reload), then:
    python scripts/ingest_sample.py
"""

from pathlib import Path

import requests

DOCS_DIR = Path(__file__).parent.parent / "data" / "sample_docs"
API_URL = "http://127.0.0.1:8000/ingest"


def main():
    texts = [p.read_text() for p in DOCS_DIR.glob("*.txt")]
    if not texts:
        print(f"No .txt files found in {DOCS_DIR}")
        return

    response = requests.post(API_URL, json={"texts": texts})
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
