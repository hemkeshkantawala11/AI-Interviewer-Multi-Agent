"""Central configuration. Reads from environment / .env, with safe defaults.

If ANTHROPIC_API_KEY is empty the whole system runs in deterministic MOCK mode,
so it is fully runnable and testable offline (and for the demo without spend).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # python-dotenv optional
    pass

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "src" / "data"
REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    model_smart: str = os.getenv("MODEL_SMART", "claude-sonnet-4-6")
    model_fast: str = os.getenv("MODEL_FAST", "claude-haiku-4-5-20251001")

    gemini_api_key: str = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    groq_api_key: str = os.getenv("GROQ_API_KEY", "").strip()
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Only these agents call the real LLM; all others stay deterministic/keyword-based.
    # Keeps the high-frequency intent classifier fast & quota-cheap.
    llm_agents: frozenset = frozenset(
        a for a in (os.getenv("LLM_AGENTS")
                    or "evaluator,hint_agent,followup,followup_assessor,model_answer,report_compiler,topic_selector")
        .replace(" ", "").split(",") if a
    )

    questions_target: int = _i("QUESTIONS_TARGET", 2)
    hard_cap_questions: int = _i("HARD_CAP_QUESTIONS", 7)
    warn_minutes: int = _i("WARN_MINUTES", 20)
    wrapup_minutes: int = _i("WRAPUP_MINUTES", 30)
    hard_stop_minutes: int = _i("HARD_STOP_MINUTES", 40)

    enable_voice: bool = _b("ENABLE_VOICE", False)

    smtp_host: str = os.getenv("SMTP_HOST", "").strip()
    smtp_port: int = _i("SMTP_PORT", 587)
    smtp_user: str = os.getenv("SMTP_USER", "").strip()
    smtp_password: str = os.getenv("SMTP_PASSWORD", "").strip()
    mentor_email: str = os.getenv("MENTOR_EMAIL", "mentor@example.com").strip()

    question_bank_path: Path = DATA_DIR / "question_bank.json"

    @property
    def provider(self) -> str:
        """Which LLM backend to use. Precedence: Groq > Gemini > Anthropic > mock."""
        if self.groq_api_key:
            return "groq"
        if self.gemini_api_key:
            return "gemini"
        if self.anthropic_api_key:
            return "anthropic"
        return "mock"

    @property
    def use_mock_llm(self) -> bool:
        return self.provider == "mock"


settings = Settings()
