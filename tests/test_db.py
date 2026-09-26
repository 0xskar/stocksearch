import time
from datetime import datetime, timedelta, timezone

import db


def test_schema_insert_and_query_round_trip(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    with db.connect(db_path) as conn:
        run_id = db.start_run(conn, ["AAPL"])
        ticker_run_id = db.start_ticker_run(conn, run_id, "AAPL")

        db.save_report_row(
            conn, ticker_run_id, "AAPL", "short_term", "Bullish", "medium", "Good news."
        )
        db.save_report_row(
            conn, ticker_run_id, "AAPL", "long_term", "Neutral", "low", "Mixed fundamentals."
        )
        for agent in ("fundamentals", "sentiment", "demand"):
            db.save_signal_summary(conn, ticker_run_id, "AAPL", agent, f"{agent} summary")

        db.finish_ticker_run(conn, ticker_run_id, "completed")
        db.finish_run(conn, run_id, "completed")

    with db.connect(db_path) as conn:
        assert db.distinct_tickers(conn) == ["AAPL"]

        latest_id = db.latest_ticker_run_id(conn, "AAPL")
        assert latest_id == ticker_run_id

        reports = db.reports_for_ticker_run(conn, latest_id)
        assert {r["horizon"] for r in reports} == {"short_term", "long_term"}

        summaries = db.signal_summaries_for_ticker_run(conn, latest_id)
        assert {r["agent"] for r in summaries} == {"fundamentals", "sentiment", "demand"}

        trend = db.report_trend(conn, "AAPL")
        assert len(trend) == 2

        overview = db.latest_verdict_per_ticker(conn)
        assert {(r["ticker"], r["horizon"]) for r in overview} == {
            ("AAPL", "short_term"),
            ("AAPL", "long_term"),
        }


def test_high_confidence_bullish_for_run_scopes_to_one_run(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    with db.connect(db_path) as conn:
        # Run 1: AAPL (medium-confidence bullish, doesn't qualify) + MSFT (qualifies).
        run_id_1 = db.start_run(conn, ["AAPL", "MSFT"])
        aapl_run = db.start_ticker_run(conn, run_id_1, "AAPL")
        db.save_report_row(conn, aapl_run, "AAPL", "short_term", "Bullish", "medium", "Mild.")
        msft_run = db.start_ticker_run(conn, run_id_1, "MSFT")
        db.save_report_row(conn, msft_run, "MSFT", "long_term", "Bullish", "high", "Strong.")
        db.save_report_row(conn, msft_run, "MSFT", "short_term", "Neutral", "high", "Flat.")

        # Run 2: TSLA also qualifies, but in a separate run - must not leak into run 1's results.
        run_id_2 = db.start_run(conn, ["TSLA"])
        tsla_run = db.start_ticker_run(conn, run_id_2, "TSLA")
        db.save_report_row(conn, tsla_run, "TSLA", "long_term", "Bullish", "high", "Also strong.")

        results_1 = db.high_confidence_bullish_for_run(conn, run_id_1)
        assert [(r["ticker"], r["horizon"]) for r in results_1] == [("MSFT", "long_term")]

        results_2 = db.high_confidence_bullish_for_run(conn, run_id_2)
        assert [(r["ticker"], r["horizon"]) for r in results_2] == [("TSLA", "long_term")]


def test_grid_rows_includes_in_progress_and_latest_completed(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    with db.connect(db_path) as conn:
        # AAPL: an older completed run, then a newer still-running run - the
        # grid should reflect the NEWER (running) one, not the older verdicts.
        run_id = db.start_run(conn, ["AAPL", "MSFT"])
        old_aapl_run = db.start_ticker_run(conn, run_id, "AAPL")
        db.save_report_row(conn, old_aapl_run, "AAPL", "long_term", "Bearish", "low", "Old.")
        db.finish_ticker_run(conn, old_aapl_run, "completed")

        new_aapl_run = db.start_ticker_run(conn, run_id, "AAPL")  # still 'running', no reports yet

        # MSFT: a completed run with both horizons and fundamentals overview data.
        msft_run = db.start_ticker_run(conn, run_id, "MSFT")
        db.save_report_row(conn, msft_run, "MSFT", "short_term", "Neutral", "medium", "Flat.")
        db.save_report_row(conn, msft_run, "MSFT", "long_term", "Bullish", "high", "Strong.")
        db.save_signal_summary(
            conn, msft_run, "MSFT", "fundamentals", "MSFT summary",
            {"name": "Microsoft Corp.", "quote_type": "EQUITY", "sector": "Technology"},
        )
        db.finish_ticker_run(conn, msft_run, "completed")

        rows = {r["ticker"]: r for r in db.grid_rows(conn)}

        assert rows["AAPL"]["ticker_run_id"] == new_aapl_run  # latest, not the completed older one
        assert rows["AAPL"]["status"] == "running"
        assert rows["AAPL"]["long_verdict"] == ""  # no reports yet on the new run

        assert rows["MSFT"]["status"] == "completed"
        assert rows["MSFT"]["type"] == "EQUITY"
        assert rows["MSFT"]["sector"] == "Technology"
        assert rows["MSFT"]["short_verdict"] == "Neutral"
        assert rows["MSFT"]["long_verdict"] == "Bullish"
        assert rows["MSFT"]["long_confidence"] == "high"


def test_ticker_metrics_round_trip_and_history_ordering(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    with db.connect(db_path) as conn:
        run_id = db.start_run(conn, ["AAPL"])

        tr1 = db.start_ticker_run(conn, run_id, "AAPL")
        db.save_ticker_metrics(conn, tr1, "AAPL", {"price": 100.0, "pe_ratio": 20.0})

        tr2 = db.start_ticker_run(conn, run_id, "AAPL")
        db.save_ticker_metrics(conn, tr2, "AAPL", {"price": 110.0, "pe_ratio": None})

        price_history = db.metric_history(conn, "AAPL", "price")
        assert [r["value"] for r in price_history] == [100.0, 110.0]

        # None values are excluded, not returned as a None data point
        pe_history = db.metric_history(conn, "AAPL", "pe_ratio")
        assert [r["value"] for r in pe_history] == [20.0]

        try:
            db.metric_history(conn, "AAPL", "not_a_real_column")
            assert False, "should have rejected an unknown field name"
        except ValueError:
            pass


def test_latest_ticker_metrics_returns_most_recent_row(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    with db.connect(db_path) as conn:
        assert db.latest_ticker_metrics(conn, "AAPL") is None

        run_id = db.start_run(conn, ["AAPL"])
        tr1 = db.start_ticker_run(conn, run_id, "AAPL")
        db.save_ticker_metrics(conn, tr1, "AAPL", {"price": 100.0, "pe_ratio": 20.0})

        tr2 = db.start_ticker_run(conn, run_id, "AAPL")
        db.save_ticker_metrics(conn, tr2, "AAPL", {"price": 110.0, "pe_ratio": 21.0})

        latest = db.latest_ticker_metrics(conn, "AAPL")
        assert latest["price"] == 110.0
        assert latest["pe_ratio"] == 21.0
        assert set(latest.keys()) == set(db.METRIC_FIELDS.keys())


def test_delete_duplicate_ticker_metrics_collapses_consecutive_matches_only(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    with db.connect(db_path) as conn:
        run_id = db.start_run(conn, ["AAPL"])

        def save(price):
            # Guarantees strictly-increasing recorded_at timestamps so
            # ORDER BY recorded_at reflects call order deterministically -
            # without this, back-to-back calls could tie at microsecond
            # resolution and make the test flaky.
            time.sleep(0.01)
            tr = db.start_ticker_run(conn, run_id, "AAPL")
            db.save_ticker_metrics(conn, tr, "AAPL", {"price": price})

        save(100.0)  # kept - first
        save(100.0)  # duplicate of the kept row - deleted
        save(105.0)  # genuinely changed - kept
        save(100.0)  # matches an OLDER row, but not the adjacent kept one - kept (reverted, not a dup)
        save(100.0)  # duplicate of the immediately preceding kept row - deleted

        deleted = db.delete_duplicate_ticker_metrics(conn)
        assert deleted == 2

        remaining = [
            r["price"]
            for r in conn.execute(
                "SELECT price FROM ticker_metrics WHERE ticker='AAPL' ORDER BY recorded_at"
            )
        ]
        assert remaining == [100.0, 105.0, 100.0]


def test_tickers_scanned_today_uses_local_calendar_date(tmp_path):
    db_path = str(tmp_path / "test.db")
    db.init_db(db_path)

    with db.connect(db_path) as conn:
        run_id = db.start_run(conn, ["AAPL", "MSFT", "TSLA"])

        aapl_run = db.start_ticker_run(conn, run_id, "AAPL")
        db.finish_ticker_run(conn, aapl_run, "completed")  # finishes "now" - today

        msft_run = db.start_ticker_run(conn, run_id, "MSFT")
        db.finish_ticker_run(conn, msft_run, "completed")
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        conn.execute("UPDATE ticker_runs SET finished_at = ? WHERE id = ?", (yesterday, msft_run))

        tsla_run = db.start_ticker_run(conn, run_id, "TSLA")
        db.finish_ticker_run(conn, tsla_run, "failed")  # not completed - never counts

        scanned = db.tickers_scanned_today(conn)
        assert scanned == {"AAPL"}
