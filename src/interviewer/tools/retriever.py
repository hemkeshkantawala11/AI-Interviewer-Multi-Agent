"""RAG retriever over the curated Maths + LR question bank.

This is the system's knowledge-grounding component: questions are GROUNDED in a
vetted bank rather than freely hallucinated by the LLM — preventing incorrect
maths and repeated questions. Selection is randomised within (topic, difficulty)
so students get a fresh question each session.
"""
from __future__ import annotations

import json
import random
from functools import lru_cache

from ..config import settings


@lru_cache(maxsize=1)
def _load_bank() -> tuple[dict, ...]:
    with open(settings.question_bank_path, "r", encoding="utf-8") as f:
        return tuple(json.load(f))


def all_topics() -> list[str]:
    seen: list[str] = []
    for item in _load_bank():
        if item["topic"] not in seen:
            seen.append(item["topic"])
    return seen


def topics_by_subject() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in _load_bank():
        out.setdefault(item.get("subject", "Other"), [])
        if item["topic"] not in out[item["subject"]]:
            out[item["subject"]].append(item["topic"])
    return out


def retrieve(topic: str, difficulty: str, exclude_ids: set[str] | None = None) -> dict | None:
    """Randomly pick an unused question matching topic + difficulty.

    Falls back to the same topic (any difficulty), then anything unused, so a
    session never runs dry.
    """
    exclude_ids = exclude_ids or set()
    bank = [q for q in _load_bank() if q["id"] not in exclude_ids]
    if not bank:
        return None

    exact = [q for q in bank if q["topic"] == topic and q["difficulty"] == difficulty]
    same_topic = [q for q in bank if q["topic"] == topic]
    pool = exact or same_topic or bank
    return random.choice(pool)
