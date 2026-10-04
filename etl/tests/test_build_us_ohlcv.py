import unittest
from datetime import datetime
from unittest.mock import patch

import duckdb
import pandas as pd

from scripts.build_us_ohlcv import (
    ETF_TICKERS,
    NEW_YORK,
    UniverseItem,
    _insert_rows,
    _replace_split_history,
    apply_share_history,
    audit_gaps,
    bar_cutoff_day,
    carry_forward_shares,
    ensure_etf,
    ensure_ohlcv,
    ensure_schema,
    ensure_shares,
    fetch_batch,
    load_universe,
    plan_fetch,
    repair_gaps,
    to_rows,
)


def _frame(ticker_days, *, splits=None, dividends=None):
    """ticker_days: ticker -> [(YYYY-MM-DD, close)]"""
    pieces = {}
    splits = splits or {}
    dividends = dividends or {}
    for ticker, values in ticker_days.items():
        index = pd.to_datetime([value[0] for value in values])
        close = [value[1] for value in values]
        pieces[ticker] = pd.DataFrame(
            {
                "Open": close,
                "High": [value + 1 for value in close],
                "Low": [value - 1 for value in close],
                "Close": close,
                "Adj Close": close,
                "Volume": [100] * len(close),
                "Dividends": [dividends.get((ticker, day), 0.0) for day, _ in values],
                "Stock Splits": [splits.get((ticker, day), 0.0) for day, _ in values],
            },
            index=index,
        )
    return pd.concat(pieces, axis=1)


def _row(day, ticker, close=100.0):
    return (day, ticker, "NASDAQ", close, close, close, close, 100, close * 100, None, None)


def _etf_row(day, ticker="SPY"):
    return (day, ticker, 100.0, 101.0, 99.0, 100.0, 100)


class TestUniverse(unittest.TestCase):
    def test_t6_common_stock_filter_no_etfs(self):
        text = """Nasdaq Traded|Symbol|Security Name|Listing Exchange|ETF|Test Issue
Y|AAA|Alpha Common Stock|Q|N|N
Y|SPAC|Example Acquisition Corp|N|N|N
Y|ETF1|Example ETF|P|Y|N
Y|TEST|Test Security|Q|N|Y
Y|WAR|Example Warrant|Q|N|N
Y|UNIT|Example Units|Q|N|N
Y|RIGHT|Example Rights|Q|N|N
Y|PREF|Example Preferred Stock|N|N|N
Y|DEP|Example Depositary Shares|N|N|N
Y|BAD$|Bad Symbol|N|N|N
Y|BRK.B|Berkshire Hathaway Inc Class B Common Stock|N|N|N
Y|ODD.XY|Odd Suffix|N|N|N
File Creation Time: 20260912|||||
"""
        items = load_universe(lambda _url: text)
        tickers = {item.ticker for item in items}
        # 클래스주는 야후 표기(BRK-B)로 살린다. 두 글자 이상 접미사는 아직 제외.
        self.assertEqual(tickers, {"AAA", "SPAC", "BRK-B"})
        self.assertEqual(next(item.market for item in items if item.ticker == "AAA"), "NASDAQ")


class TestSchemaAndConversion(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        ensure_schema(self.con)

    def tearDown(self):
        self.con.close()

    def test_t1_schema_and_primary_key(self):
        columns = self.con.execute("PRAGMA table_info('ohlcv')").fetchall()
        self.assertEqual(
            [row[1] for row in columns],
            ["date", "ticker", "market", "open", "high", "low", "close", "volume",
             "trading_value", "market_cap", "list_shrs"],
        )
        self.assertEqual([row[1] for row in columns if row[5]], ["date", "ticker"])

    def test_t5_today_bar_excluded(self):
        frames = {"AAA": _frame({"AAA": [("2026-09-11", 10), ("2026-09-12", 11)]})["AAA"]}
        rows, _ = to_rows(frames, {"AAA": "NASDAQ"}, "20260912")
        self.assertEqual([row[0] for row in rows], ["20260911"])

    def test_t10_nan_ohlc_excluded(self):
        frame = _frame({"AAA": [("2026-09-11", 10)]})["AAA"]
        frame["High"] = frame["High"].astype(float)
        frame.loc[:, "High"] = float("nan")
        rows, _ = to_rows({"AAA": frame}, {"AAA": "NASDAQ"}, "20260912")
        self.assertEqual(rows, [])

    def test_t7_point_in_time_market_cap_with_future_split(self):
        _insert_rows(self.con, [_row("20240102", "AAA", 100), _row("20240610", "AAA", 25)])
        self.con.execute("INSERT INTO splits VALUES ('AAA','20240610',4)")
        shares = pd.Series([1000], index=pd.to_datetime(["2024-01-01"]))
        apply_share_history(self.con, "AAA", shares)
        cap = self.con.execute(
            "SELECT market_cap FROM ohlcv WHERE ticker='AAA' AND date='20240102'"
        ).fetchone()[0]
        self.assertEqual(cap, 400000)

        stats = ensure_shares(
            self.con, ["AAA"], "20240101",
            shares_fetch=lambda ticker, start: shares,
            refresh_all=True,
        )
        self.assertEqual(stats["updated_rows"], 2)

    def test_t17_carried_shares_keep_split_adjusted_market_cap(self):
        """주식수를 앞날에서 끌어온 행도 이후 분할비를 반영한다 (apply_share_history 와 같은 식)."""
        _insert_rows(self.con, [_row("20240102", "AAA", 100), _row("20240103", "AAA", 100)])
        self.con.execute("INSERT INTO splits VALUES ('AAA','20240610',4)")
        self.con.execute(
            "UPDATE ohlcv SET list_shrs=1000, market_cap=400000 "
            "WHERE ticker='AAA' AND date='20240102'"
        )
        self.assertEqual(carry_forward_shares(self.con), 1)
        self.assertEqual(self.con.execute(
            "SELECT market_cap FROM ohlcv WHERE ticker='AAA' AND date='20240103'"
        ).fetchone()[0], 400000)


class TestFetchAndEnsure(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        ensure_schema(self.con)
        self.items = [UniverseItem("AAA", "Alpha", "NASDAQ")]

    def tearDown(self):
        self.con.close()

    def test_t2_idempotent(self):
        download = lambda tickers, start, end: _frame({ticker: [("2026-09-11", 10)] for ticker in tickers})
        ensure_ohlcv(self.con, self.items, "20260101", "20260912", download, "20260912")
        ensure_ohlcv(self.con, self.items, "20260101", "20260912", download, "20260912")
        self.assertEqual(self.con.execute("SELECT count(*) FROM ohlcv").fetchone()[0], 1)

    def test_t3_resume_starts_at_last_held_day(self):
        _insert_rows(self.con, [_row("20260910", "AAA")])
        self.assertEqual(plan_fetch(self.con, ["AAA"], "20260101"), {"20260910": ["AAA"]})

    def test_t4_split_replaces_full_history(self):
        _insert_rows(self.con, [_row("20240102", "AAA", 400)])
        calls = []

        def download(tickers, start, end):
            calls.append((tickers, start))
            if start == "20240102":
                return _frame({"AAA": [("20240610", 25)]}, splits={("AAA", "20240610"): 4})
            return _frame({"AAA": [("20240102", 100), ("20240610", 25)]},
                          splits={("AAA", "20240610"): 4})

        stats = ensure_ohlcv(self.con, self.items, "20240101", "20260912", download, "20260912")
        self.assertIn((['AAA'], "20240101"), calls)
        self.assertEqual(stats["split_refetched"], ["AAA"])
        self.assertEqual(self.con.execute(
            "SELECT close FROM ohlcv WHERE ticker='AAA' AND date='20240102'"
        ).fetchone()[0], 100)
        self.assertEqual(self.con.execute("SELECT count(*) FROM splits").fetchone()[0], 1)

    def test_t8_failed_batch_does_not_stop_other_batch(self):
        items = [UniverseItem("AAA", "Alpha", "NASDAQ"), UniverseItem("BBB", "Beta", "NYSE")]

        def download(tickers, start, end):
            if tickers == ["AAA"]:
                raise RuntimeError("network")
            return _frame({"BBB": [("2026-09-11", 20)]})

        with patch("scripts.build_us_ohlcv.BATCH_SIZE", 1):
            stats = ensure_ohlcv(self.con, items, "20260101", "20260912", download, "20260912")
        self.assertEqual(stats["failed_tickers"], ["AAA"])
        self.assertEqual(self.con.execute("SELECT ticker FROM ohlcv").fetchone()[0], "BBB")
        with patch("scripts.build_us_ohlcv.BATCH_SIZE", 1):
            retry = ensure_ohlcv(
                self.con, items, "20260101", "20260912",
                lambda tickers, start, end: _frame({
                    ticker: [("2026-09-11", 10)] for ticker in tickers
                }),
                "20260912",
            )
        self.assertEqual(retry["failed_tickers"], [])
        self.assertEqual(self.con.execute("SELECT count(DISTINCT ticker) FROM ohlcv").fetchone()[0], 2)

    def test_t9_delisted_rows_preserved(self):
        _insert_rows(self.con, [_row("20250102", "OLD")])
        ensure_ohlcv(self.con, self.items, "20260101", "20260912",
                     lambda tickers, start, end: _frame({"AAA": [("2026-09-11", 10)]}),
                     "20260912")
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM ohlcv WHERE ticker='OLD'"
        ).fetchone()[0], 1)

    def test_t11_partial_return_detected(self):
        frames, missing = fetch_batch(
            ["AAA", "BBB"], "20260101", "20260912",
            lambda tickers, start, end: _frame({"AAA": [("2026-09-11", 10)]}),
        )
        self.assertEqual(set(frames), {"AAA"})
        self.assertEqual(missing, ["BBB"])

    def test_t14_split_replace_rolls_back(self):
        _insert_rows(self.con, [_row("20240102", "AAA", 400)])
        self.con.execute("INSERT INTO splits VALUES ('AAA','20240101',2)")
        with patch("scripts.build_us_ohlcv._insert_rows", side_effect=RuntimeError("insert")):
            with self.assertRaises(RuntimeError):
                _replace_split_history(
                    self.con, "AAA", [_row("20240102", "AAA", 100)],
                    [("AAA", "20240610", 4)],
                )
        self.assertEqual(self.con.execute(
            "SELECT close FROM ohlcv WHERE ticker='AAA'"
        ).fetchone()[0], 400)
        self.assertEqual(self.con.execute("SELECT ratio FROM splits WHERE ticker='AAA'").fetchone()[0], 2)

    def test_t16_truncated_refetch_keeps_stored_history(self):
        """잘린 재조회(앞 구간 누락)는 기존 이력을 지우지 않고 실패한다."""
        _insert_rows(self.con, [_row("20240102", "AAA", 400), _row("20240610", "AAA", 100)])
        with self.assertRaises(RuntimeError):
            _replace_split_history(
                self.con, "AAA", [_row("20240610", "AAA", 25)], [("AAA", "20240610", 4)],
            )
        self.assertEqual(self.con.execute(
            "SELECT count(*) FROM ohlcv WHERE ticker='AAA'"
        ).fetchone()[0], 2)


class TestGapAudit(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        ensure_schema(self.con)
        self.aaa = UniverseItem("AAA", "Alpha", "NASDAQ")

    def tearDown(self):
        self.con.close()

    def test_t12_internal_gap_detected(self):
        _insert_rows(self.con, [
            _row("20260909", "AAA"), _row("20260911", "AAA"),
        ])
        self.con.executemany("INSERT INTO etf_ohlcv VALUES (?,?,?,?,?,?,?)", [
            _etf_row("20260909"), _etf_row("20260910"), _etf_row("20260911"),
        ])
        self.assertEqual(audit_gaps(self.con, [self.aaa]), {"AAA": ["20260910"]})
        stats = repair_gaps(
            self.con, [self.aaa], "20260912",
            lambda tickers, start, end: _frame({"AAA": [("20260910", 100)]}),
            "20260912",
        )
        self.assertEqual(stats["unresolved"], {})

    def test_t13_listing_edges_not_reported_or_deleted(self):
        _insert_rows(self.con, [
            _row("20260910", "AAA"),
        ])
        self.con.executemany("INSERT INTO etf_ohlcv VALUES (?,?,?,?,?,?,?)", [
            _etf_row("20260909"), _etf_row("20260910"), _etf_row("20260911"),
        ])
        self.assertEqual(audit_gaps(self.con, [self.aaa]), {})
        self.assertEqual(self.con.execute("SELECT count(*) FROM ohlcv WHERE ticker='AAA'").fetchone()[0], 1)

        _insert_rows(self.con, [_row("20260909", "AAA"), _row("20260911", "AAA")])
        self.con.execute("DELETE FROM ohlcv WHERE ticker='AAA' AND date='20260910'")
        stats = repair_gaps(
            self.con, [self.aaa], "20260912",
            lambda tickers, start, end: _frame({"AAA": [("20260909", 100), ("20260911", 100)]}),
            "20260912",
        )
        self.assertEqual(stats["unresolved"], {"AAA": ["20260910"]})
        self.assertEqual(self.con.execute("SELECT count(*) FROM ohlcv WHERE ticker='AAA'").fetchone()[0], 2)

    def test_t15_stale_spy_calendar_fails_safely(self):
        _insert_rows(self.con, [_row("20260911", "AAA")])
        self.con.executemany("INSERT INTO etf_ohlcv VALUES (?,?,?,?,?,?,?)", [_etf_row("20260910")])
        with self.assertRaisesRegex(RuntimeError, "not current"):
            audit_gaps(self.con, [self.aaa])
        self.assertEqual(self.con.execute("SELECT count(*) FROM ohlcv").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT count(*) FROM etf_ohlcv").fetchone()[0], 1)

    def test_spy_ahead_of_stocks_is_ok(self):
        _insert_rows(self.con, [
            _row("20260909", "AAA"), _row("20260910", "AAA"),
        ])
        self.con.executemany("INSERT INTO etf_ohlcv VALUES (?,?,?,?,?,?,?)", [
            _etf_row("20260909"), _etf_row("20260910"), _etf_row("20260911"),
        ])
        self.assertEqual(audit_gaps(self.con, [self.aaa]), {})


class TestEtf(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")
        ensure_schema(self.con)

    def tearDown(self):
        self.con.close()

    def test_etf_tickers(self):
        self.assertEqual(
            ETF_TICKERS,
            ("SPY", "QQQ", "IWM", "TQQQ", "BIL", "HYG", "JNK", "IEI", "IEF"),
        )

    def test_e1_etf_rows_not_mixed_into_stock_tables(self):
        _insert_rows(self.con, [_row("20260910", "SPY")])
        self.con.execute("INSERT INTO stock_names VALUES ('SPY','SPDR S&P 500','2026-09-11')")
        stats = ensure_etf(
            self.con, "20260912",
            lambda tickers, start, end: _frame(
                {ticker: [("2026-09-10", 100)] for ticker in tickers}
            ),
            "20260912",
            tickers=("SPY", "TQQQ"),
        )
        self.assertEqual(stats["failed_tickers"], [])
        self.assertEqual(
            sorted(row[0] for row in self.con.execute("SELECT DISTINCT ticker FROM etf_ohlcv").fetchall()),
            ["SPY", "TQQQ"],
        )
        self.assertEqual(self.con.execute("SELECT count(*) FROM ohlcv WHERE ticker='SPY'").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM stock_names WHERE code='SPY'").fetchone()[0], 0)

    def test_e2_dividends_stored(self):
        stats = ensure_etf(
            self.con, "20260912",
            lambda tickers, start, end: _frame(
                {"TQQQ": [("2026-09-09", 100), ("2026-09-10", 100), ("2026-09-11", 100)]},
                dividends={("TQQQ", "2026-09-10"): 0.5},
            ),
            "20260912",
            tickers=("TQQQ",),
        )
        self.assertEqual(stats["dividend_rows"], 1)
        self.assertEqual(
            self.con.execute("SELECT ticker, date, amount FROM etf_dividends").fetchall(),
            [("TQQQ", "20260910", 0.5)],
        )

    def test_e3_full_replace_and_truncated_response_protected(self):
        def download_for(days):
            def _download(tickers, start, end):
                return _frame({ticker: list(days) for ticker in tickers})
            return _download

        ensure_etf(
            self.con, "20260912",
            download_for([("2026-09-09", 100), ("2026-09-10", 100), ("2026-09-11", 100)]),
            "20260912",
            tickers=("SPY",),
        )
        ensure_etf(
            self.con, "20260912",
            download_for([("2026-09-09", 25), ("2026-09-10", 25), ("2026-09-11", 25)]),
            "20260912",
            tickers=("SPY",),
        )
        self.assertEqual(
            self.con.execute("SELECT DISTINCT close FROM etf_ohlcv WHERE ticker='SPY'").fetchall(),
            [(25.0,)],
        )
        truncated = ensure_etf(
            self.con, "20260912",
            download_for([("2026-09-10", 25), ("2026-09-11", 25)]),
            "20260912",
            tickers=("SPY",),
        )
        self.assertEqual(truncated["failed_tickers"], ["SPY"])
        self.assertEqual(self.con.execute("SELECT count(*) FROM etf_ohlcv WHERE ticker='SPY'").fetchone()[0], 3)
        self.assertEqual(
            self.con.execute("SELECT DISTINCT close FROM etf_ohlcv WHERE ticker='SPY'").fetchall(),
            [(25.0,)],
        )

    def test_e8_truncated_tail_response_protected(self):
        """잘린 재조회(뒤 구간 누락)는 기존 이력을 지우지 않고 실패한다."""
        def download_for(days):
            def _download(tickers, start, end):
                return _frame({ticker: list(days) for ticker in tickers})
            return _download

        ensure_etf(
            self.con, "20260912",
            download_for([("2026-09-09", 100), ("2026-09-10", 100), ("2026-09-11", 100)]),
            "20260912",
            tickers=("SPY",),
        )
        truncated = ensure_etf(
            self.con, "20260912",
            download_for([("2026-09-09", 25), ("2026-09-10", 25)]),
            "20260912",
            tickers=("SPY",),
        )
        self.assertEqual(truncated["failed_tickers"], ["SPY"])
        self.assertEqual(self.con.execute("SELECT count(*) FROM etf_ohlcv WHERE ticker='SPY'").fetchone()[0], 3)
        self.assertEqual(
            self.con.execute("SELECT DISTINCT close FROM etf_ohlcv WHERE ticker='SPY'").fetchall(),
            [(100.0,)],
        )

    def test_e4_empty_etf_calendar_fails(self):
        with self.assertRaisesRegex(RuntimeError, "SPY calendar is empty"):
            audit_gaps(self.con, [UniverseItem("AAA", "Alpha", "NASDAQ")])

    def test_e5_intraday_bar_excluded(self):
        ensure_etf(
            self.con, "20260912",
            lambda tickers, start, end: _frame(
                {ticker: [("2026-09-10", 100), ("2026-09-11", 100)] for ticker in tickers}
            ),
            "20260911",
            tickers=("SPY",),
        )
        self.assertEqual(
            self.con.execute("SELECT date FROM etf_ohlcv WHERE ticker='SPY'").fetchall(),
            [("20260910",)],
        )

    def test_e6_download_error_preserves_existing_rows(self):
        self.con.executemany("INSERT INTO etf_ohlcv VALUES (?,?,?,?,?,?,?)", [_etf_row("20260910")])

        def download(tickers, start, end):
            raise RuntimeError("network")

        stats = ensure_etf(self.con, "20260912", download, "20260912", tickers=("SPY", "TQQQ"))
        self.assertEqual(stats["failed_tickers"], ["SPY", "TQQQ"])
        self.assertEqual(stats["inserted_rows"], 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM etf_ohlcv").fetchone()[0], 1)

    def test_e7_download_error_preserves_stock_rows(self):
        _insert_rows(self.con, [_row("20260910", "SPY")])

        def download(tickers, start, end):
            raise RuntimeError("network")

        stats = ensure_etf(self.con, "20260912", download, "20260912", tickers=("SPY",))
        self.assertEqual(stats["failed_tickers"], ["SPY"])
        self.assertEqual(self.con.execute("SELECT count(*) FROM ohlcv WHERE ticker='SPY'").fetchone()[0], 1)


class TestBarCutoff(unittest.TestCase):
    def test_bar_cutoff_day(self):
        self.assertEqual(
            bar_cutoff_day(datetime(2026, 10, 2, 19, 59, tzinfo=NEW_YORK)), "20261002"
        )
        self.assertEqual(
            bar_cutoff_day(datetime(2026, 10, 2, 20, 1, tzinfo=NEW_YORK)), "20261003"
        )


if __name__ == "__main__":
    unittest.main()
