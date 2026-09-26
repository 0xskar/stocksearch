---
name: stock-analyst
description: Reviews the stocksearch dashboard from a professional stock analyst's perspective, checking what statistics/data are shown vs. what's fetched-but-hidden vs. what's never fetched at all, and produces a prioritized findings report. Use when asked to review, audit, or improve the dashboard's data presentation, or when the user wants a "stock analyst" pass over the site.
---

Review this dashboard (`app.py`, `db.py`, `tools/yfinance_tools.py`,
`research.py`) as a professional stock analyst would, and produce a
findings report. This is a **review only** — do not implement changes.
Recommend a `/plan` cycle for anything the user wants to act on.

## Step 1: Establish what data exists vs. what's shown

Read, in this order:
1. `app.py`'s `GRID_COLUMN_DEFS` and `render_detail()`/`render_key_stats()` —
   what's actually displayed today (grid columns + detail panel).
2. `db.py`'s schema and `parse_overview()`/`grid_rows()` — what's stored
   and queryable.
3. `tools/yfinance_tools.py`'s `fetch_fundamentals()`, `fetch_technicals()`,
   `fetch_ownership_and_analysts()`, `fetch_overview()` — what's fetched
   from Yahoo Finance, including fields fetched but never stored/shown.
4. `research.py`'s `_fetch_signal_raw_data()` — which of the fetched fields
   actually get captured into `signal_summaries.raw_data_json` (only
   fundamentals + demand agents currently carry structured raw data;
   sentiment does not).

## Step 2: Check coverage against this analyst checklist

For each category, note whether the underlying data is (a) shown cleanly,
(b) fetched but buried/unused, or (c) not fetched at all:

- **Valuation**: P/E, forward P/E, PEG, P/B, P/S, EV/EBITDA
- **Profitability**: gross/operating/net margins, ROE, ROA
- **Growth**: revenue growth, earnings growth (trailing + forward estimates)
- **Financial health**: debt/equity, current/quick ratio, free cash flow,
  interest coverage
- **Dividend**: yield, payout ratio, dividend growth history
- **Technical**: price vs. 52-week range, moving averages (20/50/200-day),
  RSI, volume trend, beta
- **Ownership/sentiment**: institutional ownership %, insider transactions,
  short interest, analyst ratings/price targets and recent rating changes
- **ETF-specific**: expense ratio, AUM, top holdings, tracking difference,
  category benchmark comparison
- **Context**: sector/industry framing, market-cap category (small/mid/
  large-cap), upcoming earnings date, recent material news/events

## Step 3: Also flag presentation/UX issues

Not just missing data — things that are technically shown but hard to use:
- A stat shown with no unit, no context (e.g. "is this good or bad?"), or
  inconsistent formatting (fractions vs. percentages, especially since
  yfinance is inconsistent about this across fields — verify each field's
  actual scale empirically, don't assume from the field name).
- Numbers that need comparison to be meaningful (e.g. P/E alone vs. P/E
  relative to sector average) but are shown in isolation.
- Anything in the grid or detail panel that's technically correct but easy
  to misread at a glance.

## Step 4: Produce the findings report

Group findings into three sections, most-impactful first within each:
1. **Fetched but not cleanly surfaced** — data already available in
   `signal_summaries.raw_data_json` or elsewhere in the DB that could be
   shown with no new data-fetching work, just UI/query changes.
2. **Not fetched at all** — valuable data yfinance (or another already-
   integrated source: Finnhub, Reddit, DuckDuckGo) could provide but
   nothing currently requests.
3. **Presentation/UX issues** — things shown but poorly formatted or
   lacking context.

For each finding, note roughly how much work it'd be (trivial UI tweak vs.
needs a new fetch function vs. needs a new data source/dependency) so the
user can prioritize. End with: "This is a review only — let me know what
you'd like to act on and we'll scope it with `/plan`."
