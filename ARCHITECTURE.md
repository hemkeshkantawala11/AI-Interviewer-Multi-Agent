# AI Interviewer — System Architecture

## Overview

AI Interviewer is a **multi-agent, LangGraph-orchestrated mock interview system** for JEE-level Maths and Logical Reasoning. A student answers spoken or typed questions; a suite of AI agents evaluates intent, scores answers, generates follow-ups, and synthesises a structured feedback report — all inside a deterministic state machine with two human interrupt points.

The system runs fully offline (deterministic mock mode) or against any of three LLM providers (Groq, Gemini, Anthropic) with automatic fallback.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (StateGraph, interrupt, MemorySaver) |
| AI Providers | Groq (llama-3.3-70b), Gemini (gemini-2.0-flash), Anthropic (claude-sonnet-4-6) |
| Offline fallback | Deterministic mock (heuristic Python, no API key required) |
| Schema validation | Pydantic v2 |
| Web backend | FastAPI |
| Speech-to-text | faster-whisper (local, optional) |
| Report delivery | SMTP + local file persistence |

---

## System Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                       Interfaces                                │
│              FastAPI (server.py)   CLI (run_cli.py)             │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                     Graph Layer                                 │
│   build.py   nodes.py   state.py   timer.py   calibrator.py    │
│   StateGraph — 16 nodes, 5 conditional routers, 2 interrupts   │
└──────────┬──────────────────────────────────────┬──────────────┘
           │                                      │
┌──────────▼──────────────┐          ┌────────────▼──────────────┐
│      Agent Layer        │          │       Tools Layer          │
│  topic_selector         │          │  retriever.py (RAG)        │
│  question_generator     │          │  emailer.py (SMTP)         │
│  intent_classifier      │          └────────────────────────────┘
│  hint_agent             │
│  evaluator              │
│  followup               │
│  report_compiler        │
└──────────┬──────────────┘
           │
┌──────────▼──────────────────────────────────────────────────────┐
│                      LLM Layer                                  │
│   llm.py (run_agent, provider dispatch, retry, JSON extract)    │
│   mock.py (deterministic offline heuristics)                    │
│   config.py (Settings from .env, provider precedence)          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Graph Architecture

The LangGraph state machine has **16 nodes**, **5 conditional routers**, and **2 interrupt points** (student turn + mentor review).

### Graph Flow

```
START
  │
  ▼
init_session ──────────────── Appends WELCOME to outbox
  │
  ▼
select_topics ─────────────── Picks 4 topics (Probability always forced)
  │
  ▼
generate_question ─────────── RAG retrieval from question_bank.json
  │
  ├──(force_end)─────────────────────────────────────► compile_report
  │
  ▼
await_message  ■ INTERRUPT ── Sends outbox + question to frontend
  │                           Resumes with student text
  ▼
timer_check ───────────────── Deterministic: warnings at 20/30 min, force_end at 40 min
  │
  ├──(end_requested / force_end)──────────────────────► compile_report
  ├──(skip_requested)─────────────────────────────────► handle_skip ──► advance
  ├──(awaiting_followup)──────────────────────────────► handle_followup_answer ──► advance
  │
  ▼
classify_intent ───────────── 5-way LLM classification
  │
  ├──(answer_attempt)──► handle_answer ──► followup_node ──(needs)──► await_message ■
  │                                                       └──(done)──► advance
  ├──(hint_request)────► handle_hint ─────────────────────────────────► await_message ■
  ├──(answer_seeking)──► handle_guard ────────────────────────────────► await_message ■
  ├──(clarification)───► handle_clarify ──────────────────────────────► await_message ■
  └──(off_topic)───────► handle_offtopic ─────────────────────────────► await_message ■
  
  advance ──(qc < target)──► generate_question
          └──(should_end)──► compile_report
                                  │
                                  ▼
                              finalize ──► END
```

### Node Catalogue

| Node | Type | Role |
|---|---|---|
| `init_session` | Deterministic | Emits welcome message |
| `select_topics` | AI + guard | Picks 4 topics; forces Probability + ≥1 LR |
| `generate_question` | RAG | Retrieves verified question from bank |
| `await_message` | **INTERRUPT** | Pauses for student input |
| `timer_check` | Deterministic | Manages session timing |
| `classify_intent` | AI | 5-way intent classification |
| `handle_answer` | AI | 4-dimension evaluation + scrubbed feedback |
| `followup_node` | AI | Generates mandatory cross-question |
| `handle_followup_answer` | AI | Assesses follow-up explanation |
| `handle_skip` | Deterministic | Records zero score, moves on |
| `handle_hint` | AI + guard | One-time conceptual nudge |
| `handle_guard` | Deterministic | Blocks answer-seeking attempts |
| `handle_clarify` | Deterministic | Re-states the current question |
| `handle_offtopic` | Deterministic | Redirects to the interview |
| `advance` | Deterministic | Increments counter, rotates topic |
| `compile_report` | AI | Synthesises session into structured report |
| `finalize` | Tool | Renders HTML, sends email |

### Routing Functions

| Router | Trigger | Destinations |
|---|---|---|
| `route_after_generate` | After every question | `await_message` or `compile_report` |
| `route_after_timer` | After timer_check | `classify_intent`, `handle_followup_answer`, `handle_skip`, `compile_report` |
| `route_intent` | After classify_intent | 5 handler nodes |
| `route_after_followup` | After followup_node | `await_message` or `advance` |
| `route_after_advance` | After advance | `generate_question` or `compile_report` |

---

## Agent Registry

| Agent | LLM Tier | Input → Output Schema |
|---|---|---|
| `topic_selector` | smart | `{n, available}` → `TopicSelection` |
| `question_rephraser` | fast | `{question}` → `RephrasedQuestion` |
| `intent_classifier` | fast | `{student_message, current_question}` → `IntentResult` |
| `hint_agent` | fast | `{question, concept_tested, correct_answer}` → `HintResult` |
| `evaluator` | smart | `{question, correct_answer, student_answer, ...}` → `Evaluation` |
| `followup` | fast | `{question, student_answer, scores}` → `FollowupDecision` |
| `followup_assessor` | smart | `{question, followup_q, student_answer, correct_answer}` → `FollowupAssessment` |
| `model_answer` | smart | `{question, correct_answer, key_concepts}` → `ModelAnswer` |
| `report_compiler` | smart | `{student_name, scores[], transcript}` → `InterviewReport` |

### LLM Provider Precedence

```
1. GROQ_API_KEY present    → Groq   (llama-3.3-70b)         fastest
2. GEMINI_API_KEY present  → Gemini (gemini-2.0-flash)
3. ANTHROPIC_API_KEY       → Claude (claude-sonnet-4-6 / haiku-4-5)
4. No key                  → Mock   (deterministic heuristics) offline
```

If a real LLM call fails (auth, quota, parse), it retries once then falls back to mock. The graph never crashes.

---

## State Schema

`InterviewState` is a flat TypedDict shared across all nodes. Every node reads from it and returns a **partial dict** — only the fields it changes.

### Field Groups

**Identity**
- `session_id`, `student_name`, `student_email`, `start_time`

**Progression**
- `question_count`, `topics_selected`, `current_topic_index`, `current_topic`
- `used_question_ids` — prevents question repeats within a session

**Current Question (internal — never shown during interview)**
- `current_question`, `current_subject`, `correct_answer`
- `concept_tested`, `key_concepts`, `current_source_id`

**Per-turn working values**
- `pending_message` — last student input
- `last_intent` — classification result
- `awaiting_followup`, `pending_followup_q`
- `_last_eval` — transient evaluation cache between handle_answer and followup_node

**Timing and control flags**
- `elapsed_minutes`, `timer_status`
- `warning_sent`, `wrap_up_flag`, `force_end`
- `end_requested`, `skip_requested`, `status`

**Accumulating collections**
- `scores: list[dict]` — one entry per question answered
- `transcript: list[dict]` — full conversation `{role, text}`
- `outbox: list[str]` — bot messages queued for the frontend (flushed each turn)

**Report**
- `report: dict`, `report_html: str`, `delivery: dict`

---

## Tools

### RAG Retriever (`tools/retriever.py`)

```
retrieve(topic, difficulty, exclude_ids)
  1. Load question_bank.json (lru_cache — loaded once)
  2. Exact match: topic + difficulty
  3. Fallback 1: same topic, any difficulty
  4. Fallback 2: any unused question in the bank
  5. Random choice from pool
  → Returns question dict or raises NoQuestionsLeft
```

Questions are **never hallucinated** — always retrieved from the verified bank. `used_question_ids` in state prevents repeats.

### Emailer (`tools/emailer.py`)

```
send_email(to, subject, html_body, tag)
  - Always writes to ./reports/{tag}.html (audit trail)
  - If SMTP configured → sends via smtplib.SMTP
  - If not configured → simulates (log + file only)
  → Returns {delivered, simulated, path, to}
```

---

## Guardrails

| Guardrail | Mechanism | Where |
|---|---|---|
| Answer-seeking block | Fixed denial message, no score recorded | `handle_guard` |
| Hint-once | `hint_used` dict in state, keyed by question | `handle_hint` |
| Answer-leak scrub | Regex replace on evaluation feedback before storage | `evaluator._scrub()` |
| Topic invariants | Probability forced, ≥1 LR forced in select_topics | `nodes.select_topics` |
| No hallucinated questions | RAG-only retrieval, `NoQuestionsLeft` → force_end | `retriever.retrieve` |
| Time hard-stop | force_end=True at 40 min | `timer.evaluate_timer` |
| LLM failure | Retry once → fallback to deterministic mock | `llm.run_agent` |

---

## Entry Points

### FastAPI (`server.py`)

| Endpoint | Role |
|---|---|
| `POST /api/start` | Create session, invoke graph, return first interrupt |
| `POST /api/answer` | Resume graph with student text |
| `POST /api/end` | Resume with `__END__` sentinel → wrap up |
| `POST /api/skip` | Resume with `__SKIP__` sentinel → skip current question |
| `GET /api/topics` | Return topic list grouped by subject |
| `POST /api/stt` | Transcribe audio via faster-whisper |
| `GET /report/{sid}` | Return rendered HTML report |
| `GET /` | Serve frontend SPA |

### CLI (`run_cli.py`)

Uses identical `build_graph()`, `start()`, `resume()` — reads input from stdin. Pass `--auto` for scripted runs with pre-filled answers.

---

## Data Flow: Single Answer Turn

```
Student submits text
        │
        ▼
await_message (interrupt resumes)
  pending_message = student_text
  transcript += [{role: "student", text}]
        │
        ▼
timer_check
  elapsed_minutes updated
  warning/wrap_up/force_end flags set if thresholds crossed
        │
        ▼
classify_intent
  intent_classifier agent → last_intent = "answer_attempt"
        │
        ▼
handle_answer
  evaluator agent → {correctness, concept_clarity, explanation_quality, basics_strength, feedback}
  feedback scrubbed of correct_answer
  scores[] += new entry
  transcript += [{role: "interviewer", text: feedback}]
  outbox += [feedback]
        │
        ▼
followup_node
  followup agent → cross-question generated
  awaiting_followup = True
  outbox += ["Follow-up: ..."]
        │
        ▼
await_message ■ (interrupt) — sends follow-up to student
        │
  (student answers follow-up)
        │
        ▼
handle_followup_answer
  followup.assess() → satisfied/feedback (scrubbed)
  awaiting_followup = False
        │
        ▼
advance
  question_count++
  topic rotated
        │
        ▼
generate_question (next question) — or compile_report if session ending
```

---

## Configuration Reference

Set via `.env` or environment variables.

```
# LLM providers (precedence order)
GROQ_API_KEY, GROQ_MODEL
GEMINI_API_KEY, GEMINI_MODEL
ANTHROPIC_API_KEY, MODEL_SMART, MODEL_FAST

# Which agents use real LLM (comma-separated)
LLM_AGENTS=evaluator,report_compiler,intent_classifier,...

# Interview timing (minutes)
WARN_MINUTES=20
WRAPUP_MINUTES=30
HARD_STOP_MINUTES=40
QUESTIONS_TARGET=2
HARD_CAP_QUESTIONS=7

# Email
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
MENTOR_EMAIL

# Voice (optional)
ENABLE_VOICE=false
WHISPER_MODEL=tiny
```
