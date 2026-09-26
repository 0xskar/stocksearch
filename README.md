# stocksearch

An agentic AI research tool for US-listed stocks. Given a ticker, it dispatches
three specialist AI subagents — fundamentals, sentiment, and demand — and
synthesizes a qualitative growth verdict across two time horizons.

**Not financial advice — informational/research purposes only.**

## What it does

Every report opens with a plain factual overview of what the ticker actually
is — company name, sector/industry (or fund category/family for an ETF), and
its business/fund description — fetched directly rather than via the LLM, so
it's always present regardless of what an agent's prose happens to mention.

For each ticker, three subagents run sequentially against a locally-hosted
model (one GPU, one loaded model — there's no benefit to running them
concurrently), then an orchestration step synthesizes their findings:

- **Fundamentals** — income statement, balance sheet, cash flow, and key
  valuation/profitability ratios, via [yfinance](https://pypi.org/project/yfinance/).
- **Sentiment** — recent company news ([Finnhub](https://finnhub.io)), retail
  investor chatter (Reddit via PRAW), and general web commentary (DuckDuckGo
  search).
- **Demand** — trading volume/momentum, institutional ownership, short
  interest, and analyst price targets, via yfinance.

Every research run also logs ~28 numeric fundamentals/technicals (price,
P/E, PEG, current/quick ratio, EV/EBITDA, P/S, payout ratio, SMA-20/50/200,
margins, RSI, analyst targets, ETF expense ratio/AUM, etc.) into a dedicated
`ticker_metrics` table — a proper time series per ticker, not just the
latest snapshot. The detail panel's "Metrics over time" chart lets you pick
any of them and see how that ticker has changed across your research runs
(needs 2+ runs for a given ticker to show a trend), and a "Recent runs"
table shows the last several runs' headline numbers together. The key-stats
panel also shows next earnings date, a sector P/E benchmark alongside the
ticker's own P/E, and a note when ROE looks unusually high (often a
buyback effect, not distress).

All agents run on a local [Ollama](https://ollama.com) model
(`qwen2.5:7b-instruct` by default) — no cloud API key required.

The orchestrator then records two verdicts (each `Bullish`/`Bearish`/`Neutral`
with a `low`/`medium`/`high` confidence and written reasoning):

- **Short-term** (1–4 weeks) — weighted toward sentiment and demand.
- **Long-term** (2–4 quarters) — weighted toward fundamentals.

There are deliberately no numeric price targets and no single composite
score — verdicts are qualitative, to avoid implying false precision from an
LLM-driven analysis.

Every run is persisted to a local SQLite database so history accumulates
across manual runs, with change-detection so re-scanning an unchanged
ticker doesn't write a redundant duplicate row. A [NiceGUI](https://nicegui.io)
web UI lets you trigger runs, browse every researched ticker in a results
grid, drill into a full report (verdicts, key stats, trend charts) in a
details panel, and read the logs — all from one place.

## Setup

Requires an NVIDIA GPU with enough VRAM for a 7B model at Q4 quantization
(~5GB) — a 12GB card has comfortable headroom.

```bash
# 1. Install and start Ollama (https://ollama.com), then pull the model:
ollama pull qwen2.5:7b-instruct
ollama serve   # skip if the Ollama desktop app already runs it in the background

# 2. Set up the Python app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` works out of the box against a default local Ollama install. Optionally fill in:

- `FINNHUB_API_KEY` — free tier at https://finnhub.io/dashboard.
  Without it, the sentiment agent skips Finnhub news and relies on DuckDuckGo
  web search instead.
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` — register a "script" app at
  https://www.reddit.com/prefs/apps to get these. Without them, the sentiment
  agent skips Reddit and relies on DuckDuckGo web search instead.
- `REDDIT_USER_AGENT` — required if using Reddit; any descriptive string
  works, e.g. `stocksearch/0.1 by u/<your-reddit-username>`.

## Usage

### Web UI (recommended)

```bash
python3 app.py
```

Then open `http://localhost:8501`. Four tabs, all reading/writing the same
local SQLite database:

- **Run Research** — enter one or more tickers (e.g. `AAPL, TSLA MSFT`) and
  click Run. Each ticker gets a live step-by-step status panel while its
  subagents run. This is a single-user local tool, not a job queue — one
  scan runs at a time (~35s per ticker).
- **Screener** — no tickers to type: pulls Yahoo Finance's current "most
  actives" list (a genuinely dynamic, self-discovered set, not a hardcoded
  universe), researches each one, and surfaces any Bullish + high-confidence
  results. Scanning N tickers takes roughly N x 30s, sequentially — a 25-ticker
  scan is ~12-15 minutes.
- **ETF Screener** — same flow, sourced from Yahoo's "top ETFs" list instead.
  ETFs don't file income statements/balance sheets, so the fundamentals
  agent's analysis is thin for these (it says so rather than making things
  up) — sentiment and demand signals carry more weight here.
- **Logs** — tail `logs/stocksearch.log` for debugging, with a substring
  filter and a full-file download button.

Below the tabs, a **results grid** lists every ticker ever researched, with
dropdown filters (type, status, verdict, confidence) — click any row to open
its full report in a details panel: verdicts and reasoning, key stats,
verdict-trend charts, a "metrics over time" chart for ~30 tracked
fundamentals/technicals, a recent-runs table, and the raw per-agent data.

### CLI (scriptable alternative)

```bash
# Single ticker
python research.py AAPL

# Watchlist (multiple tickers in one invocation, processed sequentially)
python research.py AAPL TSLA MSFT

# Or from a file, one ticker per line
python research.py --watchlist-file watchlist.example.txt
```

Each run prints the verdicts and signal summaries to the console and writes
them to the same `data/stocksearch.db` and `logs/stocksearch.log` the web UI
uses — the CLI and web UI share one execution path (`research.py`'s
`run_watchlist()`), so results from either are visible in the other.

## Scope (v1)

- US-listed stocks only — no crypto, no international listings, no
  ETF-specific fundamentals handling.
- Manual invocation only — no scheduler/cron. Run it whenever you want a
  fresh data point; history builds up from your own runs.
- Options flow (unusual activity, put/call ratio) is deliberately **not**
  built — it needs a paid data provider (e.g. Polygon, Tradier). The
  extension point is documented in `agents/demand.py`.

## Notes / known quirks

- **yfinance** is an unofficial Yahoo Finance client — some fields
  occasionally come back missing depending on the ticker or a Yahoo-side
  change; the code degrades individual missing fields to `None` rather than
  failing the whole request.
- **Verdicts are non-deterministic by design.** Since they're LLM-written
  qualitative judgments, re-running the same ticker close in time may
  produce slightly different wording or even a different confidence level.
  This is expected, not a bug.
- **Local model quality is a real tradeoff.** A 7B model's tool-calling and
  reasoning are noticeably less reliable than a large cloud model's — expect
  occasional thin summaries or a synthesis retry. Swap `OLLAMA_MODEL` in
  `.env` for a larger model (e.g. `qwen2.5:14b-instruct`) if your GPU has the
  VRAM headroom and you want stronger reasoning.
- **DuckDuckGo search** (via the `ddgs` package) is unofficial and can
  occasionally be rate-limited or blocked.
- If a ticker in a watchlist run fails (bad symbol, API error, etc.), only
  that ticker's run is marked failed — the rest of the watchlist continues
  (in both the CLI and the web UI's Run Research tab).
- Logs default to `INFO` (high-level progress only). Set `LOG_LEVEL=DEBUG`
  in `.env` to also log every tool call's arguments and result size —
  useful when debugging why an agent's summary looks off.

## Tests

```bash
pip install pytest
pytest
```

Covers report/summary payload validation (`tests/test_synthesis.py`) and the
SQLite schema/query round-trip (`tests/test_db.py`). Not covered by design:
mocked end-to-end yfinance/Finnhub/Reddit calls, and verdict "quality" —
both are out of scope for unit testing here.
