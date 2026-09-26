"""Sentiment subagent: news, Reddit, and general web commentary."""

from __future__ import annotations

import json
import logging

import ollama_client
from config import Settings
from tools.duckduckgo_tools import search_web
from tools.finnhub_tools import fetch_company_news
from tools.reddit_tools import search_subreddits

logger = logging.getLogger(__name__)

_SEARCH_WEB_SPEC = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "Search the web via DuckDuckGo for general news/commentary about a "
            "company or ticker."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query, e.g. 'AAPL stock analyst upgrade'",
                },
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
}

_COMPANY_NEWS_SPEC = {
    "type": "function",
    "function": {
        "name": "get_company_news",
        "description": "Fetch recent company news headlines/summaries for a ticker from Finnhub.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "days_back": {"type": "integer", "description": "How many days back to search", "default": 14},
            },
            "required": ["ticker"],
        },
    },
}

_REDDIT_MENTIONS_SPEC = {
    "type": "function",
    "function": {
        "name": "get_reddit_mentions",
        "description": "Search target subreddits for recent posts mentioning a ticker via Reddit.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "limit": {"type": "integer", "default": 25},
                "time_filter": {
                    "type": "string",
                    "enum": ["hour", "day", "week", "month", "year", "all"],
                    "default": "week",
                },
            },
            "required": ["ticker"],
        },
    },
}


def _search_web(args: dict) -> str:
    query = args["query"]
    max_results = args.get("max_results") or 5
    return json.dumps(search_web(query, max_results), default=str)


def build_tools(settings: Settings) -> tuple[list[dict], dict]:
    """Assembles the sentiment agent's tool set, adapting to which of
    Finnhub/Reddit are configured. search_web needs no credentials, so it's
    always included - it's a normal signal source, not a fallback.
    """
    tool_specs = [_SEARCH_WEB_SPEC]
    tool_impls = {"search_web": _search_web}

    if settings.finnhub_configured:
        def _get_company_news(args: dict) -> str:
            ticker = args["ticker"].upper()
            days_back = args.get("days_back") or 14
            articles = fetch_company_news(settings.finnhub_api_key, ticker, days_back)
            return json.dumps(articles, default=str)

        tool_specs.append(_COMPANY_NEWS_SPEC)
        tool_impls["get_company_news"] = _get_company_news

    if settings.reddit_configured:
        def _get_reddit_mentions(args: dict) -> str:
            ticker = args["ticker"].upper()
            limit = args.get("limit") or 25
            time_filter = args.get("time_filter") or "week"
            posts = search_subreddits(
                settings.reddit_client_id,
                settings.reddit_client_secret,
                settings.reddit_user_agent,
                ticker,
                settings.reddit_subreddits,
                limit=limit,
                time_filter=time_filter,
            )
            return json.dumps(posts, default=str)

        tool_specs.append(_REDDIT_MENTIONS_SPEC)
        tool_impls["get_reddit_mentions"] = _get_reddit_mentions

    return tool_specs, tool_impls


def _build_system_prompt(settings: Settings) -> str:
    missing_note = ""
    if not settings.finnhub_configured:
        missing_note += " Finnhub news is unavailable (no API key) - rely on search_web for news."
    if not settings.reddit_configured:
        missing_note += " Reddit is unavailable (no credentials) - rely on search_web for retail sentiment."

    return (
        "You are a market sentiment analyst. Given a ticker, gather recent "
        "news headlines, retail-investor commentary (Reddit), and general web "
        "commentary via search_web." + missing_note +
        " Then write a concise summary (5-8 sentences) covering: overall "
        "sentiment direction (positive/negative/mixed), what's driving it "
        "(specific events, earnings, news), and whether it's a short-lived "
        "reaction or a sustained narrative. Do not give a buy/sell verdict "
        "yourself - that is the orchestrator's job."
    )


def run(settings: Settings, ticker: str) -> str:
    logger.info("%s: sentiment agent starting", ticker)
    tool_specs, tool_impls = build_tools(settings)
    system_prompt = _build_system_prompt(settings)
    result = ollama_client.run_agent(
        settings, system_prompt, tool_specs, tool_impls, f"Research {ticker}."
    )
    logger.info("%s: sentiment agent finished", ticker)
    return result
