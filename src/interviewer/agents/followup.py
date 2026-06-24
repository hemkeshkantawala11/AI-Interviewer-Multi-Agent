"""Follow-up Decision Agent — decides whether to probe understanding further."""
from __future__ import annotations

from ..llm import run_agent
from ..schemas import Evaluation, FollowupAssessment, FollowupDecision
from .evaluator import _scrub


def assess(
    current_question: str, followup_question: str, student_answer: str, correct_answer: str = ""
) -> FollowupAssessment:
    """Judge the student's follow-up explanation and return a spoken verdict."""
    result = run_agent(
        "followup_assessor",
        {
            "current_question": current_question,
            "followup_question": followup_question,
            "correct_answer": correct_answer,
            "student_answer": student_answer,
        },
        FollowupAssessment,
    )
    result.feedback = _scrub(result.feedback, correct_answer)
    return result


def decide(current_question: str, student_answer: str, evaluation: Evaluation) -> FollowupDecision:
    return run_agent(
        "followup",
        {
            "current_question": current_question,
            "student_answer": student_answer,
            "correctness": evaluation.correctness,
            "concept_clarity": evaluation.concept_clarity,
            "internal_notes": evaluation.internal_notes,
        },
        FollowupDecision,
    )
