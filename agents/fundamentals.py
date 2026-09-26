"""Fundamentals subagent: financial statements and key ratios via yfinance."""

from __future__ import annotations

import json
import logging

import ollama_client
from tools.yfinance_tools import fetch_fundamentals

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a fundamentals research analyst. Given a ticker, call "
    "get_fundamentals once, then write a concise summary (5-8 sentences) "
    "covering: revenue/earnings trend, margins, balance sheet health "
    "(debt/equity, cash position), and valuation (P/E, PEG) relative to "
    "typical ranges for the sector if known. Note any data that was missing "
    "or unavailable rather than guessing. Do not give a buy/sell verdict "
    "yourself - that is the orchestrator's job."
)

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "get_fundamentals",
            "description": (
                "Fetch income statement, balance sheet, cash flow, and key "
                "valuation/profitability ratios for a US-listed ticker via yfinance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol, e.g. AAPL"},
                },
                "required": ["ticker"],
            },
        },
    }
]


def _get_fundamentals(args: dict) -> str:
    ticker = args["ticker"].upper()
    data = fetch_fundamentals(ticker)
    return json.dumps(data, default=str)


TOOL_IMPLS = {"get_fundamentals": _get_fundamentals}


def run(settings, ticker: str) -> str:
    logger.info("%s: fundamentals agent starting", ticker)
    result = ollama_client.run_agent(
        settings, SYSTEM_PROMPT, TOOL_SPECS, TOOL_IMPLS, f"Research {ticker}."
    )
    logger.info("%s: fundamentals agent finished", ticker)
    return result
