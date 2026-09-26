"""Fundamentals, technicals, ownership, and analyst data via yfinance.

yfinance is an unofficial Yahoo Finance client: fields vary in shape across
tickers/versions and occasionally 404s. Every accessor here is defensive —
a missing field degrades to None rather than blowing up the whole request.
"""

from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

from tools.ratelimit import with_retry

logger = logging.getLogger(__name__)

_INFO_KEYS = [
    "trailingPE",
    "forwardPE",
    "pegRatio",
    "priceToBook",
    "debtToEquity",
    "returnOnEquity",
    "grossMargins",
    "operatingMargins",
    "profitMargins",
    "freeCashflow",
    "revenueGrowth",
    "earningsGrowth",
    "marketCap",
    "sharesShort",
    "shortRatio",
    "shortPercentOfFloat",
    "fiftyTwoWeekLow",
    "fiftyTwoWeekHigh",
    "dividendYield",
    "beta",
    "currentRatio",
    "quickRatio",
    "enterpriseToEbitda",
    "priceToSalesTrailing12Months",
    "payoutRatio",
]

# GICS sector name -> a representative sector ETF, used as a rough P/E
# benchmark so a ticker's own P/E isn't shown in total isolation. Sector
# ETFs genuinely expose a usable trailingPE (verified: XLK 34.7, XLF 15.6,
# XLE 17.2) even though they're funds, not single companies.
SECTOR_ETF_PROXY = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
}


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        logger.warning("yfinance field fetch failed, defaulting to %r", default, exc_info=True)
        return default


def _df_to_records(df: pd.DataFrame | None, max_periods: int = 4) -> list[dict]:
    if df is None or df.empty:
        return []
    trimmed = df.iloc[:, :max_periods]
    records = []
    for column in trimmed.columns:
        period = {"period": str(column)}
        period.update({str(idx): _jsonable(val) for idx, val in trimmed[column].items()})
        records.append(period)
    return records


def _jsonable(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _fetch_screener_symbols(screener_name: str, count: int, quote_type: str) -> list[str]:
    logger.debug("fetching yfinance %r screener (count=%d)", screener_name, count)
    result = yf.screen(screener_name, count=count)
    quotes = result.get("quotes", [])
    symbols = [
        q["symbol"].upper()
        for q in quotes
        if q.get("quoteType") == quote_type and q.get("symbol")
    ]
    logger.debug("%r screener returned %d symbols", screener_name, len(symbols))
    return symbols


@with_retry
def fetch_trending_tickers(count: int = 25) -> list[str]:
    """Tickers currently on Yahoo Finance's 'most actives' screener - a
    genuinely dynamic, self-discovered universe rather than a hardcoded list.
    """
    return _fetch_screener_symbols("most_actives", count, "EQUITY")


@with_retry
def fetch_trending_etfs(count: int = 25) -> list[str]:
    """ETFs currently on Yahoo Finance's 'top ETFs' screener, ranked by
    percent change - a dynamic, self-discovered ETF universe."""
    return _fetch_screener_symbols("top_etfs_us", count, "ETF")


@with_retry
def fetch_overview(ticker: str) -> dict:
    """A general description of what the ticker actually is - company
    business summary for a stock, fund category/family for an ETF. Fetched
    directly (not via the LLM) since this is factual lookup, not analysis,
    and needs to be reliably present regardless of what an agent happened
    to mention in its prose summary.
    """
    t = yf.Ticker(ticker)
    info = _safe(lambda: t.info, {}) or {}

    calendar = _safe(lambda: t.calendar)
    next_earnings_date = None
    if calendar:
        dates = calendar.get("Earnings Date")
        if dates:
            next_earnings_date = str(dates[0])

    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName"),
        "quote_type": info.get("quoteType"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "category": info.get("category"),
        "fund_family": info.get("fundFamily"),
        "description": info.get("longBusinessSummary"),
        "next_earnings_date": next_earnings_date,
    }


def _fetch_recent_dividends(t) -> list[dict] | None:
    dividends = _safe(lambda: t.dividends)
    if dividends is None or dividends.empty:
        return None
    return [
        {"date": str(date), "amount": _jsonable(amount)}
        for date, amount in dividends.tail(8).items()
    ]


def _fetch_etf_details(t, info: dict) -> dict | None:
    # Only ETFs have fund data - skip the lookup entirely for stocks rather
    # than let it raise (yfinance raises YFDataException for non-funds,
    # which _safe() would catch but only after logging a warning on every
    # single stock research run).
    if info.get("quoteType") != "ETF":
        return None

    expense_ratio = info.get("netExpenseRatio")
    total_assets = info.get("totalAssets")
    top_holdings = None
    funds_data = _safe(lambda: t.funds_data)
    if funds_data is not None:
        holdings_df = _safe(lambda: funds_data.top_holdings)
        if holdings_df is not None and not holdings_df.empty:
            top_holdings = [
                {"symbol": str(symbol), "name": row.get("Name"), "weight": _jsonable(row.get("Holding Percent"))}
                for symbol, row in holdings_df.head(10).iterrows()
            ]
    if expense_ratio is None and total_assets is None and top_holdings is None:
        return None
    return {"expense_ratio": expense_ratio, "total_assets": total_assets, "top_holdings": top_holdings}


@with_retry
def fetch_fundamentals(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = _safe(lambda: t.info, {}) or {}
    key_ratios = {k: info.get(k) for k in _INFO_KEYS}

    return {
        "ticker": ticker,
        "income_statement": _safe(lambda: _df_to_records(t.income_stmt)),
        "balance_sheet": _safe(lambda: _df_to_records(t.balance_sheet)),
        "cashflow": _safe(lambda: _df_to_records(t.cashflow)),
        "key_ratios": key_ratios,
        "company_name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "recent_dividends": _fetch_recent_dividends(t),
        "etf_details": _fetch_etf_details(t, info),
    }


@with_retry
def fetch_sector_benchmark_pe(sector: str | None) -> float | None:
    """A rough sector P/E benchmark so a ticker's own P/E isn't shown in
    isolation - the trailingPE of a representative sector ETF (see
    SECTOR_ETF_PROXY). Returns None for sectors with no mapped proxy (e.g.
    ETFs themselves have no 'sector') or if the lookup fails."""
    etf = SECTOR_ETF_PROXY.get(sector or "")
    if not etf:
        return None
    info = _safe(lambda: yf.Ticker(etf).info, {}) or {}
    return info.get("trailingPE")


def _rsi(closes: pd.Series, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    value = rsi.iloc[-1]
    return round(float(value), 2) if pd.notna(value) else None


@with_retry
def fetch_technicals(ticker: str, period: str = "1y") -> dict:
    t = yf.Ticker(ticker)
    history = _safe(lambda: t.history(period=period))
    if history is None or history.empty:
        return {"ticker": ticker, "error": "no price history available"}

    # While the market is open, today's bar has volume but no finalized
    # Close yet - drop trailing rows with a NaN close so stats are computed
    # on the last fully-closed trading day, not garbage.
    history = history[history["Close"].notna()]
    if history.empty:
        return {"ticker": ticker, "error": "no closed trading day available yet"}

    closes = history["Close"]
    volumes = history["Volume"]
    last_close = float(closes.iloc[-1])
    last_volume = float(volumes.iloc[-1])
    avg_volume = float(volumes.mean())

    return {
        "ticker": ticker,
        "last_close": round(last_close, 2),
        "last_volume": int(last_volume),
        "avg_volume": int(avg_volume),
        "volume_ratio": round(last_volume / avg_volume, 2) if avg_volume else None,
        "rsi_14": _rsi(closes),
        "sma_20": round(float(closes.tail(20).mean()), 2) if len(closes) >= 20 else None,
        "sma_50": round(float(closes.tail(50).mean()), 2) if len(closes) >= 50 else None,
        "sma_200": round(float(closes.tail(200).mean()), 2) if len(closes) >= 200 else None,
        "period_return_pct": round(
            (last_close / float(closes.iloc[0]) - 1) * 100, 2
        )
        if len(closes) > 1
        else None,
    }


@with_retry
def fetch_ownership_and_analysts(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = _safe(lambda: t.info, {}) or {}

    major_holders = _safe(lambda: t.major_holders)
    institutional_holders = _safe(lambda: t.institutional_holders)
    recommendations = _safe(lambda: t.recommendations)

    try:
        analyst_targets = t.analyst_price_targets
    except Exception:
        analyst_targets = None

    # t.institutional_holders only ever returns a top-10 table - len() of it
    # is NOT the true institution count (verified: gives 10 for AAPL, not the
    # real ~7762). The actual figure lives in major_holders' "institutionsCount".
    institutional_holders_count = None
    if major_holders is not None and not major_holders.empty:
        try:
            institutional_holders_count = major_holders.loc["institutionsCount", "Value"]
        except KeyError:
            institutional_holders_count = None

    return {
        "ticker": ticker,
        "major_holders": major_holders.to_dict() if major_holders is not None and not major_holders.empty else None,
        "institutional_holders_count": institutional_holders_count,
        "top_institutional_holders": (
            institutional_holders.head(5).to_dict("records")
            if institutional_holders is not None and not institutional_holders.empty
            else None
        ),
        "recent_recommendations": (
            recommendations.tail(6).to_dict("records")
            if recommendations is not None and not recommendations.empty
            else None
        ),
        "analyst_price_targets": analyst_targets,
        "short_percent_of_float": info.get("shortPercentOfFloat"),
        "short_ratio": info.get("shortRatio"),
        "shares_short": info.get("sharesShort"),
    }
