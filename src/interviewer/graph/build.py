"""Assemble the LangGraph state machine and provide driver helpers.

Graph shape (■ = interrupt / human turn):

  START → init_session → select_topics → generate_question ─┐
                                                            │
   ┌──────────────────────── await_message ■ ◄─────────────┘
   │                              │
   │                         timer_check ──(force_end)──────────────► compile_report
   │                              │ (awaiting_followup) ► handle_followup_answer ► advance
   │                         classify_intent
   │                              │  [conditional router on intent]
   │   answer ► handle_answer ► calibrate ► followup_node ─(needs)─► await_message ■
   │                                              └─(no)─► advance
   └── hint / guard / clarify / off_topic ──────────────────────────► await_message ■
                                  advance ─(continue)─► generate_question
                                          └─(end)─────► compile_report ► mentor_review ■
                                                                          ├─approve─► finalize → END
                                                                          └─hold────► held → END
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..config import settings
from . import nodes
from .state import InterviewState

_INTENT_TO_NODE = {
    "answer_attempt": "handle_answer",
    "hint_request": "handle_hint",
    "answer_seeking": "handle_guard",
    "clarification": "handle_clarify",
    "off_topic": "handle_offtopic",
}


# --------------------------- routing (deterministic) --------------------------- #
def should_end(state: InterviewState) -> bool:
    qc = state.get("question_count", 0)
    if state.get("force_end"):
        return True
    if qc >= settings.hard_cap_questions:
        return True
    if state.get("wrap_up_flag"):
        return True
    return qc >= settings.questions_target


def route_after_generate(state: InterviewState) -> str:
    return "compile_report" if state.get("force_end") else "await_message"


def route_after_timer(state: InterviewState) -> str:
    if state.get("end_requested") or state.get("force_end"):
        return "compile_report"
    if state.get("skip_requested"):
        return "handle_skip"
    if state.get("awaiting_followup"):
        return "handle_followup_answer"
    return "classify_intent"


def route_intent(state: InterviewState) -> str:
    return _INTENT_TO_NODE.get(state.get("last_intent", ""), "handle_offtopic")


def route_after_followup(state: InterviewState) -> str:
    return "await_message" if state.get("awaiting_followup") else "advance"


def route_after_advance(state: InterviewState) -> str:
    return "compile_report" if should_end(state) else "generate_question"


# --------------------------------- build --------------------------------- #
def build_graph(checkpointer=None):
    g = StateGraph(InterviewState)

    for name, fn in [
        ("init_session", nodes.init_session),
        ("select_topics", nodes.select_topics),
        ("generate_question", nodes.generate_question),
        ("await_message", nodes.await_message),
        ("timer_check", nodes.timer_check),
        ("classify_intent", nodes.classify_intent),
        ("handle_answer", nodes.handle_answer),
        ("followup_node", nodes.followup_node),
        ("handle_followup_answer", nodes.handle_followup_answer),
        ("handle_skip", nodes.handle_skip),
        ("handle_hint", nodes.handle_hint),
        ("handle_guard", nodes.handle_guard),
        ("handle_clarify", nodes.handle_clarify),
        ("handle_offtopic", nodes.handle_offtopic),
        ("advance", nodes.advance),
        ("compile_report", nodes.compile_report),
        ("finalize", nodes.finalize),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "init_session")
    g.add_edge("init_session", "select_topics")
    g.add_edge("select_topics", "generate_question")
    g.add_conditional_edges("generate_question", route_after_generate,
                            {"await_message": "await_message", "compile_report": "compile_report"})
    g.add_edge("await_message", "timer_check")
    g.add_conditional_edges("timer_check", route_after_timer,
                            {"classify_intent": "classify_intent",
                             "handle_followup_answer": "handle_followup_answer",
                             "handle_skip": "handle_skip",
                             "compile_report": "compile_report"})
    g.add_conditional_edges("classify_intent", route_intent,
                            {v: v for v in set(_INTENT_TO_NODE.values())})

    g.add_edge("handle_answer", "followup_node")
    g.add_conditional_edges("followup_node", route_after_followup,
                            {"await_message": "await_message", "advance": "advance"})
    g.add_edge("handle_followup_answer", "advance")
    g.add_edge("handle_skip", "advance")

    for h in ("handle_hint", "handle_guard", "handle_clarify", "handle_offtopic"):
        g.add_edge(h, "await_message")

    g.add_conditional_edges("advance", route_after_advance,
                            {"generate_question": "generate_question", "compile_report": "compile_report"})
    g.add_edge("compile_report", "finalize")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


# --------------------------------- driver --------------------------------- #
def _extract_interrupt(result, app, config):
    intr = None
    if isinstance(result, dict) and result.get("__interrupt__"):
        intr = result["__interrupt__"]
    if not intr:
        snap = app.get_state(config)
        if snap.next:
            for task in snap.tasks:
                if getattr(task, "interrupts", None):
                    intr = task.interrupts
                    break
    if intr:
        return intr[0].value
    return None


def _wrap(result, app, config):
    payload = _extract_interrupt(result, app, config)
    state = app.get_state(config).values
    if payload is None:
        return {"done": True, "payload": {"type": "done", "outbox": state.get("outbox", [])}, "state": state}
    return {"done": False, "payload": payload, "state": state}


def start(app, initial, config):
    return _wrap(app.invoke(initial, config=config), app, config)


def resume(app, value, config):
    return _wrap(app.invoke(Command(resume=value), config=config), app, config)
