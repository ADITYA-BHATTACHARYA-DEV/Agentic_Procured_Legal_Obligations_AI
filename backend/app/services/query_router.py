"""Classify a question as legal | obligation | summary | general.
Fast keyword scoring first; the LLM classifier only runs when nothing matches clearly."""
import re

from ..prompts.system_prompts import QUERY_CLASSIFIER_PROMPT
from . import llm

KEYWORDS = {
    "legal": r"\b(legal|legally|liab\w*|indemn\w*|enforce\w*|valid|breach|terminat\w*|clause|governing law|"
             r"jurisdiction|warrant\w*|risk\w*|penalt\w*|dispute|arbitrat\w*|compliant|compliance|"
             r"can (they|we|i)|is it (safe|ok|fair)|rights?)\b",
    "obligation": r"\b(obligat\w*|deadline\w*|due|deliver\w*|task\w*|who (must|should|has to)|responsib\w*|"
                  r"renew\w*|notice period|payment schedule|when (do|must|is)|what do (i|we) (need|have) to|"
                  r"to-?do|action items?|overdue|upcoming)\b",
    "summary": r"\b(summar\w*|overview|tl;?dr|key (points|terms)|main points|in short|brief me|gist|explain this (document|contract))\b",
}


def heuristic(question: str) -> str | None:
    q = question.lower()
    scores = {k: len(re.findall(p, q)) for k, p in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    ranked = sorted(scores.values(), reverse=True)
    if ranked[0] == 0 or ranked[0] == ranked[1]:
        return None
    return best


async def classify(question: str) -> str:
    h = heuristic(question)
    if h:
        return h
    try:
        out = await llm.chat_json("You are a precise classifier.", QUERY_CLASSIFIER_PROMPT.format(question=question))
        t = out.get("type", "general")
        return t if t in KEYWORDS or t == "general" else "general"
    except Exception:
        return "general"
