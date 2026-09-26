"""CLI entrypoint: run the agentic research pipeline for one or more tickers.

Usage:
    python research.py AAPL
    python research.py AAPL TSLA MSFT
    python research.py --watchlist-file watchlist.example.txt

research_ticker() and run_watchlist() are also imported by app.py's
"Run Research" tab, so the CLI and the web UI share one execution path.
"""

from __future__ import annotations

import argparse
import logging
import time

import agents.demand as demand
import agents.fundamentals as fundamentals
import agents.sentiment as sentiment
import db
from agents.orchestrator import synthesize
from config import load_settings
from logging_config import configure_logging
from tools.duckduckgo_tools import search_web
from tools.finnhub_tools import fetch_company_news
from tools.reddit_tools import search_subreddits
from tools.yfinance_tools import (
    fetch_fundamentals,
    fetch_overview,
    fetch_ownership_and_analysts,
    fetch_sector_benchmark_pe,
    fetch_technicals,
)

logger = logging.getLogger(__name__)

DISCLAIMER = "Not financial advice — informational/research purposes only."

_AGENT_MODULES = [
    (fundamentals, "fundamentals"),
    (sentiment, "sentiment"),
    (demand, "demand"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agentic stock research")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols, e.g. AAPL TSLA")
    parser.add_argument(
        "--watchlist-file",
        help="Path to a file with one ticker per line, in addition to any given directly",
    )
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers]
    if args.watchlist_file:
        with open(args.watchlist_file) as f:
            tickers.extend(
                line.strip().upper() for line in f if line.strip() and not line.startswith("#")
            )

    if not tickers:
        parser.error("Provide at least one ticker or --watchlist-file")

    # de-dupe, preserve order
    seen = set()
    deduped = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    args.tickers = deduped
    return args


def _fetch_signal_raw_data(
    agent_name: str, ticker: str, overview: dict | None, settings
) -> dict | None:
    """Structured data captured directly (not via the LLM's own tool calls)
    so the UI can show clean stats regardless of what an agent's prose
    happened to mention - same reasoning as fetch_overview(). Duplicates
    what the fundamentals/demand/sentiment agents' own tools already fetch
    internally; an acceptable tradeoff (a couple of cheap extra calls) for
    reliable, structured data independent of LLM behavior.
    """
    try:
        if agent_name == "fundamentals":
            fundamentals_result = fetch_fundamentals(ticker)
            key_ratios = fundamentals_result.get("key_ratios", {})
            key_ratios["sector_pe"] = fetch_sector_benchmark_pe((overview or {}).get("sector"))
            return {
                **(overview or {}),
                "key_ratios": key_ratios,
                "recent_dividends": fundamentals_result.get("recent_dividends"),
                "etf_details": fundamentals_result.get("etf_details"),
            }
        if agent_name == "demand":
            return {
                "technicals": fetch_technicals(ticker),
                "ownership": fetch_ownership_and_analysts(ticker),
            }
        if agent_name == "sentiment":
            data = {"web_search": search_web(f"{ticker} stock news analysis")}
            if settings.finnhub_configured:
                data["company_news"] = fetch_company_news(settings.finnhub_api_key, ticker)
            if settings.reddit_configured:
                data["reddit_mentions"] = search_subreddits(
                    settings.reddit_client_id,
                    settings.reddit_client_secret,
                    settings.reddit_user_agent,
                    ticker,
                    settings.reddit_subreddits,
                )
            return data
    except Exception as e:  # noqa: BLE001 - descriptive lookup failing shouldn't abort research
        logger.warning("%s: %s raw data fetch failed: %s", ticker, agent_name, e)
        return None
    return None


def _build_ticker_metrics(fundamentals_data: dict | None, demand_data: dict | None) -> dict:
    """Flattens the fundamentals/demand raw data into the ticker_metrics
    column shape (see db.METRIC_FIELDS) - the same source fields
    app.py's render_key_stats() displays, just captured as a durable,
    chartable time series instead of only the latest snapshot."""
    key_ratios = (fundamentals_data or {}).get("key_ratios", {})
    technicals = (demand_data or {}).get("technicals", {})
    ownership = (demand_data or {}).get("ownership", {})
    analyst_targets = ownership.get("analyst_price_targets") or {}
    etf_details = (fundamentals_data or {}).get("etf_details") or {}

    return {
        "price": technicals.get("last_close"),
        "market_cap": key_ratios.get("marketCap"),
        "pe_ratio": key_ratios.get("trailingPE"),
        "forward_pe": key_ratios.get("forwardPE"),
        "peg_ratio": key_ratios.get("pegRatio"),
        "price_to_book": key_ratios.get("priceToBook"),
        "debt_to_equity": key_ratios.get("debtToEquity"),
        "roe": key_ratios.get("returnOnEquity"),
        "gross_margin": key_ratios.get("grossMargins"),
        "operating_margin": key_ratios.get("operatingMargins"),
        "profit_margin": key_ratios.get("profitMargins"),
        "revenue_growth": key_ratios.get("revenueGrowth"),
        "earnings_growth": key_ratios.get("earningsGrowth"),
        "dividend_yield": key_ratios.get("dividendYield"),
        "beta": key_ratios.get("beta"),
        "fifty_two_week_low": key_ratios.get("fiftyTwoWeekLow"),
        "fifty_two_week_high": key_ratios.get("fiftyTwoWeekHigh"),
        "rsi_14": technicals.get("rsi_14"),
        "volume_ratio": technicals.get("volume_ratio"),
        "sma_20": technicals.get("sma_20"),
        "sma_50": technicals.get("sma_50"),
        "institutional_holders_count": ownership.get("institutional_holders_count"),
        "short_percent_of_float": ownership.get("short_percent_of_float"),
        "analyst_target_mean": analyst_targets.get("mean"),
        "analyst_target_low": analyst_targets.get("low"),
        "analyst_target_high": analyst_targets.get("high"),
        "sma_200": technicals.get("sma_200"),
        "current_ratio": key_ratios.get("currentRatio"),
        "quick_ratio": key_ratios.get("quickRatio"),
        "ev_ebitda": key_ratios.get("enterpriseToEbitda"),
        "price_to_sales": key_ratios.get("priceToSalesTrailing12Months"),
        "payout_ratio": key_ratios.get("payoutRatio"),
        "expense_ratio": etf_details.get("expense_ratio"),
        "total_assets": etf_details.get("total_assets"),
    }


def research_ticker(conn, ticker_run_id: int, ticker: str, settings, on_step=None) -> None:
    def step(msg: str) -> None:
        logger.info("%s: %s", ticker, msg)
        if on_step:
            on_step(msg)

    step("fetching overview...")
    try:
        overview = fetch_overview(ticker)
    except Exception as e:  # noqa: BLE001 - descriptive lookup failing shouldn't abort research
        logger.warning("%s: overview fetch failed: %s", ticker, e)
        overview = None

    fundamentals_data = None
    demand_data = None

    for module, name in _AGENT_MODULES:
        step(f"running {name} agent...")
        summary = module.run(settings, ticker)
        raw_data = _fetch_signal_raw_data(name, ticker, overview, settings)
        if name == "fundamentals":
            fundamentals_data = raw_data
        elif name == "demand":
            demand_data = raw_data
        db.save_signal_summary(conn, ticker_run_id, ticker, name, summary, raw_data)

    try:
        metrics = _build_ticker_metrics(fundamentals_data, demand_data)
        if db.latest_ticker_metrics(conn, ticker) == metrics:
            logger.info("%s: metrics unchanged since last run, not storing a duplicate row", ticker)
        else:
            db.save_ticker_metrics(conn, ticker_run_id, ticker, metrics)
    except Exception as e:  # noqa: BLE001 - metrics logging shouldn't abort research
        logger.warning("%s: saving ticker metrics failed: %s", ticker, e)

    step("synthesizing verdicts...")
    summaries = {
        row["agent"]: row["summary_text"]
        for row in db.signal_summaries_for_ticker_run(conn, ticker_run_id)
    }
    reports = synthesize(settings, ticker, summaries)
    for payload in reports.values():
        db.save_report_row(
            conn,
            ticker_run_id,
            ticker,
            payload["horizon"],
            payload["verdict"],
            payload["confidence"],
            payload["reasoning"],
        )
    step("done")


def run_watchlist(
    tickers: list[str], settings, conn, on_step=None, on_ticker_done=None, should_cancel=None
) -> tuple[int, bool]:
    """Runs research for each ticker sequentially, persisting results.

    on_step(ticker, msg) - live progress, if given.
    on_ticker_done(ticker, ticker_run_id, success) - called after each ticker finishes.
    should_cancel() -> bool - checked before starting each new ticker, if given. A
        ticker already in progress always finishes normally - aborting mid-LLM-call
        would just waste the in-flight work, so "stop" takes effect between tickers.
    Returns (run_id, any_ticker_failed).
    """
    run_id = db.start_run(conn, tickers)
    conn.commit()  # make the run visible to other connections (e.g. a live UI poller) immediately
    run_failed = False
    cancelled = False

    for ticker in tickers:
        if should_cancel and should_cancel():
            logger.info("watchlist cancelled before %s (run %d)", ticker, run_id)
            cancelled = True
            break

        ticker_run_id = db.start_ticker_run(conn, run_id, ticker)
        conn.commit()  # same - a 'running' ticker_run should be visible right away, not ~30s later
        success = True
        try:
            step = (lambda msg, t=ticker: on_step(t, msg)) if on_step else None
            research_ticker(conn, ticker_run_id, ticker, settings, on_step=step)
            db.finish_ticker_run(conn, ticker_run_id, "completed")
        except Exception as e:  # noqa: BLE001 - one bad ticker shouldn't kill the watchlist
            db.finish_ticker_run(conn, ticker_run_id, "failed", str(e))
            run_failed = True
            success = False
            logger.error("%s: research failed: %s", ticker, e)

        conn.commit()  # persist each ticker's results even if a later one crashes

        if on_ticker_done:
            on_ticker_done(ticker, ticker_run_id, success)

        if ticker != tickers[-1]:
            time.sleep(settings.ticker_pause_seconds)

    db.finish_run(conn, run_id, "cancelled" if cancelled else ("failed" if run_failed else "completed"))
    return run_id, run_failed


def print_saved_report(conn, ticker_run_id: int, ticker: str) -> None:
    reports = db.reports_for_ticker_run(conn, ticker_run_id)
    summaries = db.signal_summaries_for_ticker_run(conn, ticker_run_id)

    print(f"\n=== {ticker} research report ===")
    for row in reports:
        print(f"\n[{row['horizon']}] {row['verdict']} (confidence: {row['confidence']})")
        print(row["reasoning"])

    for row in summaries:
        print(f"\n-- {row['agent']} signal --")
        print(row["summary_text"])

    print(f"\n{DISCLAIMER}")


def main() -> None:
    args = parse_args()
    settings = load_settings()
    configure_logging(settings.log_path, settings.log_level)
    db.init_db(settings.db_path)

    with db.connect(settings.db_path) as conn:
        def on_ticker_done(ticker: str, ticker_run_id: int, success: bool) -> None:
            if success:
                print_saved_report(conn, ticker_run_id, ticker)
            else:
                print(f"  [error] research failed for {ticker} - see {settings.log_path}")

        run_watchlist(args.tickers, settings, conn, on_ticker_done=on_ticker_done)


if __name__ == "__main__":
    main()
