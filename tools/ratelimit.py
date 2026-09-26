"""Shared retry/backoff for transient errors from external data APIs."""

from __future__ import annotations

import logging

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# yfinance/finnhub/PRAW all raise plain Exception subclasses on HTTP
# failures rather than a shared base class, so we retry broadly and rely
# on stop_after_attempt to bound the damage from a truly broken call.
with_retry = retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
