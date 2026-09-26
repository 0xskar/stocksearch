"""Reddit mentions of a ticker across target subreddits via PRAW."""

from __future__ import annotations

import logging

import praw

from tools.ratelimit import with_retry

logger = logging.getLogger(__name__)


@with_retry
def search_subreddits(
    client_id: str,
    client_secret: str,
    user_agent: str,
    ticker: str,
    subreddits: list[str],
    limit: int = 25,
    time_filter: str = "week",
) -> list[dict]:
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )
    reddit.read_only = True

    query = f"{ticker}"
    subreddit_name = "+".join(subreddits)
    logger.debug("searching r/%s for %r (limit=%d, time_filter=%s)", subreddit_name, query, limit, time_filter)
    subreddit = reddit.subreddit(subreddit_name)

    results = []
    for submission in subreddit.search(query, time_filter=time_filter, limit=limit):
        results.append(
            {
                "title": submission.title,
                "subreddit": str(submission.subreddit),
                "score": submission.score,
                "num_comments": submission.num_comments,
                "created_utc": submission.created_utc,
                "url": submission.url,
                "selftext": submission.selftext[:500] if submission.selftext else "",
            }
        )
    logger.debug("reddit returned %d posts for %s", len(results), ticker)
    return results
