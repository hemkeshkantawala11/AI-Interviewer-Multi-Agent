"""Deterministic mock 'LLM' used when no API key is set.

Each function takes the prompt variables and returns a dict that validates
against the corresponding schema. The heuristics are good enough to:
  * drive a believable end-to-end demo offline, and
  * make the evaluation test-suite deterministic (guardrail routing, scoring).
"""
from __future__ import annotations

import re


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9/]", "", (s or "").lower())


def topic_selector(v: dict) -> dict:
    available = v.get("available_list") or []
    chosen = ["Probability"]
    # ensure one LR topic
    lr = next((t for t in available if "Logical Reasoning" in t), None)
    if lr:
        chosen.append(lr)
    for t in available:
        if t not in chosen:
            chosen.append(t)
        if len(chosen) >= int(v.get("n", 4)):
            break
    return {"topics": chosen[: int(v.get("n", 4))],
            "rationale": "Probability prioritised (high weightage); LR included for reasoning coverage."}


def question_rephraser(v: dict) -> dict:
    # Mock keeps the verified question verbatim (safest — no math drift).
    return {"question": v.get("question", "")}


def intent_classifier(v: dict) -> dict:
    t = (v.get("student_message") or "").lower().strip()
    answer_seeking = ["what is the answer", "what's the answer", "tell me the answer",
                      "just tell me", "give me the answer", "solve it", "solve this",
                      "show me how", "walk me through", "what approach", "what method",
                      "give me the method", "the solution", "how do i solve", "do it for me"]
    hint = ["hint", "stuck", "no idea", "don't know", "dont know", "where do i start",
            "how to start", "i'm lost", "im lost", "give me a clue", "nudge"]
    clarify = ["repeat", "rephrase", "what do you mean", "didn't understand",
               "didnt understand", "say again", "can you clarify", "i don't get the question",
               "what does the question", "come again"]
    off = ["weather", "who are you", "your name", "bored", "movie", "cricket", "lunch",
           "how are you", "hello", "hi there", "joke"]

    if any(k in t for k in answer_seeking):
        return {"intent": "answer_seeking", "confidence": 0.9, "reasoning": "asking for solution/method"}
    if any(k in t for k in hint):
        return {"intent": "hint_request", "confidence": 0.85, "reasoning": "asking for a hint / stuck"}
    if any(k in t for k in clarify):
        return {"intent": "clarification", "confidence": 0.8, "reasoning": "wants question clarified"}
    if t and any(k in t for k in off) and not any(c.isdigit() for c in t):
        return {"intent": "off_topic", "confidence": 0.7, "reasoning": "unrelated to the question"}
    return {"intent": "answer_attempt", "confidence": 0.75, "reasoning": "looks like a genuine attempt"}


def hint_agent(v: dict) -> dict:
    concept = v.get("concept_tested", "the key idea")
    return {"hint": f"Focus on which fundamental idea behind '{concept}' the setup is "
                    f"pointing you toward, rather than jumping to a formula."}


def evaluator(v: dict) -> dict:
    correct = _norm(v.get("correct_answer", ""))
    ans = _norm(v.get("student_answer", ""))
    key_concepts = v.get("key_concepts_list") or v.get("key_concepts") or []
    if isinstance(key_concepts, str):  # tolerate a comma-joined string
        key_concepts = [c.strip() for c in key_concepts.split(",") if c.strip()]
    matched_concepts = sum(1 for c in key_concepts if _norm(c)[:6] and _norm(c)[:6] in ans)
    has_reasoning = len((v.get("student_answer") or "").split()) >= 8

    if correct and (correct in ans or ans in correct) and ans:
        correctness = 9
    elif correct and any(tok and tok in ans for tok in re.split(r"[/:]", correct) if len(tok) >= 1) and ans:
        correctness = 6
    elif ans:
        correctness = 3
    else:
        correctness = 0

    concept_clarity = min(10, 3 + matched_concepts * 2 + (2 if has_reasoning else 0))
    explanation_quality = 7 if has_reasoning else 4
    basics_strength = max(correctness, concept_clarity)
    overall = round((correctness + concept_clarity + explanation_quality + basics_strength) / 4, 1)

    if correctness >= 8:
        fb = "Solid work — your final result is on target and your reasoning comes through clearly."
    elif correctness >= 5:
        fb = "You're partly there. The direction is reasonable, but tighten the logic and re-check the key step."
    else:
        fb = "Good effort. Revisit the core concept for this topic and try again — think about what the question is really asking."
    return {"correctness": correctness, "concept_clarity": concept_clarity,
            "explanation_quality": explanation_quality, "basics_strength": basics_strength,
            "overall_score": overall, "student_feedback": fb,
            "internal_notes": f"matched_concepts={matched_concepts}; reasoning={has_reasoning}; "
                              f"answer_present={bool(ans)}."}


def followup(v: dict) -> dict:
    cc = int(v.get("concept_clarity", 5))
    corr = int(v.get("correctness", 5))
    if corr >= 7 and cc >= 7:
        # strong answer -> push deeper / related concept / different approach
        return {"needs_followup": True,
                "followup_question": "Nicely done. Now push further — can you reach the same result using a "
                                     "different approach, or explain how your answer would change if one "
                                     "condition in the problem were altered?",
                "followup_reason": "extend a strong answer with a related challenge"}
    return {"needs_followup": True,
            "followup_question": "Let's dig into your reasoning — walk me through why your first step is valid.",
            "followup_reason": "probe the gap in a weak/unclear answer"}


def model_answer(v: dict) -> dict:
    ca = v.get("correct_answer", "")
    kc = v.get("key_concepts_list") or v.get("key_concepts") or []
    if isinstance(kc, str):
        kc = [c.strip() for c in kc.split(",") if c.strip()]
    approach = ", ".join(kc)
    if approach:
        return {"answer": f"The key idea here is to use {approach}. Applying that to the problem and "
                          f"working through the steps carefully leads to the final answer: {ca}."}
    return {"answer": f"Working through the problem step by step gives the final answer: {ca}."}


def followup_assessor(v: dict) -> dict:
    ans = (v.get("student_answer") or "").strip()
    correct = _norm(v.get("correct_answer", ""))
    words = len(ans.split())
    clear = words >= 8 or (correct and correct in _norm(ans))
    if clear:
        return {"satisfied": True,
                "feedback": "Thanks — that explanation is clear and on the right track. Let's move on to the next question."}
    return {"satisfied": False,
            "feedback": "I'm not fully convinced by that explanation — it's a bit unclear, so revisit the idea later. Let's continue for now."}


def _label(score: float) -> str:
    return "Strong" if score >= 7.5 else ("Developing" if score >= 5 else "Weak")


def report_compiler(v: dict) -> dict:
    scores = v.get("scores_array_obj") or v.get("scores_array") or []
    if isinstance(scores, str):
        import json as _json
        try:
            scores = _json.loads(scores)
        except Exception:
            scores = []
    name = v.get("student_name", "Student")
    per_q, by_topic = [], {}
    for s in scores:
        ov = float(s.get("overall_score", 0))
        per_q.append({
            "question_number": s.get("question_number"),
            "topic": s.get("topic", ""),
            "correctness_label": ("Correct" if s.get("correctness", 0) >= 8
                                  else "Partially Correct" if s.get("correctness", 0) >= 5 else "Incorrect"),
            "what_went_well": "Engaged with the problem and offered reasoning."
                              if ov >= 5 else "Attempted the problem.",
            "what_to_improve": "Sharpen accuracy and justify each step."
                               if ov < 8 else "Keep up the rigour; try harder variants.",
            "scores": {"correctness": s.get("correctness", 0), "concept_clarity": s.get("concept_clarity", 0),
                       "explanation": s.get("explanation_quality", 0), "basics": s.get("basics_strength", 0)},
        })
        by_topic.setdefault(s.get("topic", "General"), []).append(ov)

    concept_map = {t: _label(sum(xs) / len(xs)) for t, xs in by_topic.items()}
    overall = round(sum(float(s.get("overall_score", 0)) for s in scores) / len(scores), 1) if scores else 0.0
    readiness = int(min(100, overall * 10))
    weak = [t for t, xs in by_topic.items() if (sum(xs) / len(xs)) < 6]

    return {
        "student_name": name,
        "overall_score": overall,
        "readiness_score": readiness,
        "summary": f"{name} attempted {len(scores)} question(s) with an average score of "
                   f"{overall}/10. Performance was strongest where fundamentals were applied "
                   f"consistently; reasoning clarity is the main growth area.",
        "per_question": per_q,
        "concept_map": concept_map,
        "communication_assessment": "Explanations were understandable; aim to state each step "
                                    "explicitly and justify why it follows.",
        "top_improvements": [
            "State your reasoning step-by-step before computing the final value.",
            "Double-check the core concept for weaker topics before attempting.",
            "Practise timed problems to build speed with accuracy.",
        ],
        "revision_topics": weak or ["Keep practising across all covered topics"],
        "encouragement_note": f"Great effort, {name}! Consistent practice on the flagged topics "
                              f"will move your readiness up quickly. Keep going.",
    }


DISPATCH = {
    "topic_selector": topic_selector,
    "question_rephraser": question_rephraser,
    "intent_classifier": intent_classifier,
    "hint_agent": hint_agent,
    "evaluator": evaluator,
    "followup": followup,
    "followup_assessor": followup_assessor,
    "model_answer": model_answer,
    "report_compiler": report_compiler,
}
