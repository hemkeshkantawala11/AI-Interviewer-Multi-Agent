# AI Mock Interviewer — Maths & Logical Reasoning

A multi-agent, adaptive mock-interview system built on **LangGraph**. It conducts a
voice-or-text interview over JEE-style Maths and Logical Reasoning (LR) questions,
adapts difficulty in real time, refuses to leak answers, and produces a structured
feedback report that a **human mentor must approve** before it reaches the student.

Built to satisfy the *Multi-Agent Orchestration* capstone rubric **and** serve as the
first production slice of the interviewer startup.

---

## Why multi-agent (not a chatbot)

Each turn flows through specialised agents with strict, schema-validated hand-offs and
deterministic routing/guardrails between them. No single prompt could safely do
classification + grounded question generation + scoring + guarded refusal + reporting
with auditable control flow.

## Agents & responsibilities

| Agent | Type | Responsibility |
|---|---|---|
| Topic Selector | AI | Picks session topics (always Probability + ≥1 LR) |
| Question Generator | AI + **RAG** | Retrieves a *verified* question from the curated bank |
| Intent Classifier | AI | Labels every message into 5 intents |
| Hint Agent | AI | One conceptual nudge — never the method |
| Answer Evaluator | AI | Scores 4 dimensions; feedback scrubbed of the answer |
| Follow-up Decision | AI | Decides whether to probe understanding |
| Report Compiler | AI | Synthesises the session into a structured report |
| Difficulty Calibrator | **Deterministic** | Score → next difficulty |
| Timer | **Deterministic** | Warning / wrap-up / hard-stop gates |

## Architecture

`src/interviewer/`
- `graph/` — LangGraph **state, nodes, routing, and the compiled graph** (`build.py`)
- `agents/` — one module per agent (single responsibility)
- `tools/` — `retriever.py` (RAG over the question bank) + `emailer.py` (delivery)
- `report/` — structured report → styled HTML
- `voice/` — thin STT/TTS adapter over the text core
- `llm.py` / `mock.py` — Claude calls with a deterministic offline fallback
- `schemas.py` — Pydantic contracts for every hand-off

Two human/LLM `interrupt()` points: the **student turn** and the **mentor approval**.

```
START → init → select_topics → generate_question(RAG) → await_message ■
      → timer → classify_intent → [router] → answer/hint/guard/clarify/off_topic
      → evaluate → calibrate → follow-up → … loop … → compile_report
      → mentor_review ■ → (approve→email student | hold→stop) → END
```

## Rubric coverage

| Requirement | Where |
|---|---|
| 3+ meaningful agents | 7 AI agents + 2 deterministic components |
| LangGraph state/nodes/edges/routing | `graph/build.py`, `graph/nodes.py` |
| State management | `graph/state.py` shared `InterviewState` |
| 2+ tools | RAG retriever, emailer, checkpointer persistence |
| Structured outputs | `schemas.py` Pydantic on every hand-off |
| Routing/branching | intent → 5-way conditional edge |
| RAG / grounding | `tools/retriever.py` over `data/question_bank.json` |
| Evaluation (5+) | `tests/test_interview.py` (9 cases) |
| Observability | per-call logging in `llm.py`; LangSmith env hooks |
| Guardrails | answer-seeking guard, hint-once, answer-leak scrub, topic invariants |
| Human-in-the-loop | mentor `interrupt()` before student delivery |
| Demo-ready | Meet-style web room (`server.py`) + CLI, runs offline in mock mode |

## Quick start

```bash
py -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

cp .env.example .env              # add GROQ_API_KEY for real LLM (free tier)
                                  # (works fully offline in MOCK mode without any key)

# Run the test/evaluation suite
pytest -q

# 🎥 Web UI — Google-Meet-style interview room (recommended demo)
py server.py                      # then open http://127.0.0.1:8000
#   or: py -m uvicorn server:app --port 8000

# CLI alternatives
py run_cli.py --auto              # self-running scripted demo (no typing)
py run_cli.py                     # interactive typing
```

**Deployment:** see [DEPLOY.md](DEPLOY.md) (Render/Railway/Docker; HTTPS required for camera+mic).

### The interview room (`server.py` → http://127.0.0.1:8000)
A two-tile video-call UI: **AI interviewer on the left** (speaks via the browser's
text-to-speech, with a speaking animation), **your webcam on the right**. Camera +
mic must be enabled to join. In the lobby the student can **pick topics to practise**
(or leave blank for an auto-selected mix). On joining you're greeted by voice, then:
1. Answer by **🎤 voice** (transcribed by faster-whisper on the server) **or by typing** —
   the transcript drops into an editable box so you can fix it before sending.
2. The AI evaluates, gives feedback, and **branches**: a clear, correct, well-explained
   answer moves on; an unclear/incorrect one triggers a **follow-up question**.
3. After 2 questions it ends, a **mentor approves** the report (human-in-the-loop),
   and you're redirected to your **HTML feedback report**.
4. Transcript panel can be hidden/shown.

### LLM modes (provider precedence: Groq → Gemini → Anthropic → mock)
- **No API key** → deterministic **MOCK mode**: fully runnable, tests pass, great for demos.
- **`GROQ_API_KEY` set** → uses Groq (`llama-3.3-70b-versatile`) for the reasoning agents
  listed in `LLM_AGENTS`. If a model reply fails to parse/quota errors, the system degrades
  gracefully to the deterministic mock so the graph never crashes.

### Voice
- **STT**: faster-whisper (local, free) via the `/api/stt` endpoint.
- **TTS**: the browser's built-in speech synthesis (free, no key).
- Interview length is **flexible** (short 5–10 min is fine); the only floor is 2 questions.

## Configuration
See `.env.example` — question count, timer thresholds, voice toggle, SMTP, models.

## Design choices worth noting
- **RAG grounds questions in a vetted bank** rather than free generation → no invalid maths, no repeats.
- **Voice is a thin adapter**; the text core is always demo-able (flip `ENABLE_VOICE`).
- **Determinism where it matters**: timing, counting, difficulty thresholds, and guard
  responses are plain code — predictable and auditable — while reasoning stays with the LLM.

## Limitations / future work
- Curated bank is small (extend with verified PYQs + difficulty tags).
- Follow-up answers are recorded but not separately re-scored in this build.
- No student auth / cross-session history yet.
- Voice is turn-based (record → transcribe → send); production target is streaming.
- Browser STT/TTS: best in Chrome/Edge; mic + camera permission required to join.
