"""General web commentary via DuckDuckGo search (no API key required).

Replaces Claude's built-in WebSearch tool, which has no local equivalent.
"""

from __future__ import annotations

import logging

from ddgs import DDGS

from tools.ratelimit import with_retry

logger = logging.getLogger(__name__)


@with_retry
def search_web(query: str, max_results: int = 5) -> list[dict]:
    logger.debug("searching web for %r (max_results=%d)", query, max_results)
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    logger.debug("web search returned %d results for %r", len(results), query)
    return [
        {"title": r.get("title"), "snippet": r.get("body"), "url": r.get("href")}
        for r in results
    ]
