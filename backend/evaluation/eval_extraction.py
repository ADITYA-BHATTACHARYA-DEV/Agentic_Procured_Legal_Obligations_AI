"""Evaluate obligation extraction F1 on synthetic contracts.
Usage:  python -m evaluation.eval_extraction
Gold file format (evaluation/gold.json):
  [{"text": "...contract text...", "obligations": ["Pay invoices within 30 days", ...]}]
Matching is fuzzy (difflib ratio >= 0.6) so wording differences don't count as misses."""
import asyncio
import json
from difflib import SequenceMatcher
from pathlib import Path


def _match(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.6


def f1(predicted: list[str], gold: list[str]) -> float:
    if not predicted or not gold:
        return 0.0
    tp_p = sum(any(_match(p, g) for g in gold) for p in predicted)
    tp_g = sum(any(_match(g, p) for p in predicted) for g in gold)
    precision, recall = tp_p / len(predicted), tp_g / len(gold)
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


async def main():
    from app.services.obligation_extractor import extract_obligations
    data = json.loads((Path(__file__).parent / "gold.json").read_text())
    scores = []
    for item in data:
        pages = [{"page": 1, "text": item["text"]}]
        preds = [o["description"] for o in await extract_obligations(pages, [])]
        scores.append(f1(preds, item["obligations"]))
    print(f"Mean extraction F1: {sum(scores) / len(scores):.3f} over {len(scores)} docs")


if __name__ == "__main__":
    asyncio.run(main())
