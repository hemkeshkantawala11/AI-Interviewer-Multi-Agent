"""Question Generator Agent — RAG-grounded.

Retrieves a verified question from the curated bank (grounding). This is where
RAG meaningfully improves the product: questions are always mathematically valid
and never repeat within a session — solving the "AI invents wrong maths" risk.

`rephrase=True` will, when a real LLM key is present, lightly reword the question
without changing its meaning/answer. We keep the verified wording by default for
maximum reliability in the demo build.
"""
from __future__ import annotations

from ..llm import run_agent
from ..schemas import GeneratedQuestion, RephrasedQuestion
from ..tools import retriever


class NoQuestionsLeft(Exception):
    pass


def generate_question(
    topic: str, difficulty: str, used_ids: set[str], rephrase: bool = False
) -> GeneratedQuestion:
    item = retriever.retrieve(topic, difficulty, exclude_ids=used_ids)
    if item is None:
        raise NoQuestionsLeft(f"No bank questions left for topic={topic!r}")

    text = item["question"]
    if rephrase:
        try:
            reworded = run_agent(
                "question_rephraser",
                {"question": item["question"], "topic": item["topic"], "difficulty": item["difficulty"]},
                RephrasedQuestion,
            )
            text = reworded.question or item["question"]
        except Exception:
            text = item["question"]

    return GeneratedQuestion(
        question=text,
        topic=item["topic"],
        difficulty=item["difficulty"],
        subject=item.get("subject", "Maths"),
        concept_tested=item["concept_tested"],
        correct_answer=item["correct_answer"],
        key_concepts=item.get("key_concepts", []),
        source_id=item["id"],
    )
