"""Intent Classifier Agent — runs on every student message before routing.

Classifies into one of 5 intents. The strict prompt biases ambiguous
hint-vs-answer-seeking cases toward answer_seeking, which is the safer guardrail
(better to refuse than to leak the method).
"""
from __future__ import annotations

from ..llm import run_agent
from ..schemas import IntentResult


def classify(student_message: str, current_question: str) -> IntentResult:
    result = run_agent(
        "intent_classifier",
        {"student_message": student_message, "current_question": current_question},
        IntentResult,
    )
    return result
