"""Demand subagent: volume/technicals, ownership, and analyst signals via yfinance.

v2 extension point: an options-flow tool (unusual activity, put/call ratio) would
go here, backed by a paid data provider (e.g. Polygon, Tradier) - deliberately
not built for v1. To add it: define its TOOL_SPECS entry + TOOL_IMPLS callable
below, and extend SYSTEM_PROMPT to reference it.
"""

from __future__ import annotations

import json
import logging

import ollama_client
from tools.yfinance_tools import fetch_ownership_and_analysts, fetch_technicals

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a market-demand analyst. Given a ticker, call "
    "get_volume_and_technicals and get_ownership_and_analyst_signals, "
    "then write a concise summary (5-8 sentences) covering: whether "
    "trading volume/momentum is elevated or muted, what institutional "
    "ownership and short interest suggest about who is accumulating or "
    "exiting, and how analyst ratings/price targets have trended. Do not "
    "give a buy/sell verdict yourself - that is the orchestrator's job."
)

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "get_volume_and_technicals",
            "description": (
                "Compute recent-vs-average trading volume, RSI(14), and moving "
                "averages for a ticker via yfinance price history."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "period": {
                        "type": "string",
                        "description": "yfinance period string, e.g. 1mo, 3mo, 6mo, 1y",
                        "default": "3mo",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ownership_and_analyst_signals",
            "description": (
                "Fetch major/institutional holders, short-interest metrics, and "
                "analyst recommendations/price targets for a ticker via yfinance."
            ),
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    },
]


def _get_volume_and_technicals(args: dict) -> str:
    ticker = args["ticker"].upper()
    period = args.get("period") or "3mo"
    return json.dumps(fetch_technicals(ticker, period), default=str)


def _get_ownership_and_analyst_signals(args: dict) -> str:
    ticker = args["ticker"].upper()
    return json.dumps(fetch_ownership_and_analysts(ticker), default=str)


TOOL_IMPLS = {
    "get_volume_and_technicals": _get_volume_and_technicals,
    "get_ownership_and_analyst_signals": _get_ownership_and_analyst_signals,
}


# --- v2 extension point (not built) ---
# TOOL_SPECS.append({"type": "function", "function": {"name": "get_options_flow",
#     "description": "Fetch unusual options activity / put-call ratio (paid data).",
#     "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}}})
# TOOL_IMPLS["get_options_flow"] = ...


def run(settings, ticker: str) -> str:
    logger.info("%s: demand agent starting", ticker)
    result = ollama_client.run_agent(
        settings, SYSTEM_PROMPT, TOOL_SPECS, TOOL_IMPLS, f"Research {ticker}."
    )
    logger.info("%s: demand agent finished", ticker)
    return result
