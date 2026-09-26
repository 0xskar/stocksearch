"""Orchestrator: synthesizes the 3 subagents' summaries into two verdicts.

The subagents themselves (agents/fundamentals.py, agents/sentiment.py,
agents/demand.py) are dispatched sequentially by research.py - one local
GPU means concurrent dispatch would just contend for the same resource, so
there's no orchestration-level fan-out to do here. This module's job is
purely the final synthesis step: given the 3 written summaries, produce a
short_term and long_term verdict, using Ollama's JSON-Schema-constrained
`format` output for reliability, with one retry on invalid output.
"""

from __future__ import annotations

import json
import logging

import ollama_client

logger = logging.getLogger(__name__)

VALID_HORIZONS = {"short_term", "long_term"}
VALID_VERDICTS = {"Bullish", "Bearish", "Neutral"}
VALID_CONFIDENCE = {"low", "medium", "high"}
VALID_AGENTS = {"fundamentals", "sentiment", "demand"}


class InvalidPayload(ValueError):
    pass


def validate_report_payload(payload: dict) -> dict:
    horizon = payload.get("horizon")
    verdict = payload.get("verdict")
    confidence = payload.get("confidence")
    reasoning = (payload.get("reasoning") or "").strip()
    ticker = (payload.get("ticker") or "").strip().upper()

    if horizon not in VALID_HORIZONS:
        raise InvalidPayload(f"horizon must be one of {VALID_HORIZONS}, got {horizon!r}")
    if verdict not in VALID_VERDICTS:
        raise InvalidPayload(f"verdict must be one of {VALID_VERDICTS}, got {verdict!r}")
    if confidence not in VALID_CONFIDENCE:
        raise InvalidPayload(f"confidence must be one of {VALID_CONFIDENCE}, got {confidence!r}")
    if not reasoning:
        raise InvalidPayload("reasoning must be a non-empty string")
    if not ticker:
        raise InvalidPayload("ticker must be a non-empty string")

    return {
        "ticker": ticker,
        "horizon": horizon,
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": reasoning,
    }


def validate_signal_summary_payload(payload: dict) -> dict:
    agent = payload.get("agent")
    summary = (payload.get("summary") or "").strip()
    ticker = (payload.get("ticker") or "").strip().upper()

    if agent not in VALID_AGENTS:
        raise InvalidPayload(f"agent must be one of {VALID_AGENTS}, got {agent!r}")
    if not summary:
        raise InvalidPayload("summary must be a non-empty string")
    if not ticker:
        raise InvalidPayload("ticker must be a non-empty string")

    return {"ticker": ticker, "agent": agent, "summary": summary}


ORCHESTRATOR_SYSTEM_PROMPT = """You are the lead analyst on a stock research team. \
You will be given a ticker plus written summaries from three specialist analysts: \
fundamentals, sentiment, and demand. Synthesize TWO separate verdicts:
- short_term (1-4 weeks): weight sentiment and demand signals more heavily.
- long_term (2-4 quarters): weight fundamentals more heavily.

Each verdict must be exactly one of Bullish, Bearish, or Neutral, with a confidence \
of low, medium, or high, and written reasoning that cites the specific findings that \
drove it. Never output a numeric price target or a single composite score - this is \
a qualitative research tool, not a price predictor. Respond with ONLY the JSON object \
matching the required schema, nothing else."""

SYNTHESIS_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "short_term": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": sorted(VALID_VERDICTS)},
                "confidence": {"type": "string", "enum": sorted(VALID_CONFIDENCE)},
                "reasoning": {"type": "string"},
            },
            "required": ["verdict", "confidence", "reasoning"],
        },
        "long_term": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": sorted(VALID_VERDICTS)},
                "confidence": {"type": "string", "enum": sorted(VALID_CONFIDENCE)},
                "reasoning": {"type": "string"},
            },
            "required": ["verdict", "confidence", "reasoning"],
        },
    },
    "required": ["short_term", "long_term"],
}


def build_synthesis_prompt(ticker: str, summaries: dict[str, str]) -> str:
    sections = "\n\n".join(
        f"## {agent.capitalize()} analyst\n{summaries.get(agent, '(no summary available)')}"
        for agent in ("fundamentals", "sentiment", "demand")
    )
    return f"Ticker: {ticker}\n\n{sections}"


def synthesize(settings, ticker: str, summaries: dict[str, str]) -> dict[str, dict]:
    logger.info("%s: synthesizing verdicts", ticker)
    messages = [
        {"role": "system", "content": ORCHESTRATOR_SYSTEM_PROMPT},
        {"role": "user", "content": build_synthesis_prompt(ticker, summaries)},
    ]

    last_error: Exception | None = None
    for attempt in range(2):
        if attempt > 0:
            logger.warning("%s: synthesis retry after invalid output: %s", ticker, last_error)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That response was invalid: {last_error}. Reply again with "
                        "ONLY JSON matching the schema."
                    ),
                }
            )

        raw = ollama_client.chat_structured_text(settings, messages, SYNTHESIS_JSON_SCHEMA)
        logger.debug("%s: synthesis raw output: %s", ticker, raw)
        try:
            data = json.loads(raw)
            short_term = validate_report_payload(
                {**data["short_term"], "ticker": ticker, "horizon": "short_term"}
            )
            long_term = validate_report_payload(
                {**data["long_term"], "ticker": ticker, "horizon": "long_term"}
            )
            return {"short_term": short_term, "long_term": long_term}
        except (json.JSONDecodeError, KeyError, InvalidPayload) as e:
            last_error = e
            messages.append({"role": "assistant", "content": raw})

    logger.error("%s: synthesis failed after retry: %s", ticker, last_error)
    raise InvalidPayload(f"synthesis failed after retry: {last_error}")
