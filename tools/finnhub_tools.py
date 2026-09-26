"""Company news via Finnhub's free-tier /company-news endpoint."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import finnhub

from tools.ratelimit import with_retry

logger = logging.getLogger(__name__)


@with_retry
def fetch_company_news(api_key: str, ticker: str, days_back: int = 14) -> list[dict]:
    logger.debug("fetching finnhub company news for %s (%d days back)", ticker, days_back)
    client = finnhub.Client(api_key=api_key)
    today = datetime.now()
    start = today - timedelta(days=days_back)

    articles = client.company_news(
        ticker,
        _from=start.strftime("%Y-%m-%d"),
        to=today.strftime("%Y-%m-%d"),
    )

    results = [
        {
            "headline": a.get("headline"),
            "summary": a.get("summary"),
            "source": a.get("source"),
            "url": a.get("url"),
            "datetime": a.get("datetime"),
        }
        for a in articles[:30]
    ]
    logger.debug("finnhub returned %d articles for %s", len(results), ticker)
    return results
