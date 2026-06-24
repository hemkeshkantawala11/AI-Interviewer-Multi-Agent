"""LLM access layer.

`run_agent` formats a prompt, calls Claude (if a key is configured) or the
deterministic mock, extracts JSON, validates it against the agent's Pydantic
schema, and returns a validated model instance. A logger records every call so
the run is observable even without LangSmith.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from . import mock, prompts
from .config import settings

log = logging.getLogger("interviewer.llm")

T = TypeVar("T", bound=BaseModel)

# agent name -> (prompt template, mock key, model tier)
_REGISTRY = {
    "topic_selector": (prompts.TOPIC_SELECTOR, "topic_selector", "smart"),
    "question_rephraser": (prompts.QUESTION_REPHRASER, "question_rephraser", "fast"),
    "intent_classifier": (prompts.INTENT_CLASSIFIER, "intent_classifier", "fast"),
    "hint_agent": (prompts.HINT_AGENT, "hint_agent", "fast"),
    "evaluator": (prompts.EVALUATOR, "evaluator", "smart"),
    "followup": (prompts.FOLLOWUP, "followup", "fast"),
    "followup_assessor": (prompts.FOLLOWUP_ASSESS, "followup_assessor", "smart"),
    "model_answer": (prompts.MODEL_ANSWER, "model_answer", "smart"),
    "report_compiler": (prompts.REPORT_COMPILER, "report_compiler", "smart"),
}


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fall back to brace matching
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in response: {text[:200]!r}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("Unbalanced JSON in model response")


_SYSTEM = ("You are a precise component in a multi-agent interview system. "
           "Respond with ONLY the requested JSON object — no prose, no markdown fences.")


def _call_anthropic(prompt: str, model: str) -> str:
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=1500,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")


def _call_groq(prompt: str, model: str) -> str:
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},  # forces clean JSON output
        max_tokens=1500,
        temperature=0.4,
    )
    return resp.choices[0].message.content or ""


def _call_gemini(prompt: str, model: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM,
            response_mime_type="application/json",  # forces clean JSON output
            max_output_tokens=1500,
            temperature=0.4,
        ),
    )
    return resp.text or ""


def run_agent(agent: str, variables: dict, schema: Type[T]) -> T:
    if agent not in _REGISTRY:
        raise KeyError(f"Unknown agent '{agent}'")
    template, mock_key, tier = _REGISTRY[agent]

    # Use the real LLM only when a provider is configured AND this agent is opted in.
    use_real = (not settings.use_mock_llm) and (agent in settings.llm_agents)
    if not use_real:
        raw = mock.DISPATCH[mock_key](variables)
        log.info("[MOCK %s] -> %s", agent, raw)
        return schema.model_validate(raw)

    if settings.provider == "groq":
        model, caller = settings.groq_model, _call_groq
    elif settings.provider == "gemini":
        model, caller = settings.gemini_model, _call_gemini
    else:
        model = settings.model_smart if tier == "smart" else settings.model_fast
        caller = _call_anthropic

    prompt = template.format(**variables)
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            text = caller(prompt, model)
            data = _extract_json(text)
            obj = schema.model_validate(data)
            log.info("[%s/%s] ok", agent, model)
            return obj
        except Exception as e:  # parse/validate AND api errors (quota 429, network, auth)
            last_err = e
            log.warning("[%s] LLM call failed (attempt %d): %s", agent, attempt + 1, e)
    # graceful degradation: fall back to the deterministic mock so the graph never crashes
    log.error("[%s] falling back to mock after errors: %s", agent, last_err)
    return schema.model_validate(mock.DISPATCH[mock_key](variables))
