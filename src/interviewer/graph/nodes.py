"""Graph node functions. Each takes the state and returns a partial update.

AI nodes delegate to the agents; deterministic nodes (timer, calibrate, advance,
routers) contain plain logic. The two `interrupt()` points are the student turn
and the mentor approval (human-in-the-loop).
"""
from __future__ import annotations

from langgraph.types import interrupt

from ..agents import evaluator, followup, hint_agent, intent_classifier
from ..agents import question_generator as qg
from ..agents import report_compiler, topic_selector
from ..config import settings
from ..report.html_report import render_report_html
from ..tools import retriever
from ..tools.emailer import send_email
from . import timer
from .state import InterviewState, question_key

# ---- fixed (no-LLM) guardrail messages ----
GUARD_MESSAGE = (
    "I can't give you the answer or the method — that's the whole point of this interview. "
    "Take your best attempt, or type 'hint' for one conceptual nudge."
)
HINT_DENIED = (
    "You've already used your one hint for this question. Give it your best attempt — you've got this!"
)
WELCOME = (
    "Welcome to your AI mock interview covering Maths and Logical Reasoning. "
    "I'll ask you a couple of questions and listen to your reasoning — explain your thinking "
    "in your own words. Let's begin!"
)

# Fixed difficulty progression: open with a medium question to challenge, then an easy one.
DIFFICULTY_SCHEDULE = ["medium", "easy"]


# --------------------------------------------------------------------------- #
# Setup nodes
# --------------------------------------------------------------------------- #
def init_session(state: InterviewState) -> dict:
    return {"outbox": state.get("outbox", []) + [WELCOME], "status": "active"}


def select_topics(state: InterviewState) -> dict:
    # If the student picked topics at the start, honour them; otherwise auto-select.
    valid = set(retriever.all_topics())
    chosen = [t for t in (state.get("requested_topics") or []) if t in valid]
    if not chosen:
        chosen = topic_selector.select_topics(n=4).topics
    return {
        "topics_selected": chosen,
        "current_topic_index": 0,
        "current_topic": chosen[0],
    }


def generate_question(state: InterviewState) -> dict:
    qc = state.get("question_count", 0)
    topic = state["current_topic"]
    difficulty = DIFFICULTY_SCHEDULE[qc] if qc < len(DIFFICULTY_SCHEDULE) else DIFFICULTY_SCHEDULE[-1]
    used = set(state.get("used_question_ids", []))
    try:
        q = qg.generate_question(topic, difficulty, used)
    except qg.NoQuestionsLeft:
        return {"force_end": True,
                "outbox": state.get("outbox", []) + ["We've run through the available questions — wrapping up."]}
    # Do NOT reveal topic or difficulty in the question label.
    label = f"Question {qc + 1}: {q.question}"
    return {
        "current_question": q.question,
        "current_subject": q.subject,
        "current_difficulty": difficulty,
        "correct_answer": q.correct_answer,
        "concept_tested": q.concept_tested,
        "key_concepts": q.key_concepts,
        "current_source_id": q.source_id or "",
        "used_question_ids": list(used | {q.source_id}) if q.source_id else list(used),
        "outbox": state.get("outbox", []) + [label],
    }


# --------------------------------------------------------------------------- #
# Student turn (interrupt) + timer + classification
# --------------------------------------------------------------------------- #
def await_message(state: InterviewState) -> dict:
    payload = {
        "type": "await_message",
        "outbox": state.get("outbox", []),
        "question": state.get("current_question", ""),
        "question_number": state.get("question_count", 0) + 1,
    }
    message: str = interrupt(payload)  # pauses here; resumes with the student's text
    if message in ("__END__", "__SKIP__"):  # control sentinels — don't record them
        return {"pending_message": message, "outbox": []}
    transcript = state.get("transcript", []) + [{"role": "student", "text": message}]
    return {"pending_message": message, "transcript": transcript, "outbox": []}


def timer_check(state: InterviewState) -> dict:
    t = timer.evaluate_timer(
        state["start_time"], state.get("warning_sent", False), state.get("wrap_up_flag", False)
    )
    return {
        "elapsed_minutes": t["elapsed_minutes"],
        "timer_status": t["timer_status"],
        "warning_sent": t["warning_sent"],
        "wrap_up_flag": t["wrap_up_flag"],
        "force_end": t["force_end"],
        "outbox": state.get("outbox", []) + t["messages"],
    }


def classify_intent(state: InterviewState) -> dict:
    res = intent_classifier.classify(state["pending_message"], state.get("current_question", ""))
    return {"last_intent": res.intent}


# --------------------------------------------------------------------------- #
# Intent handlers
# --------------------------------------------------------------------------- #
def handle_answer(state: InterviewState) -> dict:
    ev = evaluator.evaluate(
        current_question=state["current_question"],
        subject=state.get("current_subject", "Maths"),
        correct_answer=state["correct_answer"],
        key_concepts=state.get("key_concepts", []),
        student_answer=state["pending_message"],
    )
    qkey = question_key(state)
    score_entry = {
        "question_number": state.get("question_count", 0) + 1,
        "question": state["current_question"],
        "topic": state["current_topic"],
        "difficulty": state.get("current_difficulty", "medium"),
        "correctness": ev.correctness,
        "concept_clarity": ev.concept_clarity,
        "explanation_quality": ev.explanation_quality,
        "basics_strength": ev.basics_strength,
        "overall_score": ev.overall_score,
        "hint_used": state.get("hint_used", {}).get(qkey, False),
        "student_feedback": ev.student_feedback,
        "internal_notes": ev.internal_notes,
        # kept for the report's "ideal answer" section (shown only after the interview)
        "correct_answer": state.get("correct_answer", ""),
        "key_concepts": state.get("key_concepts", []),
        "concept_tested": state.get("concept_tested", ""),
    }
    transcript = state.get("transcript", []) + [{"role": "interviewer", "text": ev.student_feedback}]
    return {
        "scores": state.get("scores", []) + [score_entry],
        "_last_eval": {"correctness": ev.correctness, "concept_clarity": ev.concept_clarity,
                       "overall_score": ev.overall_score, "internal_notes": ev.internal_notes},
        "transcript": transcript,
        "outbox": state.get("outbox", []) + [ev.student_feedback],
    }


def followup_node(state: InterviewState) -> dict:
    """Always cross-question once per main question.

    If the answer was strong, push deeper (a different approach or a related
    concept); if it was weak, probe the misunderstanding. Never leads to the answer.
    """
    from ..schemas import Evaluation

    last = state.get("_last_eval", {})
    ev = Evaluation(
        correctness=last.get("correctness", 5), concept_clarity=last.get("concept_clarity", 5),
        explanation_quality=5, basics_strength=5, overall_score=last.get("overall_score", 5),
        student_feedback="", internal_notes=last.get("internal_notes", ""),
    )
    decision = followup.decide(state["current_question"], state["pending_message"], ev)
    cross_q = decision.followup_question or (
        "Can you take this a step further — explain the reasoning behind your approach?"
    )
    return {"awaiting_followup": True,
            "pending_followup_q": cross_q,
            "outbox": state.get("outbox", []) + [f"Follow-up: {cross_q}"]}


def handle_followup_answer(state: InterviewState) -> dict:
    # Judge the student's follow-up explanation (LLM) and give an honest verdict
    # — satisfied / unclear / not convinced — before moving on.
    verdict = followup.assess(
        current_question=state.get("current_question", ""),
        followup_question=state.get("pending_followup_q", ""),
        student_answer=state["pending_message"],
        correct_answer=state.get("correct_answer", ""),
    )
    transcript = state.get("transcript", []) + [{"role": "interviewer", "text": verdict.feedback}]
    return {"awaiting_followup": False, "pending_followup_q": "", "transcript": transcript,
            "outbox": state.get("outbox", []) + [verdict.feedback]}


def handle_skip(state: InterviewState) -> dict:
    """Inactivity skip: move on with a short feedback. If we were on the main
    question (not a cross-question), record a zero score so the report reflects it."""
    fb = "No answer was recorded for this question, so we'll move on. Be sure to review this topic afterwards."
    updates: dict = {"skip_requested": False, "awaiting_followup": False,
                     "transcript": state.get("transcript", []) + [{"role": "interviewer", "text": fb}],
                     "outbox": state.get("outbox", []) + [fb]}
    if not state.get("awaiting_followup"):  # skipping the main question -> record a 0 score
        updates["scores"] = state.get("scores", []) + [{
            "question_number": state.get("question_count", 0) + 1,
            "question": state.get("current_question", ""),
            "topic": state.get("current_topic", ""),
            "difficulty": state.get("current_difficulty", "medium"),
            "correctness": 0, "concept_clarity": 0, "explanation_quality": 0, "basics_strength": 0,
            "overall_score": 0.0, "hint_used": state.get("hint_used", {}).get(question_key(state), False),
            "student_feedback": fb, "internal_notes": "skipped due to inactivity",
            "correct_answer": state.get("correct_answer", ""),
            "key_concepts": state.get("key_concepts", []),
            "concept_tested": state.get("concept_tested", ""),
        }]
    return updates


def handle_hint(state: InterviewState) -> dict:
    qkey = question_key(state)
    hint_used = dict(state.get("hint_used", {}))
    if hint_used.get(qkey, False):
        return {"outbox": state.get("outbox", []) + [HINT_DENIED]}
    h = hint_agent.make_hint(state["current_question"], state.get("concept_tested", ""), state["correct_answer"])
    hint_used[qkey] = True
    return {"hint_used": hint_used, "outbox": state.get("outbox", []) + [f"Hint: {h.hint}"]}


def handle_guard(state: InterviewState) -> dict:
    return {"outbox": state.get("outbox", []) + [GUARD_MESSAGE]}


def handle_clarify(state: InterviewState) -> dict:
    q = state.get("current_question", "")
    msg = (f"Sure — here's the question again: {q}\n"
           "(I can't add hints here, but type 'hint' if you'd like one nudge.)")
    return {"outbox": state.get("outbox", []) + [msg]}


def handle_offtopic(state: InterviewState) -> dict:
    q = state.get("current_question", "")
    msg = f"Let's stay focused on the interview. Here's the current question again: {q}"
    return {"outbox": state.get("outbox", []) + [msg]}


# --------------------------------------------------------------------------- #
# Loop control
# --------------------------------------------------------------------------- #
def advance(state: InterviewState) -> dict:
    qc = state.get("question_count", 0) + 1
    topics = state.get("topics_selected", [])
    idx = (state.get("current_topic_index", 0) + 1) % max(1, len(topics))
    return {
        "question_count": qc,
        "current_topic_index": idx,
        "current_topic": topics[idx] if topics else state.get("current_topic", ""),
        "awaiting_followup": False,
    }


# --------------------------------------------------------------------------- #
# Reporting + human-in-the-loop
# --------------------------------------------------------------------------- #
def compile_report(state: InterviewState) -> dict:
    # Synthesise the whole session (per-question scores + transcript = the
    # accumulated 'memory') into a curated overall feedback + structured report.
    report = report_compiler.compile_report(
        student_name=state.get("student_name", "Student"),
        elapsed_minutes=state.get("elapsed_minutes", 0.0),
        topics_selected=state.get("topics_selected", []),
        scores_array=state.get("scores", []),
        transcript=state.get("transcript", []),
    )
    report_dict = report.model_dump()

    # Attach ideal/model answers per question (deterministic, from the verified bank
    # — accurate, and only shown AFTER the interview, never during it).
    ideal_by_q = {}
    for s in state.get("scores", []):
        ma = report_compiler.model_answer(
            s.get("question", ""), s.get("correct_answer", ""), s.get("key_concepts") or []
        )
        ideal_by_q[s.get("question_number")] = {
            "question": s.get("question", ""), "topic": s.get("topic", ""), "ideal_answer": ma.answer,
        }
    for pq in report_dict.get("per_question", []):
        info = ideal_by_q.get(pq.get("question_number"))
        if info:
            pq["ideal_answer"] = info["ideal_answer"]
            pq.setdefault("question", info["question"])
    if not report_dict.get("per_question"):
        report_dict["per_question"] = [
            {"question_number": qn, "topic": v["topic"], "question": v["question"],
             "correctness_label": "", "what_went_well": "", "what_to_improve": "",
             "scores": {}, "ideal_answer": v["ideal_answer"]}
            for qn, v in ideal_by_q.items()
        ]

    overall = f"That brings us to the end of the interview. Here is my overall feedback: {report.summary}"
    if report.encouragement_note:
        overall += " " + report.encouragement_note
    return {
        "report": report_dict,
        "outbox": state.get("outbox", []) + [overall],
    }


def finalize(state: InterviewState) -> dict:
    # Render the student report, persist a copy (delivery tool), and complete.
    report = dict(state.get("report", {}))
    html = render_report_html(report, for_mentor=False)
    delivery = send_email(
        to=state.get("student_email", "student@example.com"),
        subject="Your AI Mock Interview — Feedback Report",
        html_body=html,
        tag=f"student_{state.get('session_id', 'session')}",
    )
    return {
        "report": report,
        "report_html": html,
        "delivery": delivery,
        "status": "completed",
    }
