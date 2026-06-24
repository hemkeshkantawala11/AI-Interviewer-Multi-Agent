"""FastAPI backend for the Google-Meet-style AI interviewer.

Serves the single-page frontend and drives the LangGraph interview:
  POST /api/start    -> create session, return greeting + first question
  POST /api/answer   -> submit a (possibly edited) answer, get the AI's next turn
  POST /api/mentor   -> human-in-the-loop approval at the end
  POST /api/stt      -> transcribe an audio blob with faster-whisper (local, free)
  GET  /report/{sid} -> the final HTML feedback report

Run:  py -m uvicorn server:app --reload --port 8000   (or:  py server.py)
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from interviewer.graph.build import build_graph, resume, start
from interviewer.graph.state import new_session
from interviewer.report.html_report import render_report_html

app = FastAPI(title="AI Mock Interviewer")
GRAPH = build_graph()
STATIC = os.path.join(os.path.dirname(__file__), "static")


@app.exception_handler(Exception)
async def _json_errors(request, exc):  # always return JSON so the UI can recover
    import traceback

    traceback.print_exc()
    return JSONResponse({"error": str(exc)[:300], "type": type(exc).__name__}, status_code=500)

# ---- faster-whisper (STT), loaded once and warmed at startup ----
_WHISPER = None
_WLOCK = threading.Lock()


def _whisper():
    global _WHISPER
    if _WHISPER is None:
        with _WLOCK:  # only one thread loads the model
            if _WHISPER is None:
                from faster_whisper import WhisperModel

                _WHISPER = WhisperModel(
                    os.getenv("WHISPER_MODEL", "base.en"),
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=os.cpu_count() or 4,
                )
    return _WHISPER


@app.on_event("startup")
def _warm_whisper():
    """Load + warm the STT model in the background so the first real
    transcription is instant (the heavy cost is load, not transcribe)."""

    def go():
        try:
            import numpy as np

            m = _whisper()
            list(m.transcribe(np.zeros(16000, dtype="float32"), language="en", beam_size=1)[0])
            print(f"[stt] whisper ready: {os.getenv('WHISPER_MODEL', 'base.en')}")
        except Exception as e:
            print(f"[stt] warmup failed: {e}")

    threading.Thread(target=go, daemon=True).start()


def _cfg(sid: str) -> dict:
    return {"configurable": {"thread_id": sid}}


def _pack(step: dict) -> dict:
    payload, state = step["payload"], step["state"]
    ptype = payload.get("type")
    return {
        "session_id": state.get("session_id"),
        "type": ptype,
        "done": step["done"],
        "messages": payload.get("outbox", []),
        "question_number": payload.get("question_number"),
        "status": state.get("status"),
        "report": state.get("report") if (ptype == "mentor_review" or step["done"]) else None,
    }


# --------------------------------- API --------------------------------- #
class StartReq(BaseModel):
    name: str = "Student"
    email: str = "student@example.com"
    topics: list[str] = []  # optional — student-selected topics; empty = auto-select


class AnswerReq(BaseModel):
    session_id: str
    text: str


@app.get("/api/topics")
def api_topics():
    from interviewer.tools import retriever

    return {"by_subject": retriever.topics_by_subject(), "all": retriever.all_topics()}


@app.post("/api/start")
def api_start(body: StartReq):
    sid = "s_" + uuid.uuid4().hex[:12]
    init = new_session(body.name, body.email, sid, requested_topics=body.topics)
    step = start(GRAPH, init, _cfg(sid))
    return _pack(step)


@app.post("/api/answer")
def api_answer(body: AnswerReq):
    text = (body.text or "").strip()
    if not text:
        return JSONResponse({"error": "empty answer"}, status_code=400)
    step = resume(GRAPH, text, _cfg(body.session_id))
    return _pack(step)


class EndReq(BaseModel):
    session_id: str


@app.post("/api/end")
def api_end(body: EndReq):
    # Student ended early: flag the state and resume — the graph wraps up and
    # builds a scorecard from whatever has been answered so far.
    GRAPH.update_state(_cfg(body.session_id), {"end_requested": True})
    step = resume(GRAPH, "__END__", _cfg(body.session_id))
    return _pack(step)


@app.post("/api/skip")
def api_skip(body: EndReq):
    # Inactivity: skip the current question (short feedback) and move to the next.
    GRAPH.update_state(_cfg(body.session_id), {"skip_requested": True})
    step = resume(GRAPH, "__SKIP__", _cfg(body.session_id))
    return _pack(step)


# Whisper's common hallucinations on silence/near-silence — never surface these.
_JUNK = {"", ".", "you", "you.", "thank you", "thank you.", "thanks for watching",
         "thanks for watching.", "bye", "bye.", "i'm sorry.", "thank you very much."}


def _decode_to_16k_mono(path: str):
    """Decode any audio container to a 16 kHz mono float32 array via PyAV."""
    import av
    import numpy as np

    container = av.open(path)
    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    parts = []
    for frame in container.decode(audio=0):
        for rf in resampler.resample(frame):
            parts.append(rf.to_ndarray().flatten().astype("float32"))
    container.close()
    return np.concatenate(parts) if parts else np.zeros(0, dtype="float32")


@app.post("/api/stt")
async def api_stt(audio: UploadFile = File(...)):
    data = await audio.read()
    suffix = os.path.splitext(audio.filename or "a.webm")[1] or ".webm"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp.close()

    # Best-effort energy reading — for diagnostics/messaging ONLY (never blocks STT).
    rms, dur = None, 0.0
    try:
        import numpy as np

        arr = _decode_to_16k_mono(tmp.name)
        dur = len(arr) / 16000.0
        rms = round(float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0.0, 4)
    except Exception as e:
        print(f"[stt] rms decode skipped: {e}")

    try:
        # Transcribe straight from the file — whisper's own decoder is battle-tested
        # across webm/opus/ogg/mp4 from any browser.
        segments, _ = _whisper().transcribe(
            tmp.name, language="en", beam_size=1, condition_on_previous_text=False
        )
        text = " ".join(s.text for s in segments).strip()
        if text.lower() in _JUNK:  # drop silence hallucinations ("you", "thank you", …)
            text = ""
        reason = "" if text else ("no_speech" if (rms is not None and rms < 0.004) else "unclear")
        print(f"[stt] bytes={len(data)} dur={dur:.2f}s rms={rms} -> {text!r}")
        return {"text": text, "reason": reason, "duration": round(dur, 2), "rms": rms,
                "bytes": len(data)}
    except Exception as e:  # never break the UI — let the user type instead
        return JSONResponse({"text": "", "reason": "error", "error": str(e)[:200]}, status_code=200)
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


@app.get("/favicon.ico")
def favicon():
    from fastapi import Response

    return Response(status_code=204)  # silence the browser's favicon 404


@app.get("/report/{sid}", response_class=HTMLResponse)
def report(sid: str):
    state = GRAPH.get_state(_cfg(sid)).values
    html = state.get("report_html")
    if not html and state.get("report"):
        html = render_report_html(state["report"], for_mentor=False)
    if not html:
        return HTMLResponse("<h2>Report not ready yet.</h2>", status_code=404)
    return HTMLResponse(html)


# --------------------------------- frontend --------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
