"""Pydantic schemas for every structured agent hand-off.

These models are the contract between agents. Each agent must return an object
that validates against its schema (rubric: 'Structured outputs for important
agent handoffs').
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CoerceNoneStr(BaseModel):
    """Base model that makes LLM JSON robust:
      * `null` -> "" for fields typed as `str`
      * a float (e.g. 3.5) -> rounded int for fields typed as `int`
    LLMs (Groq/Gemini) emit these often; this avoids crashing the hand-off.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _coerce(cls, v, info):
        ann = cls.model_fields[info.field_name].annotation
        if v is None and ann is str:
            return ""
        if ann is int and isinstance(v, float):
            return round(v)
        return v


Difficulty = Literal["easy", "medium", "hard"]
Intent = Literal[
    "answer_attempt", "hint_request", "answer_seeking", "clarification", "off_topic"
]


class TopicSelection(CoerceNoneStr):
    topics: list[str]
    rationale: str = ""


class GeneratedQuestion(CoerceNoneStr):
    question: str
    topic: str
    difficulty: Difficulty
    subject: str = "Maths"
    concept_tested: str
    correct_answer: str  # internal only — never shown to the student
    key_concepts: list[str] = Field(default_factory=list)
    source_id: str | None = None  # which bank item it was grounded on (RAG provenance)


class RephrasedQuestion(CoerceNoneStr):
    question: str


class IntentResult(CoerceNoneStr):
    intent: Intent
    confidence: float = 0.5
    reasoning: str = ""


class HintResult(CoerceNoneStr):
    hint: str


class Evaluation(CoerceNoneStr):
    correctness: int = Field(ge=0, le=10)
    concept_clarity: int = Field(ge=0, le=10)
    explanation_quality: int = Field(ge=0, le=10)
    basics_strength: int = Field(ge=0, le=10)
    overall_score: float = Field(ge=0, le=10)
    student_feedback: str  # shown to student — must NOT reveal the answer/method
    internal_notes: str = ""  # never shown to student


class FollowupDecision(CoerceNoneStr):
    needs_followup: bool
    followup_question: str = ""
    followup_reason: str = ""


class ModelAnswer(CoerceNoneStr):
    answer: str  # a complete, sentence-form model answer for the report


class FollowupAssessment(CoerceNoneStr):
    satisfied: bool
    feedback: str  # 1-2 sentences spoken to the student; never reveals the answer


class PerQuestionReport(CoerceNoneStr):
    question_number: int
    topic: str
    correctness_label: str
    what_went_well: str
    what_to_improve: str
    scores: dict


class InterviewReport(CoerceNoneStr):
    student_name: str
    overall_score: float
    readiness_score: int
    summary: str
    per_question: list[PerQuestionReport] = Field(default_factory=list)
    concept_map: dict = Field(default_factory=dict)
    communication_assessment: str = ""
    top_improvements: list[str] = Field(default_factory=list)
    revision_topics: list[str] = Field(default_factory=list)
    encouragement_note: str = ""
