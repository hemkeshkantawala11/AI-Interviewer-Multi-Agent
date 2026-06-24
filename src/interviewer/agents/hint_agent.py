"""Hint Agent — one conceptual nudge, never the answer or method."""
from __future__ import annotations

from ..llm import run_agent
from ..schemas import HintResult


def make_hint(current_question: str, concept_tested: str, correct_answer: str) -> HintResult:
    return run_agent(
        "hint_agent",
        {
            "current_question": current_question,
            "concept_tested": concept_tested,
            "correct_answer": correct_answer,
        },
        HintResult,
    )
