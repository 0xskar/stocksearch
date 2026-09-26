"""Synchronous client for Ollama's /api/chat, plus a generic tool-calling loop.

Verified against installed Ollama v0.17.5 + qwen2.5:7b-instruct before writing
any agent code:
  - message.tool_calls[i].function.arguments comes back as a dict already
    (not a JSON string) - the str fallback below is defensive, not required.
  - a tool-result turn just needs {"role": "tool", "tool_name": ..., "content": ...};
    no tool_call_id threading needed to disambiguate multiple calls in one turn.
  - `format` accepts a bare JSON Schema object directly (no OpenAI-style
    {"type": "json_schema", ...} wrapper) and returns matching JSON text.
"""

from __future__ import annotations

import json
import logging

import requests

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


def _post_chat(settings, messages: list[dict], tools: list[dict] | None = None, format=None) -> dict:
    body = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": False,
        "keep_alive": settings.ollama_keep_alive,
    }
    if tools:
        body["tools"] = tools
    if format:
        body["format"] = format

    try:
        resp = requests.post(
            f"{settings.ollama_host}/api/chat",
            json=body,
            timeout=settings.ollama_timeout_seconds,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Ollama request failed: %s", e)
        raise OllamaError(
            f"Could not reach Ollama at {settings.ollama_host} - is `ollama serve` running "
            f"and is {settings.ollama_model!r} pulled? ({e})"
        ) from e

    return resp.json()["message"]


def run_agent(
    settings,
    system_prompt: str,
    tool_specs: list[dict],
    tool_impls: dict,
    user_prompt: str,
    max_turns: int = 6,
) -> str:
    """Runs a tool-calling conversation to completion, returning the model's
    final text response. Tool execution happens entirely in-process; no
    external orchestration is needed for a single, self-contained agent.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    message: dict = {}
    for _ in range(max_turns):
        message = _post_chat(settings, messages, tools=tool_specs or None)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return (message.get("content") or "").strip()

        messages.append(message)
        for call in tool_calls:
            name = call["function"]["name"]
            args = call["function"]["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            logger.debug("tool call: %s(%r)", name, args)
            impl = tool_impls.get(name)
            if impl is None:
                result = f"Unknown tool {name!r}"
            else:
                try:
                    result = impl(args)
                except Exception as e:  # noqa: BLE001 - one flaky data source shouldn't abort the whole agent
                    result = f"Error calling {name}: {e}"
            logger.debug("tool result: %s -> %d chars", name, len(str(result)))
            messages.append({"role": "tool", "tool_name": name, "content": str(result)})

    # Exhausted max_turns without a final text answer - return whatever's there.
    return (message.get("content") or "").strip()


def chat_structured_text(settings, messages: list[dict], json_schema: dict) -> str:
    """One /api/chat call constrained to json_schema; returns the raw response
    text. The caller parses/validates it - keeps this module dumb and testable.
    """
    return _post_chat(settings, messages, format=json_schema).get("content") or ""
