"""NiceGUI web UI: trigger research, watch it run live, filter and drill into
results, view logs.

Run with: python3 app.py
As a service: systemctl --user restart stocksearch.service
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import altair as alt
import pandas as pd
from nicegui import run, ui

import db
import job_state
from config import load_settings
from logging_config import configure_logging
from research import run_watchlist
from tools.yfinance_tools import fetch_trending_etfs, fetch_trending_tickers

DISCLAIMER = "Not financial advice — informational/research purposes only."

VERDICT_BADGE_COLOR = {"Bullish": "blue", "Neutral": "grey", "Bearish": "orange"}
VERDICT_ORDER = {"Bearish": -1, "Neutral": 0, "Bullish": 1}
VERDICT_CHART_COLORS = {"Bullish": "#3b7dd8", "Neutral": "#8a8f98", "Bearish": "#d87d3b"}
CONFIDENCE_SIZE = {"low": 60, "medium": 120, "high": 200}
DRAWER_MIN_WIDTH = 280
DRAWER_MAX_WIDTH = 900

# Columns whose filters are populated dropdowns built from the results
# currently in the grid, rather than free-text typing.
FILTER_FIELDS = [
    ("type", "Type"),
    ("status", "Status"),
    ("short_verdict", "Short Verdict"),
    ("short_confidence", "Short Confidence"),
    ("long_verdict", "Long Verdict"),
    ("long_confidence", "Long Confidence"),
]

settings = load_settings()
configure_logging(settings.log_path, settings.log_level)
db.init_db(settings.db_path)

# <nicegui-refreshable> (the wrapper @ui.refreshable puts around render_detail's
# content) ships with no CSS at all, so browsers default it to `display: inline`,
# which doesn't reliably act as a containing block for its `w-full` children -
# that's what let long text/JSON escape the drawer's width instead of wrapping.
# `display: contents` removes its own box from layout entirely, so children size
# themselves directly against the actual column around it.
ui.add_css("nicegui-refreshable { display: contents; }", shared=True)

GRID_COLUMN_DEFS = [
    {
        "field": "ticker",
        "headerName": "Ticker",
        "pinned": "left",
        ":cellRenderer": (
            "params => `<div>${params.value}"
            "<div style=\"font-size:0.75em;color:#888;\">${params.data.name || ''}</div></div>`"
        ),
    },
    {"field": "type", "headerName": "Type"},
    {"field": "sector", "headerName": "Sector/Category"},
    {"field": "status", "headerName": "Status"},
    {"field": "short_verdict", "headerName": "Short-term Verdict"},
    {"field": "short_confidence", "headerName": "Short-term Confidence"},
    {"field": "long_verdict", "headerName": "Long-term Verdict"},
    {"field": "long_confidence", "headerName": "Long-term Confidence"},
]


def _parse_tickers(raw: str) -> list[str]:
    seen, tickers = set(), []
    for part in re.split(r"[,\s]+", (raw or "").strip()):
        t = part.strip().upper()
        if t and t not in seen:
            seen.add(t)
            tickers.append(t)
    return tickers


def _get_raw_data(summaries, agent: str) -> dict | None:
    for row in summaries:
        if row["agent"] == agent and row["raw_data_json"]:
            try:
                return json.loads(row["raw_data_json"])
            except (TypeError, ValueError):
                return None
    return None


def _fmt_num(v, digits: int = 2) -> str | None:
    if v is None:
        return None
    try:
        return f"{float(v):,.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_pct(v, digits: int = 1) -> str | None:
    if v is None:
        return None
    try:
        return f"{float(v) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return str(v)


def _fmt_money(v) -> str | None:
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= div:
            return f"${v / div:.2f}{unit}"
    return f"${v:,.0f}"


def render_key_stats(summaries, overview: dict | None = None) -> None:
    """Clean, labeled stat tiles from data already fetched (fetch_fundamentals/
    fetch_technicals/fetch_ownership_and_analysts, captured directly in
    research.py) but previously only visible buried in the raw-JSON expander."""
    fundamentals_raw = _get_raw_data(summaries, "fundamentals")
    demand_raw = _get_raw_data(summaries, "demand")
    if not fundamentals_raw and not demand_raw:
        return

    key_ratios = (fundamentals_raw or {}).get("key_ratios", {})
    etf_details = (fundamentals_raw or {}).get("etf_details") or {}
    technicals = (demand_raw or {}).get("technicals", {})
    ownership = (demand_raw or {}).get("ownership", {})
    analyst_targets = ownership.get("analyst_price_targets") or {}

    fifty_two_low = key_ratios.get("fiftyTwoWeekLow")
    fifty_two_high = key_ratios.get("fiftyTwoWeekHigh")
    dividend_yield = key_ratios.get("dividendYield")
    pe_ratio = key_ratios.get("trailingPE")
    sector_pe = key_ratios.get("sector_pe")
    roe = key_ratios.get("returnOnEquity")
    next_earnings_date = (overview or {}).get("next_earnings_date")
    expense_ratio = etf_details.get("expense_ratio")

    tiles = [
        (
            "P/E",
            f"{pe_ratio:.1f} (sector ~{sector_pe:.1f})"
            if pe_ratio is not None and sector_pe is not None
            else _fmt_num(pe_ratio, 1),
        ),
        ("Fwd P/E", _fmt_num(key_ratios.get("forwardPE"), 1)),
        ("PEG", _fmt_num(key_ratios.get("pegRatio"), 2)),
        ("P/B", _fmt_num(key_ratios.get("priceToBook"), 1)),
        ("P/S", _fmt_num(key_ratios.get("priceToSalesTrailing12Months"), 2)),
        ("EV/EBITDA", _fmt_num(key_ratios.get("enterpriseToEbitda"), 1)),
        ("ROE", _fmt_pct(roe)),
        ("Debt/Equity", _fmt_num(key_ratios.get("debtToEquity"), 1)),
        ("Current Ratio", _fmt_num(key_ratios.get("currentRatio"), 2)),
        ("Quick Ratio", _fmt_num(key_ratios.get("quickRatio"), 2)),
        ("Market Cap", _fmt_money(key_ratios.get("marketCap"))),
        ("Beta", _fmt_num(key_ratios.get("beta"), 2)),
        ("Dividend Yield", f"{dividend_yield:.2f}%" if dividend_yield is not None else None),
        ("Payout Ratio", _fmt_pct(key_ratios.get("payoutRatio"))),
        (
            "52wk Range",
            f"${fifty_two_low:.0f} - ${fifty_two_high:.0f}"
            if fifty_two_low is not None and fifty_two_high is not None
            else None,
        ),
        ("Next Earnings", next_earnings_date.split(" ")[0] if next_earnings_date else None),
        ("RSI (14)", _fmt_num(technicals.get("rsi_14"), 1)),
        (
            "Vol vs Avg",
            f"{technicals['volume_ratio']:.2f}x" if technicals.get("volume_ratio") is not None else None,
        ),
        ("SMA 200", _fmt_num(technicals.get("sma_200"), 2)),
        ("Short % Float", _fmt_pct(ownership.get("short_percent_of_float"))),
        (
            "Institutional Holders",
            f"{int(ownership['institutional_holders_count']):,}"
            if ownership.get("institutional_holders_count") is not None
            else None,
        ),
        (
            "Analyst Target (mean)",
            f"${analyst_targets['mean']:.0f}" if analyst_targets.get("mean") is not None else None,
        ),
        (
            "Analyst Target Range",
            f"${analyst_targets['low']:.0f} - ${analyst_targets['high']:.0f}"
            if analyst_targets.get("low") is not None and analyst_targets.get("high") is not None
            else None,
        ),
        # ETF-only - None/absent for regular stocks, so these tiles simply
        # don't render outside the ETF Screener's results.
        ("Expense Ratio (ETF)", f"{expense_ratio:.3f}%" if expense_ratio is not None else None),
        ("Total Assets (ETF)", _fmt_money(etf_details.get("total_assets"))),
    ]
    tiles = [(label, value) for label, value in tiles if value]
    if not tiles:
        return

    ui.label("Key stats").classes("font-semibold")
    with ui.row().classes("w-full max-w-full flex-wrap gap-x-6 gap-y-2"):
        for label, value in tiles:
            with ui.column().classes("items-center gap-0"):
                ui.label(value).classes("text-lg font-bold")
                ui.label(label).classes("text-xs text-gray-500")

    if roe is not None and roe > 1.0:
        ui.label(
            "Note: unusually high ROE often reflects share buybacks shrinking "
            "the equity base, not financial distress."
        ).classes("text-xs text-gray-500 italic")


def _build_trend_chart(rows, horizon: str):
    data = [r for r in rows if r["horizon"] == horizon]
    if not data:
        return None

    df = pd.DataFrame(
        {
            "created_at": [pd.to_datetime(r["created_at"]) for r in data],
            "verdict": [r["verdict"] for r in data],
            "confidence": [r["confidence"] for r in data],
            "verdict_score": [VERDICT_ORDER[r["verdict"]] for r in data],
        }
    )

    return (
        alt.Chart(df)
        .mark_point(filled=True)
        .encode(
            x=alt.X("created_at:T", title="Run date"),
            y=alt.Y(
                "verdict_score:Q",
                title="Verdict",
                scale=alt.Scale(domain=[-1.5, 1.5]),
                axis=alt.Axis(values=[-1, 0, 1], labelExpr="{-1:'Bearish',0:'Neutral',1:'Bullish'}[datum.value]"),
            ),
            color=alt.Color(
                "verdict:N",
                scale=alt.Scale(domain=list(VERDICT_CHART_COLORS.keys()), range=list(VERDICT_CHART_COLORS.values())),
                legend=alt.Legend(title="Verdict"),
            ),
            size=alt.Size(
                "confidence:N",
                scale=alt.Scale(domain=list(CONFIDENCE_SIZE.keys()), range=list(CONFIDENCE_SIZE.values())),
                legend=alt.Legend(title="Confidence"),
            ),
            tooltip=["created_at:T", "verdict:N", "confidence:N"],
        )
        .properties(height=180, width="container")
    )


def _run_job_sync(tickers: list[str]) -> None:
    """Runs entirely in a worker thread (via run.io_bound) - opens its own
    DB connection, never shares one with the event-loop thread."""
    job_state.set_total(len(tickers))
    with db.connect(settings.db_path) as conn:
        def on_step(ticker: str, msg: str) -> None:
            job_state.note_activity(ticker, msg)

        def on_ticker_done(ticker: str, ticker_run_id: int, success: bool) -> None:
            job_state.mark_ticker_done()

        run_watchlist(
            tickers,
            settings,
            conn,
            on_step=on_step,
            on_ticker_done=on_ticker_done,
            should_cancel=job_state.is_cancel_requested,
        )


@ui.page("/")
def main_page() -> None:
    # Lets the results grid grow to fill the rest of the viewport (via
    # flex-grow) instead of sitting at a fixed height with empty page
    # space below it - falls back to a normal scrolling page if the
    # content above/below the grid ever needs more than one viewport.
    ui.query(".nicegui-content").classes("h-screen")

    ui.label("📈 stocksearch").classes("text-2xl font-bold")
    ui.label("Agentic fundamentals + sentiment + demand research").classes("text-sm text-gray-500")
    activity_label = ui.label("").classes("text-sm text-gray-500 italic")
    progress_bar = ui.linear_progress(value=0.0, show_value=False).classes("w-full")
    progress_bar.visible = False
    progress_label = ui.label("").classes("text-xs text-gray-500")
    def on_stop_click() -> None:
        job_state.request_cancel()
        stop_button.props("disable")
        ui.notify("Stopping after the current ticker finishes...", type="info")

    stop_button = ui.button("Stop scan", color="negative", on_click=on_stop_click)
    stop_button.visible = False

    buttons: list = []

    def set_buttons_disabled(disabled: bool) -> None:
        for b in buttons:
            b.props("disable" if disabled else "remove=disable")

    async def launch(tickers: list[str]) -> None:
        if not tickers:
            ui.notify("Enter at least one ticker.", type="warning")
            return
        if not job_state.try_start():
            ui.notify("A research job is already running.", type="warning")
            return
        set_buttons_disabled(True)
        stop_button.visible = True
        stop_button.props(remove="disable")
        progress_bar.visible = True
        try:
            await run.io_bound(_run_job_sync, tickers)
        finally:
            job_state.mark_finished()
            set_buttons_disabled(False)
            stop_button.visible = False
            progress_bar.visible = False
            refresh_grid()

    with ui.tabs() as tabs:
        tab_run = ui.tab("Run Research")
        tab_screener = ui.tab("Screener")
        tab_etf = ui.tab("ETF Screener")
        tab_logs = ui.tab("Logs")

    with ui.tab_panels(tabs, value=tab_run).classes("w-full"):
        with ui.tab_panel(tab_run):
            ticker_input = ui.input("Ticker(s)", placeholder="e.g. AAPL, TSLA MSFT").classes("w-full")

            async def on_run_click() -> None:
                await launch(_parse_tickers(ticker_input.value))

            run_button = ui.button("Run", on_click=on_run_click)
            buttons.append(run_button)

        with ui.tab_panel(tab_screener):
            ui.label('Discovers currently-trending stocks from Yahoo Finance\'s "most actives" list.')
            stock_count = ui.slider(min=5, max=50, value=25, step=5).props("label-always")

            async def on_stock_scan_click() -> None:
                if job_state.is_running():
                    ui.notify("A research job is already running.", type="warning")
                    return
                try:
                    tickers = await run.io_bound(fetch_trending_tickers, int(stock_count.value))
                except Exception as e:  # noqa: BLE001 - surface cleanly, don't crash the page
                    ui.notify(f"Could not fetch trending stocks: {e}", type="negative")
                    return
                if not tickers:
                    ui.notify("No trending stocks found right now.", type="warning")
                    return
                ui.notify(f"Found {len(tickers)} trending stocks: {', '.join(tickers)}")
                await launch(tickers)

            stock_scan_button = ui.button("Scan market", on_click=on_stock_scan_click)
            buttons.append(stock_scan_button)

        with ui.tab_panel(tab_etf):
            ui.label('Discovers currently-trending ETFs from Yahoo Finance\'s "top ETFs" list.')
            ui.label(
                "Note: ETFs don't file income statements/balance sheets like companies do, "
                "so the fundamentals agent's analysis will be thin for these - sentiment and "
                "demand signals carry more weight here."
            ).classes("text-xs text-gray-500")
            etf_count = ui.slider(min=5, max=50, value=25, step=5).props("label-always")

            async def on_etf_scan_click() -> None:
                if job_state.is_running():
                    ui.notify("A research job is already running.", type="warning")
                    return
                try:
                    tickers = await run.io_bound(fetch_trending_etfs, int(etf_count.value))
                except Exception as e:  # noqa: BLE001 - surface cleanly, don't crash the page
                    ui.notify(f"Could not fetch trending ETFs: {e}", type="negative")
                    return
                if not tickers:
                    ui.notify("No trending ETFs found right now.", type="warning")
                    return
                ui.notify(f"Found {len(tickers)} trending ETFs: {', '.join(tickers)}")
                await launch(tickers)

            etf_scan_button = ui.button("Scan market", on_click=on_etf_scan_click)
            buttons.append(etf_scan_button)

        with ui.tab_panel(tab_logs):
            log_filter = ui.input("Filter (substring match)").classes("w-full")
            logs_code = ui.code("", language=None).classes("w-full h-64 overflow-auto")

            def refresh_logs() -> None:
                log_path = Path(settings.log_path)
                if not log_path.exists():
                    logs_code.set_content("(no logs yet - run some research first)")
                    return
                full_text = log_path.read_text()
                lines = full_text.splitlines()[-300:]
                needle = (log_filter.value or "").lower()
                if needle:
                    lines = [line for line in lines if needle in line.lower()]
                logs_code.set_content("\n".join(lines) or "(no matching lines)")

            def download_logs() -> None:
                log_path = Path(settings.log_path)
                if log_path.exists():
                    ui.download(log_path.read_bytes(), "stocksearch.log")
                else:
                    ui.notify("No log file yet.", type="warning")

            with ui.row():
                ui.button("Refresh", on_click=refresh_logs)
                ui.button("Download full log", on_click=download_logs)

            refresh_logs()

    ui.separator()
    ui.label("Results").classes("text-xl font-bold")

    filter_selects: dict[str, ui.select] = {}
    with ui.row().classes("w-full flex-wrap gap-4"):
        for field, label in FILTER_FIELDS:
            select = ui.select([], label=label, multiple=True, clearable=True).classes("min-w-36").props(
                "use-chips"
            )
            select.on_value_change(lambda: refresh_grid())
            filter_selects[field] = select

    grid = ui.aggrid(
        {
            "columnDefs": GRID_COLUMN_DEFS,
            "rowData": [],
            "defaultColDef": {"filter": True, "sortable": True, "resizable": True, "floatingFilter": False},
            ":getRowId": "params => params.data.ticker",
            "rowSelection": "single",
        },
        html_columns=[0],
    ).classes("w-full flex-grow min-h-96")

    drawer = ui.right_drawer(value=False, fixed=True, bordered=True).props(
        f"width={DRAWER_MAX_WIDTH}"
    ).classes("overflow-x-hidden")
    with drawer:
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Details").classes("text-lg font-bold")
            with ui.row().classes("items-center gap-2"):
                ui.icon("swap_horiz").classes("text-gray-400")
                width_slider = ui.slider(
                    min=DRAWER_MIN_WIDTH, max=DRAWER_MAX_WIDTH, step=20, value=DRAWER_MAX_WIDTH
                ).classes("w-32")
        width_slider.on_value_change(lambda e: drawer.props(f"width={int(e.value)}"))
        detail_container = ui.column().classes("w-full max-w-full")

    @ui.refreshable
    def render_detail(ticker_run_id: int, ticker: str) -> None:
        with db.connect(settings.db_path) as conn:
            reports = db.reports_for_ticker_run(conn, ticker_run_id)
            summaries = db.signal_summaries_for_ticker_run(conn, ticker_run_id)
            trend_rows = db.report_trend(conn, ticker)
        overview = db.parse_overview(summaries) or {}

        ui.markdown(f"#### {overview.get('name') or ticker}").classes("w-full break-words")
        subtitle = " · ".join(
            filter(
                None,
                [
                    overview.get("quote_type"),
                    overview.get("sector"),
                    overview.get("industry"),
                    overview.get("category"),
                    overview.get("fund_family"),
                ],
            )
        )
        if subtitle:
            ui.label(subtitle).classes("w-full break-words text-sm text-gray-500")
        if overview.get("description"):
            ui.label(overview["description"]).classes("w-full break-words")

        for r in reports:
            with ui.card().classes("w-full max-w-full"):
                label = "Short-term (1-4 wks)" if r["horizon"] == "short_term" else "Long-term (2-4 qtrs)"
                with ui.row().classes("w-full items-center"):
                    ui.label(label).classes("font-semibold")
                    ui.badge(f"{r['verdict']} ({r['confidence']})", color=VERDICT_BADGE_COLOR.get(r["verdict"], "grey"))
                ui.markdown(r["reasoning"]).classes("w-full break-words")

        render_key_stats(summaries, overview)

        for horizon, horizon_label in (("short_term", "Short-term trend"), ("long_term", "Long-term trend")):
            chart = _build_trend_chart(trend_rows, horizon)
            if chart is not None:
                ui.label(horizon_label).classes("font-semibold mt-2")
                ui.altair(chart).classes("w-full")

        ui.label("Metrics over time").classes("font-semibold mt-2")
        metric_chart_container = ui.column().classes("w-full")

        def render_metric_chart(field: str) -> None:
            metric_chart_container.clear()
            with metric_chart_container:
                with db.connect(settings.db_path) as metric_conn:
                    history = db.metric_history(metric_conn, ticker, field)
                if len(history) < 2:
                    ui.label("Not enough history yet for this metric (need 2+ runs).").classes(
                        "text-sm text-gray-500"
                    )
                    return
                df = pd.DataFrame(
                    {
                        "recorded_at": [pd.to_datetime(r["recorded_at"]) for r in history],
                        "value": [r["value"] for r in history],
                    }
                )
                chart = (
                    alt.Chart(df)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("recorded_at:T", title="Run date"),
                        y=alt.Y("value:Q", title=db.METRIC_FIELDS[field]),
                        tooltip=["recorded_at:T", "value:Q"],
                    )
                    .properties(height=180, width="container")
                )
                ui.altair(chart).classes("w-full")

        metric_select = ui.select(db.METRIC_FIELDS, value="price", label="Metric").classes("w-48")
        metric_select.on_value_change(lambda: render_metric_chart(metric_select.value))
        render_metric_chart("price")

        with db.connect(settings.db_path) as history_conn:
            recent = db.recent_metrics(history_conn, ticker)
        if len(recent) > 1:
            ui.label("Recent runs").classes("font-semibold mt-2")
            ui.table(
                rows=[
                    {
                        "recorded_at": r["recorded_at"],
                        "date": r["recorded_at"][:10],
                        "price": _fmt_num(r["price"], 2) or "-",
                        "pe_ratio": _fmt_num(r["pe_ratio"], 1) or "-",
                        "rsi_14": _fmt_num(r["rsi_14"], 1) or "-",
                    }
                    for r in recent
                ],
                columns=[
                    {"name": "date", "label": "Date", "field": "date"},
                    {"name": "price", "label": "Price", "field": "price"},
                    {"name": "pe_ratio", "label": "P/E", "field": "pe_ratio"},
                    {"name": "rsi_14", "label": "RSI", "field": "rsi_14"},
                ],
                row_key="recorded_at",  # full timestamp, not just the date - stays
                # unique even when multiple runs happen on the same calendar day
            ).classes("w-full")
        else:
            ui.label("Recent runs").classes("font-semibold mt-2")
            ui.label("Not enough history yet (need 2+ runs).").classes("text-sm text-gray-500")

        for s in summaries:
            with ui.expansion(s["agent"].capitalize()).classes("w-full max-w-full"):
                ui.markdown(s["summary_text"]).classes("w-full break-words")
                if s["raw_data_json"]:
                    with ui.expansion("Raw data").classes("w-full max-w-full"):
                        try:
                            pretty_json = json.dumps(json.loads(s["raw_data_json"]), indent=2)
                        except (TypeError, ValueError):
                            pretty_json = s["raw_data_json"]
                        ui.code(pretty_json, language="json").classes("w-full max-w-full overflow-x-auto")

    def open_detail_panel(row_data: dict) -> None:
        detail_container.clear()
        with detail_container:
            render_detail(row_data["ticker_run_id"], row_data["ticker"])
        drawer.value = True

    grid.on("cellClicked", lambda e: open_detail_panel(e.args["data"]))

    last_rendered_rows: list[dict] | None = None

    def refresh_grid() -> None:
        nonlocal last_rendered_rows
        with db.connect(settings.db_path) as conn:
            rows = db.grid_rows(conn)

        for field, _label in FILTER_FIELDS:
            distinct = sorted({r[field] for r in rows if r.get(field)})
            select = filter_selects[field]
            if select.options != distinct:
                kept = [v for v in (select.value or []) if v in distinct]
                select.set_options(distinct, value=kept)

        filtered = rows
        for field, _label in FILTER_FIELDS:
            selected = filter_selects[field].value
            if selected:
                filtered = [r for r in filtered if r.get(field) in selected]

        # ag-Grid's update() destroys and recreates the whole grid instance -
        # skip it unless the rendered rows actually changed, so a poll tick
        # with nothing new doesn't reset scroll position/flicker every 1.5s.
        if filtered == last_rendered_rows:
            return
        last_rendered_rows = filtered
        grid.options["rowData"] = filtered
        grid.update()

    def poll_tick() -> None:
        running = job_state.is_running()
        set_buttons_disabled(running)
        stop_button.visible = running
        progress_bar.visible = running
        activity = job_state.activity_snapshot()
        activity_label.set_text(" | ".join(f"{t}: {m}" for t, m in activity.items()) if running else "")
        completed, total = job_state.progress_snapshot()
        if running and total:
            progress_bar.set_value(completed / total)
            progress_label.set_text(f"{completed} / {total} tickers done")
        else:
            progress_label.set_text("")
        refresh_grid()
        refresh_logs()

    refresh_grid()
    ui.timer(1.5, poll_tick)

    ui.separator()
    ui.label(DISCLAIMER).classes("text-xs text-gray-400")


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(host="0.0.0.0", port=8501, title="stocksearch", reload=False, show=False, uvicorn_logging_level="warning")
