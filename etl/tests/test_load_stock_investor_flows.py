import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import duckdb
import pandas as pd

from scripts import load_stock_investor_flows as mod
from scripts.load_stock_investor_flows import (
    fetch_ticker, parse_row, run, save_ticker, universe,
)


def _raw(**over):
    base = {
        "dt": "20240104",
        "cur_prc": "+70000", "flu_rt": "+1.25",
        "acc_trde_qty": "1234567", "acc_trde_prica": "6706958",
        "ind_invsr": "+100", "frgnr_invsr": "--200", "orgn": "0",
        "fnnc_invt": "", "insrnc": None, "invtrt": "1",
        "etc_fnnc": "2", "bank": "3", "penfnd_etc": "4",
        "samo_fund": "5", "natn": "6", "etc_corp": "7", "natfor": "8",
    }
    base.update(over)
    return base


def _args(**over):
    kw = {"start": "20240101", "end": "20240131", "n": 200,
          "limit": None, "tickers": None, "refresh": False}
    kw.update(over)
    return SimpleNamespace(**kw)


def _px():
    rows = [
        # D1 상위 2: A, B
        ("20240102", "A", 100), ("20240102", "B", 90),
        ("20240102", "C", 10), ("20240102", "D", 1),
        # D2 상위 2: A, C
        ("20240103", "A", 100), ("20240103", "B", 10),
        ("20240103", "C", 90), ("20240103", "D", 1),
        # D3 상위 2: B, D (다음 거래일 없음 → 미사용)
        ("20240104", "A", 10), ("20240104", "B", 100),
        ("20240104", "C", 1), ("20240104", "D", 90),
    ]
    return pd.DataFrame(rows, columns=["date", "ticker", "market_cap"])


class StockInvestorFlowsTest(unittest.TestCase):
    def test_universe_uses_prev_day_topn(self):
        # start=D2 → D2←D1{A,B}, D3←D2{A,C} → {A,B,C}.
        # B는 D1에만 상위인데 포함 (첫날 D2가 자신의 시총 대신 전일 순위 사용),
        # D는 D3 자신의 상위라 제외.
        self.assertEqual(universe(_px(), n=2, start="20240103"), ["A", "B", "C"])

    def test_parse_signs_and_scale(self):
        d = dict(zip(mod._COLS, parse_row(_raw(), "005930", "ts")))
        self.assertEqual(d["date"], "20240104")
        self.assertEqual(d["ticker"], "005930")
        self.assertEqual(d["close"], 70000)   # 부호 뗀 값
        self.assertAlmostEqual(d["chg_pct"], 0.0125)
        self.assertEqual(d["ind"], 100)       # '+' 제거
        self.assertEqual(d["frgnr"], -200)    # '--' → 음수 하나
        self.assertIsNone(d["fnnc_invt"])     # 빈 값 → None
        self.assertIsNone(d["insrnc"])        # None → None
        d2 = dict(zip(mod._COLS, parse_row(
            _raw(cur_prc="--70000", flu_rt="--128"), "005930", "ts")))
        self.assertEqual(d2["close"], 70000)  # 음수여도 절대값
        self.assertAlmostEqual(d2["chg_pct"], -1.28)
        self.assertIsNone(parse_row(_raw(dt=""), "005930", "ts"))  # 날짜 없으면 버림

    def test_fetch_merges_pages_and_stops_before_start(self):
        pages = [
            SimpleNamespace(data={"stk_invsr_orgn": [_raw(dt="20240104"), _raw(dt="20240103")]},
                            cont_yn="Y", next_key="k1"),
            SimpleNamespace(data={"stk_invsr_orgn": [_raw(dt="20240102"), _raw(dt="20231229")]},
                            cont_yn="Y", next_key="k2"),
        ]
        with mock.patch.object(mod, "request", side_effect=pages) as req:
            rows = fetch_ticker("005930", "20240102", "20240104")
        self.assertEqual([r["dt"] for r in rows], ["20240104", "20240103", "20240102"])
        self.assertEqual(req.call_count, 2)  # 시작일 이전 행에서 멈춤 (3번째 요청 없음)
        # 요청 형식 고정 (실측 2026-11-08)
        first_args, _ = req.call_args_list[0]
        self.assertEqual(first_args[0], "ka10059")
        self.assertEqual(first_args[1], "/api/dostk/stkinfo")
        self.assertEqual(first_args[2],
                         {"dt": "20240104", "stk_cd": "005930",
                          "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1"})
        _, kwargs = req.call_args_list[1]
        self.assertEqual(kwargs["cont_yn"], "Y")
        self.assertEqual(kwargs["next_key"], "k1")

    def test_done_skips_without_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "investor_flows.duckdb"
            con = duckdb.connect(str(db))
            try:
                con.execute(mod._SCHEMA)
                con.execute(mod._DONE_SCHEMA)
                mod._mark_done(con, "005930", "20240101", "20240131", 2, "ok", "ts")
            finally:
                con.close()
            with mock.patch.object(mod, "request") as req:
                res = run(_args(), db_path=db, tickers=["005930"])
            req.assert_not_called()
            self.assertEqual(res["skipped"], 1)
            self.assertEqual(res["saved"], 0)

    def test_empty_records_done_and_no_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "investor_flows.duckdb"
            page = SimpleNamespace(data={"stk_invsr_orgn": []}, cont_yn="N", next_key="")
            with mock.patch.object(mod, "request", return_value=page):
                res = run(_args(), db_path=db, tickers=["999999"])
            self.assertEqual(res["empty"], ["999999"])
            con = duckdb.connect(str(db))
            try:
                self.assertEqual(
                    con.execute("SELECT status, rows FROM stock_investor_done "
                                "WHERE ticker='999999'").fetchone(),
                    ("empty", 0))
                self.assertEqual(
                    con.execute("SELECT count(*) FROM stock_investor_daily").fetchone()[0], 0)
            finally:
                con.close()

    def test_error_records_done_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "investor_flows.duckdb"
            with mock.patch.object(mod, "request", side_effect=mod.KiwoomError("boom")), \
                    mock.patch.object(mod, "RETRY_WAIT", 0):
                res = run(_args(), db_path=db, tickers=["005930"])
            self.assertEqual(len(res["failed"]), 1)
            con = duckdb.connect(str(db))
            try:
                self.assertEqual(
                    con.execute("SELECT status FROM stock_investor_done "
                                "WHERE ticker='005930'").fetchone()[0],
                    "error")
            finally:
                con.close()

    def test_save_temp_db_pk_and_rowcount(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "investor_flows.duckdb"
            con = duckdb.connect(str(db))
            try:
                con.execute(mod._SCHEMA)
                n = save_ticker(con, "005930", "20240101", "20240131", [
                    parse_row(_raw(dt="20240103"), "005930", "ts"),
                    parse_row(_raw(dt="20240104"), "005930", "ts"),
                ])
                self.assertEqual(n, 2)
                self.assertEqual(
                    con.execute("SELECT count(*) FROM stock_investor_daily").fetchone()[0], 2)
                with self.assertRaises(duckdb.ConstraintException):
                    con.execute(
                        "INSERT INTO stock_investor_daily "
                        f"({', '.join(mod._COLS)}) VALUES ({', '.join('?' * len(mod._COLS))})",
                        parse_row(_raw(dt="20240103"), "005930", "ts"),
                    )
            finally:
                con.close()

    def test_main_exit_code(self):
        argv = ["--start", "20240101", "--end", "20240102"]
        with mock.patch.object(mod, "run", return_value={"failed": ["x"]}):
            self.assertEqual(mod.main(argv), 1)
        with mock.patch.object(mod, "run", return_value={"failed": []}):
            self.assertEqual(mod.main(argv), 0)

    def test_parse_args_rejects_bad_dates(self):
        with self.assertRaises(SystemExit) as cm:
            mod.parse_args(["--start", "20240131", "--end", "20240101"])
        self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(SystemExit) as cm:
            mod.parse_args(["--start", "20240230", "--end", "20240301"])
        self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(SystemExit) as cm:
            mod.parse_args(["--start", "20240101", "--end", "notadate"])
        self.assertEqual(cm.exception.code, 2)

    def test_unknown_response_preserves_existing(self):
        for bad_data in ({"foo": []}, {"stk_invsr_orgn": "not-a-list"}):
            with self.subTest(bad_data=bad_data):
                with tempfile.TemporaryDirectory() as tmp:
                    db = Path(tmp) / "investor_flows.duckdb"
                    con = duckdb.connect(str(db))
                    try:
                        con.execute(mod._SCHEMA)
                        con.execute(mod._DONE_SCHEMA)
                        save_ticker(con, "005930", "20240101", "20240131",
                                    [parse_row(_raw(dt="20240103"), "005930", "ts")])
                    finally:
                        con.close()
                    page = SimpleNamespace(data=bad_data, cont_yn="N", next_key="")
                    with mock.patch.object(mod, "request", return_value=page), \
                            mock.patch.object(mod, "RETRY_WAIT", 0):
                        res = run(_args(), db_path=db, tickers=["005930"])
                    self.assertEqual(len(res["failed"]), 1)
                    con = duckdb.connect(str(db))
                    try:
                        self.assertEqual(
                            con.execute("SELECT count(*) FROM stock_investor_daily"
                                        ).fetchone()[0], 1)
                        self.assertEqual(
                            con.execute("SELECT status FROM stock_investor_done "
                                        "WHERE ticker='005930'").fetchone()[0],
                            "error")
                    finally:
                        con.close()

    def test_all_invalid_dt_fails_without_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "investor_flows.duckdb"
            con = duckdb.connect(str(db))
            try:
                con.execute(mod._SCHEMA)
                con.execute(mod._DONE_SCHEMA)
                save_ticker(con, "005930", "20240101", "20240131",
                            [parse_row(_raw(dt="20240103"), "005930", "ts")])
            finally:
                con.close()
            page = SimpleNamespace(
                data={"stk_invsr_orgn": [_raw(dt="bad"), _raw(dt="")]},
                cont_yn="N", next_key="")
            with mock.patch.object(mod, "request", return_value=page):
                res = run(_args(), db_path=db, tickers=["005930"])
            self.assertEqual(len(res["failed"]), 1)
            con = duckdb.connect(str(db))
            try:
                self.assertEqual(
                    con.execute("SELECT count(*) FROM stock_investor_daily").fetchone()[0], 1)
                self.assertEqual(
                    con.execute("SELECT status FROM stock_investor_done "
                                "WHERE ticker='005930'").fetchone()[0],
                    "error")
            finally:
                con.close()

    def test_universe_end_excludes_late_top(self):
        rows = [
            ("20240102", "A", 100), ("20240102", "Z", 1),
            ("20240103", "A", 100), ("20240103", "Z", 1),
            ("20240104", "A", 1), ("20240104", "Z", 100),
            ("20240105", "A", 1), ("20240105", "Z", 1),
        ]
        df = pd.DataFrame(rows, columns=["date", "ticker", "market_cap"])
        self.assertEqual(universe(df, n=1, start="20240103"), ["A", "Z"])
        self.assertEqual(universe(df, n=1, start="20240103", end="20240103"), ["A"])

    def test_run_passes_end_to_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "investor_flows.duckdb"
            page = SimpleNamespace(data={"stk_invsr_orgn": []}, cont_yn="N", next_key="")
            with mock.patch.object(mod, "load_px", return_value=_px()), \
                    mock.patch.object(mod, "universe", wraps=mod.universe) as uni, \
                    mock.patch.object(mod, "request", return_value=page):
                run(_args(start="20240103", end="20240103"), db_path=db, tickers=None)
            uni.assert_called_once()
            _, kwargs = uni.call_args
            self.assertEqual(kwargs.get("end"), "20240103")
