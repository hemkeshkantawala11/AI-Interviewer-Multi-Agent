# Deployment Guide

The app is a single FastAPI service (`server.py`) that serves the web UI and the
interview API. Speech-to-text (faster-whisper) runs **on the server**, so students
need nothing installed — just a browser with a mic and camera.

## ⚠️ The one hard requirement: HTTPS

Browsers only allow **camera + microphone** (`getUserMedia`) on a **secure context**:
`https://` or `localhost`. So on any real device (phone/another laptop) the app
**must be served over HTTPS**, or the camera/mic simply won't turn on.

All the platforms below give you HTTPS automatically.

---

## Option A — Render / Railway (easiest, free tier)

1. Push this folder to a GitHub repo.
2. Create a new **Web Service** from the repo.
3. Settings:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - **Environment variables:** `GROQ_API_KEY=...`, `WHISPER_MODEL=base.en`
   - Instance with **≥ 1 GB RAM** (faster-whisper needs it).
4. Deploy → you get an `https://...` URL that works on any device.

A `Procfile` is included, so Railway/Render auto-detect the start command.

## Option B — Docker (any host: Fly.io, a VM, Cloud Run)

```bash
docker build -t ai-interviewer .
docker run -p 8000:8000 -e GROQ_API_KEY=your_key ai-interviewer
```

The image pre-downloads the Whisper model at build time, so the first request is fast.
Put it behind a reverse proxy (Caddy/Nginx) or a platform that terminates TLS to get HTTPS.
On Fly.io / Cloud Run, HTTPS is automatic.

## Option C — Quick public test from your laptop

Run locally and tunnel with HTTPS (no deploy needed):

```bash
py server.py                 # runs on http://localhost:8000
# in another terminal:
ngrok http 8000              # gives you an https URL anyone can open
```

---

## Environment variables

| Var | Purpose | Default |
|---|---|---|
| `GROQ_API_KEY` | LLM provider (free tier) | — (falls back to deterministic mock) |
| `GROQ_MODEL` | model id | `llama-3.3-70b-versatile` |
| `WHISPER_MODEL` | STT model: `tiny.en`/`base.en`/`small.en` | `base.en` |
| `QUESTIONS_TARGET` | number of main questions | `2` |
| `PORT` | server port | `8000` |

Provider precedence is **Groq → Gemini → Anthropic → mock**; set the matching key.

---

## Voice across devices — how it works

- **STT (student → text):** the browser records audio and uploads it to `/api/stt`;
  the server transcribes with faster-whisper. **No per-device install** — works on any
  device that can reach the server.
- **TTS (AI → voice):** uses the browser's built-in speech synthesis — also no install.
- **Camera + mic:** standard browser APIs; only needs **HTTPS** (see above).

### Resource note
faster-whisper `base.en` uses ~1 GB RAM. On tiny/free instances, set
`WHISPER_MODEL=tiny.en` to cut memory/CPU. For heavy concurrent use, run STT on a
GPU host or swap in a hosted STT API behind the same `/api/stt` endpoint.
