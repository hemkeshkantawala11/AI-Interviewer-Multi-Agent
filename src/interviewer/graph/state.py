"""Shared graph state (rubric: state management across steps)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, TypedDict


class InterviewState(TypedDict, total=False):
    # identity
    session_id: str
    student_name: str
    student_email: str
    start_time: str

    # topic / question progression
    requested_topics: list[str]  # topics the student picked at the start (optional)
    topics_selected: list[str]
    current_topic_index: int
    current_topic: str
    current_difficulty: str
    question_count: int
    used_question_ids: list[str]

    # current question (internal metadata kept here, never surfaced to student)
    current_question: str
    current_subject: str
    correct_answer: str
    concept_tested: str
    key_concepts: list[str]
    current_source_id: str

    # per-message working values
    pending_message: str
    last_intent: str
    awaiting_followup: bool
    pending_followup_q: str  # the follow-up question currently awaiting an answer
    _last_eval: dict  # transient: last evaluation summary for calibrate/followup

    # hint tracking
    hint_used: dict  # {"q1": bool, ...}

    # timing / control
    elapsed_minutes: float
    timer_status: str
    warning_sent: bool
    wrap_up_flag: bool
    force_end: bool
    end_requested: bool  # student clicked "End interview" — wrap up early
    skip_requested: bool  # inactivity: skip the current question and move on
    status: str

    # accumulated data
    scores: list[dict]
    transcript: list[dict]
    outbox: list[str]  # bot messages queued for display, flushed each turn

    # reporting
    report: dict
    report_html: str
    delivery: dict


def new_session(student_name: str, student_email: str, session_id: str,
                requested_topics: list[str] | None = None) -> InterviewState:
    return InterviewState(
        session_id=session_id,
        student_name=student_name,
        student_email=student_email,
        start_time=datetime.now(timezone.utc).isoformat(),
        requested_topics=requested_topics or [],
        topics_selected=[],
        current_topic_index=0,
        current_topic="",
        current_difficulty="medium",  # all questions start at medium
        question_count=0,
        used_question_ids=[],
        current_question="",
        current_subject="Maths",
        correct_answer="",
        concept_tested="",
        key_concepts=[],
        current_source_id="",
        pending_message="",
        last_intent="",
        awaiting_followup=False,
        hint_used={},
        elapsed_minutes=0.0,
        timer_status="normal",
        warning_sent=False,
        wrap_up_flag=False,
        force_end=False,
        end_requested=False,
        skip_requested=False,
        status="active",
        scores=[],
        transcript=[],
        outbox=[],
        report={},
        report_html="",
        delivery={},
    )


def question_key(state: InterviewState) -> str:
    return f"q{state.get('question_count', 0) + 1}"
