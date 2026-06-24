# AI Interviewer — Implementation Guide

This document explains **how** things are built: LangGraph patterns, state management, agent execution, routing logic, and the full lifecycle of a session.

---

## 1. LangGraph Primitives Used

### StateGraph

The entire orchestration is a single `StateGraph` compiled once at startup:

```python
# graph/build.py
g = StateGraph(InterviewState)
# ... add nodes and edges ...
app = g.compile(checkpointer=MemorySaver())
```

`StateGraph` takes the state type (`InterviewState`) and manages state transitions across node invocations. The compiled graph (`app`) is reused across all sessions.

### Nodes

Every node is a plain Python function with this signature:

```python
def node_name(state: InterviewState) -> dict:
    # read from state
    # do work
    return {only_the_fields_that_changed: new_values}
```

Nodes return **partial updates** — not the full state. LangGraph merges the returned dict into the current state for the next node. Returning a field not in the state dict raises no error; returning `{}` is valid (no-op).

### Edges

Two kinds of edges:

```python
# Deterministic: always goes here next
g.add_edge("init_session", "select_topics")

# Conditional: routing function decides
g.add_conditional_edges(
    "classify_intent",
    route_intent,                        # returns one of the keys below
    {"handle_answer": "handle_answer",   # key → target node
     "handle_hint":   "handle_hint",
     ...}
)
```

All 5 routing functions in `build.py` are pure, deterministic functions — they read state and return a string. They contain no LLM calls.

### interrupt()

The `interrupt()` call is how the graph pauses and hands control back to the caller:

```python
# graph/nodes.py — await_message node
def await_message(state: InterviewState) -> dict:
    payload = {"type": "await_message", "outbox": state.get("outbox", []), ...}
    message: str = interrupt(payload)   # graph PAUSES here
    # execution resumes here when the caller calls resume()
    return {"pending_message": message, "transcript": ..., "outbox": []}
```

The `payload` is what the caller receives. The return value of `interrupt()` is what the caller passes back via `Command(resume=value)`.

### Command(resume=value)

To resume a paused graph:

```python
# graph/build.py
def resume(app, value, config):
    return _wrap(app.invoke(Command(resume=value), config=config), app, config)
```

`Command(resume=value)` tells LangGraph to inject `value` as the return value of the last `interrupt()` call, then continue executing from that point.

### MemorySaver (checkpointer)

```python
app = g.compile(checkpointer=MemorySaver())
```

`MemorySaver` stores the full graph state in memory between `invoke()` calls. Each session has its own state keyed by `thread_id`:

```python
config = {"configurable": {"thread_id": session_id}}
app.invoke(initial_state, config=config)   # start
app.invoke(Command(resume=answer), config=config)  # resume same session
```

The same `config` across multiple `invoke()` calls links them to the same session state.

---

## 2. State Management

### InterviewState TypedDict

```python
# graph/state.py
class InterviewState(TypedDict, total=False):
    session_id: str
    question_count: int
    scores: list[dict]
    outbox: list[str]
    # ... 40+ more fields
```

`total=False` means every field is optional — nodes can return partial updates without needing to supply every field. LangGraph merges the returned dict into the running state.

### Session Factory

```python
def new_session(student_name, student_email, session_id, requested_topics=None) -> InterviewState:
    return InterviewState(
        session_id=session_id,
        question_count=0,
        scores=[],
        outbox=[],
        # all fields initialized to safe defaults
    )
```

The factory creates the initial state passed to `app.invoke()` on session start. All list fields are initialized to `[]` — never `None`.

### Manual List Accumulation

Because `InterviewState` does not use LangGraph's `Annotated` reducer annotations, every node that appends to a list reads the existing list from state and concatenates manually:

```python
# nodes.py — every node that appends to outbox
return {
    "outbox": state.get("outbox", []) + ["new message"],
    "scores": state.get("scores", []) + [score_entry],
    "transcript": state.get("transcript", []) + [{"role": "student", "text": text}],
}
```

The idiomatic LangGraph alternative would be:
```python
# What it could look like with reducers (not currently used)
class InterviewState(TypedDict, total=False):
    outbox: Annotated[list[str], operator.add]   # reducer: auto-concatenate
```
With reducers, nodes would return `{"outbox": ["new message"]}` and LangGraph would handle the merge. The current manual approach is correct for sequential graphs but is more verbose and would break silently under parallelism.

### Transient Cache: `_last_eval`

`_last_eval` is a scratch field used to pass evaluation scores between `handle_answer` and `followup_node` without them needing to be in the same function:

```python
# handle_answer writes it
return {"_last_eval": {"correctness": ev.correctness, ...}}

# followup_node reads it
last = state.get("_last_eval", {})
```

It persists in the checkpointed state (slightly wasteful) but is overwritten each question cycle.

---

## 3. Graph Construction (`build_graph`)

Full node and edge registration in `build.py`:

```python
def build_graph(checkpointer=None):
    g = StateGraph(InterviewState)

    # Register all 16 nodes
    for name, fn in [
        ("init_session",          nodes.init_session),
        ("select_topics",         nodes.select_topics),
        ("generate_question",     nodes.generate_question),
        ("await_message",         nodes.await_message),
        ("timer_check",           nodes.timer_check),
        ("classify_intent",       nodes.classify_intent),
        ("handle_answer",         nodes.handle_answer),
        ("followup_node",         nodes.followup_node),
        ("handle_followup_answer",nodes.handle_followup_answer),
        ("handle_skip",           nodes.handle_skip),
        ("handle_hint",           nodes.handle_hint),
        ("handle_guard",          nodes.handle_guard),
        ("handle_clarify",        nodes.handle_clarify),
        ("handle_offtopic",       nodes.handle_offtopic),
        ("advance",               nodes.advance),
        ("compile_report",        nodes.compile_report),
        ("finalize",              nodes.finalize),
    ]:
        g.add_node(name, fn)

    # Linear startup sequence
    g.add_edge(START, "init_session")
    g.add_edge("init_session", "select_topics")
    g.add_edge("select_topics", "generate_question")

    # Conditional edges (all 5 routers)
    g.add_conditional_edges("generate_question", route_after_generate, {...})
    g.add_conditional_edges("timer_check",        route_after_timer,    {...})
    g.add_conditional_edges("classify_intent",    route_intent,         {...})
    g.add_conditional_edges("followup_node",      route_after_followup, {...})
    g.add_conditional_edges("advance",            route_after_advance,  {...})

    # All non-answer handlers loop back to await_message
    for h in ("handle_hint", "handle_guard", "handle_clarify", "handle_offtopic"):
        g.add_edge(h, "await_message")

    g.add_edge("compile_report", "finalize")
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())
```

`build_graph()` is called once. The returned `app` is stored on the FastAPI app state and reused for all sessions.

---

## 4. Interrupt / Resume Cycle

The full lifecycle of a session across multiple HTTP calls:

```
POST /api/start
  │
  ├─ new_session() → initial_state
  ├─ app.invoke(initial_state, config={"configurable": {"thread_id": sid}})
  │    Graph runs: init_session → select_topics → generate_question → await_message
  │    await_message calls interrupt(payload) → graph PAUSES
  │    invoke() returns
  ├─ _extract_interrupt(result, app, config) → payload dict
  └─ Response: {outbox: [...], question: "...", question_number: 1}

POST /api/answer {"session_id": sid, "text": "42"}
  │
  ├─ app.invoke(Command(resume="42"), config={"configurable": {"thread_id": sid}})
  │    Graph resumes: "42" → pending_message
  │    Runs: timer_check → classify_intent → handle_answer → followup_node → await_message
  │    await_message calls interrupt(payload) again → graph PAUSES
  │    invoke() returns
  ├─ _extract_interrupt(result, app, config) → new payload
  └─ Response: {outbox: [feedback, "Follow-up: ..."], question_number: 1}

POST /api/answer {"session_id": sid, "text": "because..."}
  │  (follow-up answer — awaiting_followup is True)
  │
  ├─ app.invoke(Command(resume="because..."), config=...)
  │    Graph resumes: handle_followup_answer → advance → generate_question → await_message
  │    PAUSES again with next question
  └─ Response: {outbox: [verdict, "Question 2: ..."], question_number: 2}

... (repeat until session ends) ...

POST /api/end {"session_id": sid}
  │
  ├─ app.invoke(Command(resume="__END__"), config=...)
  │    Graph resumes: pending_message = "__END__"
  │    timer_check → route_after_timer → "compile_report"
  │    compile_report → finalize → END
  │    No more interrupts → invoke() returns with done=True
  └─ Response: {done: true, outbox: ["overall feedback..."], report: {...}}
```

### Driver Helpers

```python
# build.py
def start(app, initial, config):
    return _wrap(app.invoke(initial, config=config), app, config)

def resume(app, value, config):
    return _wrap(app.invoke(Command(resume=value), config=config), app, config)

def _wrap(result, app, config):
    payload = _extract_interrupt(result, app, config)
    state = app.get_state(config).values
    if payload is None:
        return {"done": True, "payload": ..., "state": state}
    return {"done": False, "payload": payload, "state": state}
```

`_extract_interrupt` checks both the invoke result and the graph snapshot for interrupt payloads, since different LangGraph versions surface them differently.

---

## 5. Node Implementations

### Setup Nodes

**`init_session`** — pure side-effect free, just emits welcome:
```python
def init_session(state: InterviewState) -> dict:
    return {"outbox": state.get("outbox", []) + [WELCOME], "status": "active"}
```

**`select_topics`** — honours student-picked topics, falls back to AI:
```python
def select_topics(state: InterviewState) -> dict:
    valid = set(retriever.all_topics())
    chosen = [t for t in (state.get("requested_topics") or []) if t in valid]
    if not chosen:
        chosen = topic_selector.select_topics(n=4).topics
    return {"topics_selected": chosen, "current_topic_index": 0, "current_topic": chosen[0]}
```

**`generate_question`** — RAG retrieval with no-repeat guarantee:
```python
def generate_question(state: InterviewState) -> dict:
    qc = state.get("question_count", 0)
    difficulty = DIFFICULTY_SCHEDULE[qc] if qc < len(DIFFICULTY_SCHEDULE) else DIFFICULTY_SCHEDULE[-1]
    used = set(state.get("used_question_ids", []))
    try:
        q = qg.generate_question(topic, difficulty, used)
    except qg.NoQuestionsLeft:
        return {"force_end": True, "outbox": [...]}
    return {
        "current_question": q.question,
        "correct_answer": q.correct_answer,     # stored in state; NEVER shown to student
        "used_question_ids": list(used | {q.source_id}),
        "outbox": state.get("outbox", []) + [f"Question {qc+1}: {q.question}"],
    }
```

Difficulty schedule is fixed: `["medium", "easy"]` — first question challenges, subsequent questions consolidate.

### The Interrupt Node

**`await_message`** — the only place the graph pauses:
```python
def await_message(state: InterviewState) -> dict:
    payload = {"type": "await_message", "outbox": state.get("outbox", []), ...}
    message: str = interrupt(payload)          # PAUSES — execution stops here
                                               # RESUMES when resume() is called
    if message in ("__END__", "__SKIP__"):
        return {"pending_message": message, "outbox": []}
    transcript = state.get("transcript", []) + [{"role": "student", "text": message}]
    return {"pending_message": message, "transcript": transcript, "outbox": []}
```

Outbox is cleared (`"outbox": []`) when the interrupt resumes — each turn starts with a fresh outbox.

### Evaluation Chain

**`handle_answer`** → **`followup_node`** — the core scoring pipeline:

```python
def handle_answer(state: InterviewState) -> dict:
    ev = evaluator.evaluate(
        current_question=state["current_question"],
        correct_answer=state["correct_answer"],   # never leaks to student
        student_answer=state["pending_message"],
        ...
    )
    return {
        "scores": state.get("scores", []) + [score_entry],
        "_last_eval": {"correctness": ev.correctness, ...},   # pass to followup_node
        "outbox": state.get("outbox", []) + [ev.student_feedback],
    }

def followup_node(state: InterviewState) -> dict:
    # Always generates a cross-question — strong answers push deeper, weak probe the gap
    decision = followup.decide(state["current_question"], state["pending_message"], ev)
    return {
        "awaiting_followup": True,
        "pending_followup_q": decision.followup_question,
        "outbox": state.get("outbox", []) + [f"Follow-up: {decision.followup_question}"],
    }
```

Note: `followup_node` **always** sets `awaiting_followup=True`. The agent's `needs_followup` field is computed but the routing unconditionally generates a follow-up question.

### Guardrail Nodes

**`handle_hint`** — state-tracked one-time allow:
```python
def handle_hint(state: InterviewState) -> dict:
    qkey = question_key(state)         # "q1", "q2", etc.
    hint_used = dict(state.get("hint_used", {}))
    if hint_used.get(qkey, False):
        return {"outbox": [...+ [HINT_DENIED]]}    # deterministic denial
    h = hint_agent.make_hint(...)
    hint_used[qkey] = True
    return {"hint_used": hint_used, "outbox": [...+ [f"Hint: {h.hint}"]]}
```

**`handle_guard`** — zero logic, fixed message:
```python
def handle_guard(state: InterviewState) -> dict:
    return {"outbox": state.get("outbox", []) + [GUARD_MESSAGE]}
```

### Loop Control

**`advance`** — increments counter and rotates topic:
```python
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
```

Topics rotate round-robin. `awaiting_followup` is always cleared here.

**`should_end`** — the termination condition checked by `route_after_advance`:
```python
def should_end(state: InterviewState) -> bool:
    qc = state.get("question_count", 0)
    if state.get("force_end"): return True
    if qc >= settings.hard_cap_questions: return True
    if state.get("wrap_up_flag"): return True
    return qc >= settings.questions_target
```

---

## 6. Routing Logic

All 5 routers are pure functions in `build.py` — they read state and return a string.

### `route_after_timer`

The most complex router — handles all emergency exits:

```python
def route_after_timer(state: InterviewState) -> str:
    if state.get("end_requested") or state.get("force_end"):
        return "compile_report"      # hard stop or student requested end
    if state.get("skip_requested"):
        return "handle_skip"         # inactivity timeout
    if state.get("awaiting_followup"):
        return "handle_followup_answer"   # we're mid-follow-up
    return "classify_intent"         # normal turn
```

This runs **every turn**, making it the single enforcement point for timing and session control.

### `route_intent`

Simple lookup — uses `_INTENT_TO_NODE` dict with `handle_offtopic` as default:

```python
_INTENT_TO_NODE = {
    "answer_attempt": "handle_answer",
    "hint_request":   "handle_hint",
    "answer_seeking": "handle_guard",
    "clarification":  "handle_clarify",
    "off_topic":      "handle_offtopic",
}

def route_intent(state: InterviewState) -> str:
    return _INTENT_TO_NODE.get(state.get("last_intent", ""), "handle_offtopic")
```

Unknown intents are treated as off-topic — safe default.

---

## 7. Agent Execution (`llm.py`)

### `run_agent(name, variables)`

Every AI operation goes through this single function:

```python
def run_agent(name: str, variables: dict) -> dict:
    entry = _REGISTRY[name]          # (prompt_template, mock_key, tier)
    
    if name not in settings.llm_agents:
        return _mock(entry.mock_key, variables)    # skip real LLM for this agent
    
    for attempt in range(2):          # one retry on failure
        try:
            prompt = entry.prompt.format(**variables)
            raw = _call_provider(entry.tier, prompt)   # Groq / Gemini / Anthropic
            data = _extract_json(raw)                   # strip markdown fences
            return data
        except Exception:
            if attempt == 0: continue
            return _mock(entry.mock_key, variables)    # fallback to mock on second failure
```

### Agent Registry

```python
_REGISTRY = {
    "evaluator":      AgentEntry(EVALUATOR,      "evaluator",      "smart"),
    "intent_classifier": AgentEntry(INTENT_CLASSIFIER, "intent_classifier", "fast"),
    "report_compiler": AgentEntry(REPORT_COMPILER, "report_compiler", "smart"),
    # ... all 9 agents
}
```

Each entry holds the prompt template, the mock function key, and the tier (`"smart"` or `"fast"`).

### Tier → Model Mapping

```python
# Per provider
Groq:      fast → groq_model,    smart → groq_model   (single model)
Gemini:    fast → gemini_model,  smart → gemini_model
Anthropic: fast → MODEL_FAST (haiku), smart → MODEL_SMART (sonnet)
```

### JSON Extraction

LLMs frequently wrap JSON in markdown fences. `_extract_json` handles this:

```python
def _extract_json(raw: str) -> dict:
    # Strip ```json ... ``` fences
    # Try json.loads directly
    # Try regex: find first {...} block
    # Raise ParseError if nothing works
```

### Pydantic Validation

After `run_agent` returns a dict, each agent module validates it:

```python
# agents/evaluator.py
def evaluate(...) -> Evaluation:
    data = run_agent("evaluator", variables)
    return Evaluation(**data)   # validates types, ranges, coerces nulls
```

`CoerceNoneStr` (base class for all schemas) handles two common LLM quirks:
- `null` string fields → `""`
- `3.5` integer fields → `3` (float overflow from LLM)

---

## 8. Guardrail System

### Answer-Leak Scrubber

After every evaluation, the `correct_answer` is scrubbed from `student_feedback`:

```python
# agents/evaluator.py
def _scrub(text: str, answer: str) -> str:
    if not answer:
        return text
    pattern = re.escape(answer.strip())
    if re.search(pattern, text, re.IGNORECASE):
        return "Good effort — keep working through the reasoning on your own."
    return text
```

Applied twice: in `evaluator.py` and in `followup.py` (for follow-up feedback).

### Intent Classifier Bias

The intent classifier prompt is written to be biased toward `answer_seeking` on ambiguous cases:

```
If the student is asking for a method, formula, approach, or the answer itself
in any indirect form — classify as answer_seeking, not answer_attempt.
```

This ensures "how would I solve this?" is blocked, not scored.

### Topic Invariants

```python
# agents/topic_selector.py
def select_topics(n=4) -> TopicSelection:
    result = run_agent("topic_selector", {...})
    topics = result.topics
    # Post-processing guardrails (applied even if LLM ignores them):
    if "Probability" not in topics:
        topics = ["Probability"] + [t for t in topics if t != "Probability"][:n-1]
    if not any("Logical Reasoning" in t for t in topics):
        topics[-1] = "Logical Reasoning: Sequences"   # force at least one LR topic
    return TopicSelection(topics=topics[:n], ...)
```

---

## 9. Tools Integration

### RAG Retriever

```python
# tools/retriever.py
@lru_cache(maxsize=1)
def _load_bank() -> list[dict]:
    path = Path(__file__).parent.parent / "data" / "question_bank.json"
    return json.loads(path.read_text())

def retrieve(topic: str, difficulty: str, exclude_ids: set[str]) -> dict | None:
    bank = _load_bank()
    pool = [q for q in bank
            if q["topic"] == topic
            and q["difficulty"] == difficulty
            and q.get("id") not in exclude_ids]
    if not pool:
        pool = [q for q in bank if q["topic"] == topic and q.get("id") not in exclude_ids]
    if not pool:
        pool = [q for q in bank if q.get("id") not in exclude_ids]
    if not pool:
        return None                  # caller raises NoQuestionsLeft
    return random.choice(pool)
```

The `lru_cache` ensures the JSON file is read exactly once per process.

### Emailer

```python
# tools/emailer.py
def send_email(to, subject, html_body, tag) -> dict:
    path = Path(f"./reports/{tag}.html")
    path.parent.mkdir(exist_ok=True)
    path.write_text(html_body)      # always write locally (audit trail)
    
    if all([settings.smtp_host, settings.smtp_user, settings.smtp_password]):
        # Send via SMTP
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.sendmail(settings.smtp_user, to, msg.as_string())
        return {"delivered": True, "simulated": False, "path": str(path), "to": to}
    else:
        return {"delivered": False, "simulated": True, "path": str(path), "to": to}
```

`send_email` is called from the `finalize` node. If SMTP is not configured, the report is saved locally and delivery is flagged as simulated — the graph completes either way.

---

## 10. Report Generation

### compile_report

```python
def compile_report(state: InterviewState) -> dict:
    report = report_compiler.compile_report(
        student_name=state.get("student_name", "Student"),
        elapsed_minutes=state.get("elapsed_minutes", 0.0),
        topics_selected=state.get("topics_selected", []),
        scores_array=state.get("scores", []),
        transcript=state.get("transcript", []),
    )
    # Also generate ideal answers for each question (from verified bank, post-interview only)
    for s in state.get("scores", []):
        ma = report_compiler.model_answer(s["question"], s["correct_answer"], s["key_concepts"])
        # attach to per_question entries
    return {"report": report.model_dump(), "outbox": [...+ [overall_feedback]]}
```

The `report_compiler` agent receives the full accumulated session memory (scores + transcript) and synthesises a structured `InterviewReport`. Ideal answers are generated **only here** — never during the interview.

### HTML Report

```python
# report/html_report.py
def render_report_html(report: dict, for_mentor: bool = False) -> str:
    # Produces a self-contained, styled HTML report
    # All values HTML-escaped
    # Dark/light theme toggle via CSS custom properties
    # Sections: summary, per-question breakdown, concept map, improvement areas
```

---

## 11. Deterministic Mock (`mock.py`)

The mock module provides offline equivalents for every agent. Used when:
- No API key is configured
- An agent is not in `LLM_AGENTS`
- A real LLM call fails after retry

```python
# mock.py — evaluator example
def evaluator(v: dict) -> dict:
    student = re.sub(r'[^a-z0-9]', '', v["student_answer"].lower())
    correct = re.sub(r'[^a-z0-9]', '', v["correct_answer"].lower())
    
    if student == correct:
        correctness = 9
    elif correct in student:
        correctness = 6
    elif any(kw in student for kw in v["key_concepts"]):
        correctness = 3
    else:
        correctness = 0
    
    # ... compute other dimensions ...
    return {"correctness": correctness, "student_feedback": "...", ...}
```

This enables:
- Fully offline demos
- Deterministic testing (no LLM calls = reproducible results)
- Cost-free development

---

## 12. Testing Strategy

**9 test cases** in `tests/test_interview.py`:

| Test | What it verifies |
|---|---|
| Difficulty calibration | Correct threshold mapping (score → next difficulty) |
| RAG retrieval | Questions come from bank; no repeats via exclude_ids |
| Intent classification (×5) | Each intent correctly classified from sample inputs |
| Feedback scrubber | Correct answer not present in student feedback |
| Timer hard-stop | 45-min-old session → force_end=True |
| Answer-seeking guard (e2e) | Guard fires, no score; subsequent answer scores correctly |
| Hint-once guardrail (e2e) | First hint granted, second denied; answer scored |
| Full happy path (e2e) | 2 questions answered → report generated → delivered |
| Completion feedback (e2e) | Session ends → "overall feedback" in outbox |

All tests run against the mock LLM (no API key required). The e2e tests use `start()` and `resume()` to run the full graph, not individual nodes.

---

## Known Gaps

| Gap | Detail |
|---|---|
| No LangGraph reducers | Lists accumulated manually; would break under parallelism |
| `mentor_review` node missing | Documented in graph diagram; not wired in build_graph() |
| `calibrate` node not wired | calibrator.py exists but edge goes handle_answer → followup_node directly |
| `__END__`/`__SKIP__` routing | Stored in pending_message; router checks end_requested/skip_requested flags which are never set from this path |
| `followup_node` ignores agent | Always sets awaiting_followup=True; agent's needs_followup is computed but unused |
