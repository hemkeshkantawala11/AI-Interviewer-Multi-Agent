"""Difficulty Calibrator — DETERMINISTIC (no LLM).

Pure threshold logic, kept out of the LLM on purpose: it must be predictable
and auditable.
    overall >= 8   -> hard
    5 <= overall <= 7 -> medium
    overall < 5    -> easy
"""
from __future__ import annotations


def next_difficulty(overall_score: float) -> str:
    if overall_score >= 8:
        return "hard"
    if overall_score >= 5:
        return "medium"
    return "easy"
