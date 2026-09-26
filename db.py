"""SQLite schema and CRUD/query helpers for stocksearch run history."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    tickers TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS ticker_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    ticker TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_ticker_runs_ticker ON ticker_runs(ticker);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker_run_id INTEGER NOT NULL REFERENCES ticker_runs(id),
    ticker TEXT NOT NULL,
    horizon TEXT NOT NULL CHECK (horizon IN ('short_term','long_term')),
    verdict TEXT NOT NULL CHECK (verdict IN ('Bullish','Bearish','Neutral')),
    confidence TEXT NOT NULL CHECK (confidence IN ('low','medium','high')),
    reasoning TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_ticker_created ON reports(ticker, created_at);

CREATE TABLE IF NOT EXISTS signal_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker_run_id INTEGER NOT NULL REFERENCES ticker_runs(id),
    ticker TEXT NOT NULL,
    agent TEXT NOT NULL CHECK (agent IN ('fundamentals','sentiment','demand')),
    summary_text TEXT NOT NULL,
    raw_data_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_summaries_ticker_created ON signal_summaries(ticker, created_at);

CREATE TABLE IF NOT EXISTS ticker_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker_run_id INTEGER NOT NULL REFERENCES ticker_runs(id),
    ticker TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    price REAL,
    market_cap REAL,
    pe_ratio REAL,
    forward_pe REAL,
    peg_ratio REAL,
    price_to_book REAL,
    debt_to_equity REAL,
    roe REAL,
    gross_margin REAL,
    operating_margin REAL,
    profit_margin REAL,
    revenue_growth REAL,
    earnings_growth REAL,
    dividend_yield REAL,
    beta REAL,
    fifty_two_week_low REAL,
    fifty_two_week_high REAL,
    rsi_14 REAL,
    volume_ratio REAL,
    sma_20 REAL,
    sma_50 REAL,
    sma_200 REAL,
    institutional_holders_count INTEGER,
    short_percent_of_float REAL,
    analyst_target_mean REAL,
    analyst_target_low REAL,
    analyst_target_high REAL,
    current_ratio REAL,
    quick_ratio REAL,
    ev_ebitda REAL,
    price_to_sales REAL,
    payout_ratio REAL,
    expense_ratio REAL,
    total_assets REAL
);
CREATE INDEX IF NOT EXISTS idx_ticker_metrics_ticker_recorded ON ticker_metrics(ticker, recorded_at);
"""

# Columns added after ticker_metrics already existed in production - present
# in SCHEMA above for fresh databases, but an existing database needs an
# explicit migration since CREATE TABLE IF NOT EXISTS won't add columns to
# a table that's already there. See _migrate_ticker_metrics_columns().
_NEW_TICKER_METRICS_COLUMNS = [
    "sma_200", "current_ratio", "quick_ratio", "ev_ebitda",
    "price_to_sales", "payout_ratio", "expense_ratio", "total_assets",
]

# Column -> dropdown label, in display order. Also doubles as the allowlist
# for metric_history()'s column-name interpolation (never user-controlled -
# always one of these fixed, known-safe names).
METRIC_FIELDS = {
    "price": "Price",
    "market_cap": "Market Cap",
    "pe_ratio": "P/E",
    "forward_pe": "Forward P/E",
    "peg_ratio": "PEG",
    "price_to_book": "P/B",
    "debt_to_equity": "Debt/Equity",
    "roe": "ROE",
    "gross_margin": "Gross Margin",
    "operating_margin": "Operating Margin",
    "profit_margin": "Profit Margin",
    "revenue_growth": "Revenue Growth",
    "earnings_growth": "Earnings Growth",
    "dividend_yield": "Dividend Yield",
    "beta": "Beta",
    "fifty_two_week_low": "52wk Low",
    "fifty_two_week_high": "52wk High",
    "rsi_14": "RSI (14)",
    "volume_ratio": "Volume vs Avg",
    "sma_20": "SMA 20",
    "sma_50": "SMA 50",
    "sma_200": "SMA 200",
    "institutional_holders_count": "Institutional Holders",
    "short_percent_of_float": "Short % of Float",
    "analyst_target_mean": "Analyst Target (mean)",
    "analyst_target_low": "Analyst Target (low)",
    "analyst_target_high": "Analyst Target (high)",
    "current_ratio": "Current Ratio",
    "quick_ratio": "Quick Ratio",
    "ev_ebitda": "EV/EBITDA",
    "price_to_sales": "P/S",
    "payout_ratio": "Payout Ratio",
    "expense_ratio": "Expense Ratio (ETF)",
    "total_assets": "Total Assets/AUM (ETF)",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_ticker_metrics_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(ticker_metrics)")}
    for col in _NEW_TICKER_METRICS_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE ticker_metrics ADD COLUMN {col} REAL")


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate_ticker_metrics_columns(conn)
        # WAL mode lets the polling reader (app.py's live grid) proceed without
        # blocking on the background research worker's writes, and vice versa.
        conn.execute("PRAGMA journal_mode=WAL")


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- writes -----------------------------------------------------------------


def start_run(conn: sqlite3.Connection, tickers: list[str]) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, tickers, status) VALUES (?, ?, 'running')",
        (now_iso(), json.dumps(tickers)),
    )
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, status: str) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ? WHERE id = ?",
        (now_iso(), status, run_id),
    )


def start_ticker_run(conn: sqlite3.Connection, run_id: int, ticker: str) -> int:
    cur = conn.execute(
        "INSERT INTO ticker_runs (run_id, ticker, started_at, status) "
        "VALUES (?, ?, ?, 'running')",
        (run_id, ticker, now_iso()),
    )
    return cur.lastrowid


def finish_ticker_run(
    conn: sqlite3.Connection,
    ticker_run_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    conn.execute(
        "UPDATE ticker_runs SET finished_at = ?, status = ?, error_message = ? "
        "WHERE id = ?",
        (now_iso(), status, error_message, ticker_run_id),
    )


def save_report_row(
    conn: sqlite3.Connection,
    ticker_run_id: int,
    ticker: str,
    horizon: str,
    verdict: str,
    confidence: str,
    reasoning: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO reports "
        "(ticker_run_id, ticker, horizon, verdict, confidence, reasoning, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker_run_id, ticker, horizon, verdict, confidence, reasoning, now_iso()),
    )
    return cur.lastrowid


def save_signal_summary(
    conn: sqlite3.Connection,
    ticker_run_id: int,
    ticker: str,
    agent: str,
    summary_text: str,
    raw_data: dict | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO signal_summaries "
        "(ticker_run_id, ticker, agent, summary_text, raw_data_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            ticker_run_id,
            ticker,
            agent,
            summary_text,
            json.dumps(raw_data, default=str) if raw_data is not None else None,
            now_iso(),
        ),
    )
    return cur.lastrowid


def save_ticker_metrics(
    conn: sqlite3.Connection, ticker_run_id: int, ticker: str, metrics: dict
) -> int:
    """One row per research run capturing the numeric fundamentals/technicals
    already fetched, as a proper time series (see METRIC_FIELDS) - rather
    than leaving them buried in signal_summaries.raw_data_json where charting
    them over time would mean parsing JSON on every read."""
    columns = list(METRIC_FIELDS.keys())
    placeholders = ", ".join("?" for _ in columns)
    cur = conn.execute(
        f"INSERT INTO ticker_metrics (ticker_run_id, ticker, recorded_at, {', '.join(columns)}) "
        f"VALUES (?, ?, ?, {placeholders})",
        (ticker_run_id, ticker, now_iso(), *(metrics.get(c) for c in columns)),
    )
    return cur.lastrowid


# --- reads --------------------------------------------------------------


def distinct_tickers(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT ticker FROM reports ORDER BY ticker").fetchall()
    return [r["ticker"] for r in rows]


def latest_ticker_run_id(conn: sqlite3.Connection, ticker: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM ticker_runs WHERE ticker = ? AND status = 'completed' "
        "ORDER BY finished_at DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return row["id"] if row else None


def reports_for_ticker_run(conn: sqlite3.Connection, ticker_run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM reports WHERE ticker_run_id = ? ORDER BY horizon",
        (ticker_run_id,),
    ).fetchall()


def signal_summaries_for_ticker_run(
    conn: sqlite3.Connection, ticker_run_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM signal_summaries WHERE ticker_run_id = ? ORDER BY agent",
        (ticker_run_id,),
    ).fetchall()


def report_trend(conn: sqlite3.Connection, ticker: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT created_at, horizon, verdict, confidence, reasoning FROM reports "
        "WHERE ticker = ? ORDER BY created_at",
        (ticker,),
    ).fetchall()


def metric_history(conn: sqlite3.Connection, ticker: str, field: str) -> list[sqlite3.Row]:
    """recorded_at + value for one metric field, across every research run
    for this ticker - the time series that powers the metrics-over-time
    chart. `field` is validated against METRIC_FIELDS (a fixed, known-safe
    set of column names) before being interpolated into the query - it is
    never taken directly from user input."""
    if field not in METRIC_FIELDS:
        raise ValueError(f"unknown metric field: {field!r}")
    return conn.execute(
        f"SELECT recorded_at, {field} AS value FROM ticker_metrics "
        f"WHERE ticker = ? AND {field} IS NOT NULL ORDER BY recorded_at",
        (ticker,),
    ).fetchall()


def recent_metrics(conn: sqlite3.Connection, ticker: str, limit: int = 10) -> list[sqlite3.Row]:
    """The last N runs' headline numbers for one ticker, most recent first -
    a compact multi-run view (date/price/P-E/RSI) alongside the single-metric
    chart, so you can see several runs together instead of one metric at a time."""
    return conn.execute(
        "SELECT recorded_at, price, pe_ratio, rsi_14 FROM ticker_metrics "
        "WHERE ticker = ? ORDER BY recorded_at DESC LIMIT ?",
        (ticker, limit),
    ).fetchall()


def latest_ticker_metrics(conn: sqlite3.Connection, ticker: str) -> dict | None:
    """The most recent ticker_metrics row for a ticker, as a plain dict over
    METRIC_FIELDS's columns - compared against a freshly fetched metrics
    dict before deciding whether a new run's numbers are actually different
    from last time, so unchanged data doesn't get written as a duplicate row."""
    columns = list(METRIC_FIELDS.keys())
    row = conn.execute(
        f"SELECT {', '.join(columns)} FROM ticker_metrics "
        f"WHERE ticker = ? ORDER BY recorded_at DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return {c: row[c] for c in columns} if row else None


def delete_duplicate_ticker_metrics(conn: sqlite3.Connection) -> int:
    """One-off cleanup for rows written before the insert-time dedup check
    existed: for each ticker, walks its ticker_metrics rows oldest-to-newest
    and deletes any row that's an exact match (over METRIC_FIELDS) of the
    nearest earlier row still being kept - the same "unchanged since last
    kept run" definition latest_ticker_metrics()/research.py uses going
    forward. Only collapses consecutive runs of identical data; a row that
    matches an older, non-adjacent row (data changed and then reverted) is
    kept, since that's a real, distinct data point. Returns the number of
    rows deleted."""
    columns = list(METRIC_FIELDS.keys())
    tickers = [r["ticker"] for r in conn.execute("SELECT DISTINCT ticker FROM ticker_metrics")]

    to_delete = []
    for ticker in tickers:
        rows = conn.execute(
            f"SELECT id, {', '.join(columns)} FROM ticker_metrics "
            f"WHERE ticker = ? ORDER BY recorded_at",
            (ticker,),
        ).fetchall()
        kept = None
        for row in rows:
            current = {c: row[c] for c in columns}
            if current == kept:
                to_delete.append(row["id"])
            else:
                kept = current

    if to_delete:
        conn.executemany("DELETE FROM ticker_metrics WHERE id = ?", [(i,) for i in to_delete])
    return len(to_delete)


def high_confidence_bullish_for_run(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    """Bullish + high-confidence verdicts (either horizon) among the tickers
    scanned in one specific run - used by the Screener tab to surface only
    the strongest signals from a batch scan, not all-time history."""
    return conn.execute(
        """
        SELECT r.ticker, r.horizon, r.verdict, r.confidence, r.reasoning
        FROM reports r
        JOIN ticker_runs tr ON tr.id = r.ticker_run_id
        WHERE tr.run_id = ? AND r.verdict = 'Bullish' AND r.confidence = 'high'
        ORDER BY r.ticker, r.horizon
        """,
        (run_id,),
    ).fetchall()


def latest_verdict_per_ticker(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.ticker, r.horizon, r.verdict, r.confidence, r.created_at
        FROM reports r
        WHERE r.created_at = (
            SELECT MAX(r2.created_at) FROM reports r2
            WHERE r2.ticker = r.ticker AND r2.horizon = r.horizon
        )
        ORDER BY r.ticker, r.horizon
        """
    ).fetchall()


def parse_overview(summaries: list[sqlite3.Row]) -> dict | None:
    """Extracts the fundamentals agent's cached tools.yfinance_tools.fetch_overview()
    payload (name/quote_type/sector/industry/category/fund_family/description)
    from a ticker_run's signal_summaries rows, if present."""
    for row in summaries:
        if row["agent"] == "fundamentals" and row["raw_data_json"]:
            try:
                data = json.loads(row["raw_data_json"])
            except (TypeError, ValueError):
                continue
            if data.get("description") or data.get("name"):
                return data
    return None


def grid_rows(conn: sqlite3.Connection) -> list[dict]:
    """One row per ticker, reflecting its most recent ticker_runs row
    regardless of status - unlike latest_ticker_run_id (completed-only),
    this includes in-progress 'running' rows, which the live results grid
    needs to show a ticker the moment its research starts. Older history
    isn't lost, just not surfaced here - see report_trend() for that.

    Type/sector here intentionally come from parse_overview() (signal_summaries),
    not ticker_metrics - this isn't duplication, it's a deliberate split:
    categorical/descriptive fields (type, sector, description) live with the
    overview data, numeric time-series fields (price, P/E, RSI, ...) live in
    ticker_metrics. See metric_history() for the latter.
    """
    latest = conn.execute(
        """
        SELECT tr.id AS ticker_run_id, tr.ticker, tr.status, tr.error_message, tr.finished_at
        FROM ticker_runs tr
        INNER JOIN (
            SELECT ticker, MAX(id) AS max_id FROM ticker_runs GROUP BY ticker
        ) m ON tr.ticker = m.ticker AND tr.id = m.max_id
        """
    ).fetchall()

    rows = []
    for tr in latest:
        reports = {r["horizon"]: r for r in reports_for_ticker_run(conn, tr["ticker_run_id"])}
        summaries = signal_summaries_for_ticker_run(conn, tr["ticker_run_id"])
        overview = parse_overview(summaries) or {}
        short = reports.get("short_term")
        long_ = reports.get("long_term")
        rows.append(
            {
                "ticker_run_id": tr["ticker_run_id"],
                "ticker": tr["ticker"],
                "type": overview.get("quote_type") or "",
                "sector": overview.get("sector") or overview.get("category") or "",
                "status": tr["status"],
                "finished_at": tr["finished_at"],
                "short_verdict": short["verdict"] if short else "",
                "short_confidence": short["confidence"] if short else "",
                "long_verdict": long_["verdict"] if long_ else "",
                "long_confidence": long_["confidence"] if long_ else "",
            }
        )
    return rows


def tickers_scanned_today(conn: sqlite3.Connection) -> set[str]:
    """Tickers whose most recent COMPLETED run finished on today's local
    calendar date. finished_at is stored in UTC (see now_iso()), so this
    converts to local time before comparing dates - a naive UTC-string
    comparison would get the day boundary wrong near local midnight."""
    today_local = datetime.now().astimezone().date()
    rows = conn.execute(
        """
        SELECT tr.ticker, tr.finished_at
        FROM ticker_runs tr
        INNER JOIN (
            SELECT ticker, MAX(id) AS max_id FROM ticker_runs
            WHERE status = 'completed' GROUP BY ticker
        ) m ON tr.ticker = m.ticker AND tr.id = m.max_id
        """
    ).fetchall()
    return {
        r["ticker"] for r in rows
        if datetime.fromisoformat(r["finished_at"]).astimezone().date() == today_local
    }
