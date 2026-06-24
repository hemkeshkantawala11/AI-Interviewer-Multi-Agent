"""Report Compiler Agent — synthesises the full session into a structured report."""
from __future__ import annotations

import json

from ..llm import run_agent
from ..schemas import InterviewReport, ModelAnswer


def model_answer(question: str, correct_answer: str, key_concepts: list[str]) -> ModelAnswer:
    return run_agent(
        "model_answer",
        {
            "question": question,
            "correct_answer": correct_answer,
            "key_concepts": ", ".join(key_concepts),
            "key_concepts_list": key_concepts,
        },
        ModelAnswer,
    )


def compile_report(
    student_name: str,
    elapsed_minutes: float,
    topics_selected: list[str],
    scores_array: list[dict],
    transcript: list[dict],
) -> InterviewReport:
    return run_agent(
        "report_compiler",
        {
            "student_name": student_name,
            "elapsed_minutes": round(elapsed_minutes, 1),
            "topics_selected": ", ".join(topics_selected),
            "scores_array": json.dumps(scores_array, ensure_ascii=False),
            "transcript": json.dumps(transcript, ensure_ascii=False)[:4000],
            # mock path reads these structured values directly:
            "scores_array_obj": scores_array,
        },
        InterviewReport,
    )
