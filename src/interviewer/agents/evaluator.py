"""Answer Evaluator Agent — scores a student answer on 4 dimensions.

Guardrail: the student-facing feedback is scrubbed so it can never contain the
correct answer string, even if the model slips.
"""
from __future__ import annotations

import re

from ..llm import run_agent
from ..schemas import Evaluation


def _scrub(feedback: str, correct_answer: str) -> str:
    if not correct_answer:
        return feedback
    pattern = re.escape(correct_answer.strip())
    if pattern and re.search(pattern, feedback, flags=re.IGNORECASE):
        return ("Let's not focus on a final figure yet — revisit the core concept and "
                "check each step of your reasoning.")
    return feedback


def evaluate(
    current_question: str,
    subject: str,
    correct_answer: str,
    key_concepts: list[str],
    student_answer: str,
) -> Evaluation:
    result = run_agent(
        "evaluator",
        {
            "current_question": current_question,
            "subject": subject,
            "correct_answer": correct_answer,
            "key_concepts": ", ".join(key_concepts),  # string for the prompt template
            "key_concepts_list": key_concepts,  # list for the deterministic mock
            "student_answer": student_answer,
        },
        Evaluation,
    )
    result.student_feedback = _scrub(result.student_feedback, correct_answer)
    return result
