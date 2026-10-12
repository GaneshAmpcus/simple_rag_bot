"""Convert evals/eval_testset.csv (Ragas testset) into RagDiff JSONL datasets.

Run from the simple-rag folder:  python ragdiff_eval/make_dataset.py
Writes ragdiff_eval/dataset.jsonl (all cases) and dataset_smoke.jsonl (first 3).
"""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE_SIZE = 3


def dump(path: Path, cases: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for case in cases:
            stream.write(json.dumps(case, ensure_ascii=False) + "\n")


def main() -> None:
    with (ROOT / "evals" / "eval_testset.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    cases = [
        {
            "question": row["user_input"],
            "reference": row["reference"],
            "source": "synthetic",
            "status": "approved",
            "metadata": {},
        }
        for row in rows
        if row["user_input"] and row["reference"]
    ]
    dump(ROOT / "ragdiff_eval" / "dataset.jsonl", cases)
    dump(ROOT / "ragdiff_eval" / "dataset_smoke.jsonl", cases[:SMOKE_SIZE])
    print(f"{len(cases)} cases -> dataset.jsonl; smoke set: {SMOKE_SIZE}")


if __name__ == "__main__":
    main()
