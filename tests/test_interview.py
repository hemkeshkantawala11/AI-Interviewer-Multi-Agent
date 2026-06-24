"""Evaluation suite (rubric: >=5 test cases / scenarios).

Covers: guardrails (answer-seeking, hint-once, answer-leak scrub), deterministic
calibration, RAG grounding/no-repeat, intent routing, timer hard-stop, and a full
end-to-end happy path through the LangGraph with human-in-the-loop approval.
"""
from datetime import datetime, timedelta, timezone

import pytest

from interviewer.agents import evaluator
from interviewer.config import settings
from interviewer.graph import calibrator, timer
from interviewer.graph.build import build_graph, resume, start
from interviewer.graph.state import new_session
from interviewer.tools import retriever


# ----------------------------- helpers ----------------------------- #
def drive(app, config, initial, answers, mentor=("approve", "Great effort.")):
    """Run the graph to completion, feeding scripted answers + mentor decision.
    Returns (all_assistant_messages, final_state)."""
    msgs, it = [], iter(answers)
    step = start(app, initial, config)
    for _ in range(80):  # safety cap
        msgs += step["payload"].get("outbox", [])
        if step["done"]:
            return msgs, step["state"]
        ptype = step["payload"]["type"]
        if ptype == "await_message":
            try:
                ans = next(it)
            except StopIteration:
                ans = "My best attempt is 42."
            step = resume(app, ans, config)
        elif ptype == "mentor_review":
            step = resume(app, {"decision": mentor[0], "notes": mentor[1]}, config)
        else:
            step = resume(app, "", config)
    raise AssertionError("Graph did not terminate within cap")


# --------------------------- 1. calibrator -------------------------- #
@pytest.mark.parametrize("score,expected", [(9, "hard"), (8, "hard"), (7, "medium"),
                                            (5, "medium"), (4.9, "easy"), (0, "easy")])
def test_difficulty_calibration(score, expected):
    assert calibrator.next_difficulty(score) == expected


# --------------------------- 2. RAG retriever ----------------------- #
def test_rag_retrieval_and_no_repeat():
    seen = set()
    for _ in range(5):
        item = retriever.retrieve("Probability", "medium", exclude_ids=seen)
        assert item is not None
        assert item["id"] not in seen  # no repeats within a session
        seen.add(item["id"])
    # topic grounding: first pick for Probability/medium should be a Probability item
    first = retriever.retrieve("Probability", "medium", exclude_ids=set())
    assert first["topic"] == "Probability"


# --------------------------- 3. intent routing ---------------------- #
@pytest.mark.parametrize("msg,intent", [
    ("what is the answer?", "answer_seeking"),
    ("just tell me the method", "answer_seeking"),
    ("I'm stuck, can I get a hint", "hint_request"),
    ("can you repeat the question?", "clarification"),
    ("the probability is 1/3", "answer_attempt"),
    ("what's the weather like?", "off_topic"),
])
def test_intent_classification(msg, intent):
    from interviewer.agents import intent_classifier
    assert intent_classifier.classify(msg, "Some question").intent == intent


# --------------------------- 4. evaluator scrub --------------------- #
def test_feedback_never_leaks_answer():
    # plant the answer inside feedback and ensure the scrubber removes it
    leaked = evaluator._scrub("The correct answer is 350 obviously.", "350")
    assert "350" not in leaked


# --------------------------- 5. timer hard-stop --------------------- #
def test_timer_hard_stop_sets_force_end():
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    out = timer.evaluate_timer(old, warning_sent=False, wrap_up_flag=False)
    assert out["force_end"] is True
    assert out["timer_status"] == "hard_stop"


# --------------------------- 6. answer-seeking guard (e2e) ---------- #
def test_guard_blocks_answer_seeking(monkeypatch):
    monkeypatch.setattr(settings, "questions_target", 1)
    app = build_graph()
    cfg = {"configurable": {"thread_id": "t_guard"}}
    init = new_session("Guard Test", "g@example.com", "t_guard")
    # First try to extract the answer (blocked), then actually answer.
    msgs, state = drive(app, cfg, init, ["what is the answer?", "1/3 is my answer"])
    blob = "\n".join(msgs)
    assert "can't give you the answer" in blob.lower()
    assert state["status"] == "completed"
    # the answer-seeking turn must NOT have produced a score
    assert len(state["scores"]) == 1


# --------------------------- 7. hint-once guardrail (e2e) ----------- #
def test_hint_allowed_once(monkeypatch):
    monkeypatch.setattr(settings, "questions_target", 1)
    app = build_graph()
    cfg = {"configurable": {"thread_id": "t_hint"}}
    init = new_session("Hint Test", "h@example.com", "t_hint")
    msgs, state = drive(app, cfg, init, ["hint", "hint", "1/3 final answer"])
    blob = "\n".join(msgs).lower()
    assert "hint:" in blob                      # first hint given
    assert "already used your one hint" in blob  # second denied
    assert state["status"] == "completed"


# --------------------------- 8. full happy path (e2e) --------------- #
def test_full_happy_path(monkeypatch):
    monkeypatch.setattr(settings, "questions_target", 2)
    app = build_graph()
    cfg = {"configurable": {"thread_id": "t_happy"}}
    init = new_session("Asha", "asha@example.com", "t_happy")
    answers = ["1/3 because 2 of 6 outcomes exceed 4",
               "the reasoning is two favourable outcomes over six",
               "350 by 7C3 times 5C2",
               "I used combinations for men and women separately"]
    msgs, state = drive(app, cfg, init, answers, mentor=("approve", "Keep practising."))
    assert state["status"] == "completed"
    assert state["report"]["student_name"] == "Asha"
    assert state["delivery"]["to"] == "asha@example.com"
    # report has per-question entries equal to number of scored questions
    assert len(state["scores"]) >= 2
    assert "readiness_score" in state["report"]


# --------------------------- 9. overall feedback at end (e2e) ------- #
def test_overall_feedback_and_completion(monkeypatch):
    monkeypatch.setattr(settings, "questions_target", 1)
    app = build_graph()
    cfg = {"configurable": {"thread_id": "t_final"}}
    init = new_session("Final Test", "f@example.com", "t_final")
    msgs, state = drive(app, cfg, init, ["1/3 is my answer", "two of six outcomes"])
    assert state["status"] == "completed"
    # an overall-feedback message is spoken at the end, and the report is delivered
    assert any("overall feedback" in m.lower() for m in msgs)
    assert state["delivery"]["to"] == "f@example.com"
