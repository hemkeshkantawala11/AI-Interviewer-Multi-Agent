"""Topic Selector Agent — picks the session topics (always includes Probability
and at least one LR topic)."""
from __future__ import annotations

from ..llm import run_agent
from ..schemas import TopicSelection
from ..tools import retriever


def select_topics(n: int = 4) -> TopicSelection:
    available = retriever.all_topics()
    result = run_agent(
        "topic_selector",
        {"n": n, "available": "\n".join(f"- {t}" for t in available), "available_list": available},
        TopicSelection,
    )
    # Guardrail: enforce invariants regardless of what the model returned.
    topics = [t for t in result.topics if t in available]
    if "Probability" not in topics:
        topics = ["Probability"] + topics
    if not any("Logical Reasoning" in t for t in topics):
        lr = next((t for t in available if "Logical Reasoning" in t), None)
        if lr:
            topics.append(lr)
    # de-dup, keep order, clamp to n
    seen, ordered = set(), []
    for t in topics:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    result.topics = ordered[:n] if len(ordered) >= n else ordered
    return result
