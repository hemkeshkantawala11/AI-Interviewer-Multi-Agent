"""Timer logic — DETERMINISTIC. Runs on every student message before anything else.

Returns the timer status plus any one-time messages and flags to merge into state.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..config import settings


def evaluate_timer(start_time_iso: str, warning_sent: bool, wrap_up_flag: bool) -> dict:
    start = datetime.fromisoformat(start_time_iso)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    elapsed_min = (datetime.now(timezone.utc) - start).total_seconds() / 60.0

    out: dict = {"elapsed_minutes": round(elapsed_min, 2), "messages": [],
                 "force_end": False, "warning_sent": warning_sent, "wrap_up_flag": wrap_up_flag,
                 "timer_status": "normal"}

    if elapsed_min >= settings.hard_stop_minutes:
        out["force_end"] = True
        out["timer_status"] = "hard_stop"
        out["messages"].append("We've reached the time limit, so I'll wrap up and prepare your report now.")
    elif elapsed_min >= settings.wrapup_minutes:
        out["timer_status"] = "wrap_up"
        if not wrap_up_flag:
            out["wrap_up_flag"] = True
            out["messages"].append("We're near the end of our time — this will be one of the last questions.")
    elif elapsed_min >= settings.warn_minutes:
        out["timer_status"] = "warning"
        if not warning_sent:
            out["warning_sent"] = True
            out["messages"].append("Heads up: about 10 minutes left. Keep your answers focused.")
    return out
