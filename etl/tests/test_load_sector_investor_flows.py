import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import duckdb

from scripts import load_sector_investor_flows as mod
from scripts.load_sector_investor_flows import fetch_day, parse_row, run, save_day


def _raw(**over):
    base = {
        "inds_cd": "001", "inds_nm": "종합",
        "cur_prc": "+285012", "flu_rt": "+1.25", "trde_qty": "1234567",
        "ind_netprps": "+100", "frgnr_netprps": "--200", "orgn_netprps": "0",
        "sc_netprps": "", "insrnc_netprps": None, "invtrt_netprps": "1",
        "bank_netprps": "2", "jnsinkm_netprps": "3", "endw_netprps": "4",
        "etc_corp_netprps": "5", "native_trmt_frgnr_netprps": "6",
        "natn_netprps": "7", "samo_fund_netprps": "8",
    }
    base.update(over)
    return base


def _args(**over):
    kw = {"start": "20240101", "end": "20240131", "market": "both", "refresh": False}
    kw.update(over)
    return SimpleNamespace(**kw)


class SectorInvestorFlowsTest(unittest.TestCase):
    def test_parse_signs_and_double_minus(self):
        row = parse_row(_raw(), "20240102", "KOSPI", "ts")
        d = dict(zip(mod._COLS, row))
        self.assertEqual(d["ind"], 100)       # '+' 제거
        self.assertEqual(d["frgnr"], -200)    # '--' → 음수 하나
        self.assertEqual(d["orgn"], 0)
        self.assertIsNone(d["sc"])            # 빈 값 → None
        self.assertIsNone(d["insrnc"])        # None → None

    def test_parse_index_scale(self):
        d = dict(zip(mod._COLS, parse_row(_raw(), "20240102", "KOSPI", "ts")))
        self.assertAlmostEqual(d["idx_close"], 2850.12)  # 부호 떼고 /100
        self.assertAlmostEqual(d["idx_chg_pct"], 0.0125)  # 부호 유지 /100
        d2 = dict(zip(mod._COLS, parse_row(
            _raw(cur_prc="-285012", flu_rt="--128"), "20240102", "KOSPI", "ts")))
        self.assertAlmostEqual(d2["idx_close"], 2850.12)  # 음수여도 절대값
        self.assertAlmostEqual(d2["idx_chg_pct"], -1.28)  # 이중 마이너스 유지

    def test_resume_skips_without_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "investor_flows.duckdb"
            con = duckdb.connect(str(db))
            try:
                con.execute(mod._SCHEMA)
                save_day(con, "20240102", "KOSPI",
                         [parse_row(_raw(), "20240102", "KOSPI", "ts")])
            finally:
                con.close()
            with mock.patch.object(mod, "request") as req:
                res = run(_args(market="kospi"), db_path=db, dates=["20240102"])
            req.assert_not_called()
            self.assertEqual(res["skipped"], 1)
            self.assertEqual(res["saved"], 0)

    def test_cont_yn_merges_pages(self):
        pages = [
            SimpleNamespace(data={"inds_netprps": [_raw(inds_cd="001")]},
                            cont_yn="Y", next_key="abc"),
            SimpleNamespace(data={"inds_netprps": [_raw(inds_cd="002")]},
                            cont_yn="N", next_key=""),
        ]
        with mock.patch.object(mod, "request", side_effect=pages) as req:
            rows = fetch_day("20240102", "0")
        self.assertEqual([r["inds_cd"] for r in rows], ["001", "002"])
        # 요청 형식 고정 (실측 2026-11-08)
        first_args, _ = req.call_args_list[0]
        self.assertEqual(first_args[0], "ka10051")
        self.assertEqual(first_args[1], "/api/dostk/sect")
        self.assertEqual(first_args[2],
                         {"mrkt_tp": "0", "amt_qty_tp": "0",
                          "base_dt": "20240102", "stex_tp": "1"})
        self.assertEqual(req.call_count, 2)
        _, kwargs = req.call_args_list[1]
        self.assertEqual(kwargs["cont_yn"], "Y")
        self.assertEqual(kwargs["next_key"], "abc")

    def test_save_temp_db_pk_and_rowcount(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "investor_flows.duckdb"
            con = duckdb.connect(str(db))
            try:
                con.execute(mod._SCHEMA)
                n = save_day(con, "20240102", "KOSPI", [
                    parse_row(_raw(inds_cd="001"), "20240102", "KOSPI", "ts"),
                    parse_row(_raw(inds_cd="002"), "20240102", "KOSPI", "ts"),
                ])
                self.assertEqual(n, 2)
                self.assertEqual(
                    con.execute("SELECT count(*) FROM sector_investor_daily").fetchone()[0], 2)
                with self.assertRaises(duckdb.ConstraintException):
                    con.execute(
                        "INSERT INTO sector_investor_daily "
                        f"({', '.join(mod._COLS)}) VALUES ({', '.join('?' * len(mod._COLS))})",
                        parse_row(_raw(inds_cd="001"), "20240102", "KOSPI", "ts"),
                    )
            finally:
                con.close()

    def test_trading_dates_union_sort_filter(self):
        with mock.patch.object(mod, "market_dates",
                               return_value=["20240103", "20240102"]):
            with mock.patch.object(mod, "_etf_adj_dates",
                                   return_value=["20240102", "20100104"]):
                self.assertEqual(mod.trading_dates("20100104", "20240103"),
                                 ["20100104", "20240102", "20240103"])
                self.assertEqual(mod.trading_dates("20240102", "20240102"),
                                 ["20240102"])
                self.assertEqual(mod.trading_dates("20250101", "20250131"), [])

    def test_etf_adj_dates_missing_db_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mod, "db_dir", return_value=Path(tmp)):
                self.assertEqual(mod._etf_adj_dates(), [])
