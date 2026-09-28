"""스윙 저장(store) 테스트.

실행 (etl 폴더): PYTHONPATH=. uv run python -m unittest tests.test_swing_pick_store
"""
import json
import sqlite3
import unittest
from tempfile import TemporaryDirectory

from scripts.swing_pick.store import (
    connect_ro,
    init_db,
    mark_notified,
    save_day,
)


def _cand(ticker, **kw):
    row = {
        "ticker": ticker, "name": f"종목{ticker}", "sources": ["telegram"],
        "trading_value": 6_000_000_000,
        "jev_sustain": 1.5, "jev_risk": 0.1,
        "op_profit": 100.0, "op_profit_yoy": 80.0,
        "frgn_net_5d": 10, "orgn_net_5d": 20,
        "ma20_gap": 0.05, "ret_5d": 0.03,
        "per": 11.1, "per_note": None,
        "themes": [{"name": "반도체", "ret": 5.2, "up": 8, "down": 2}],
        "s1": 2, "s3": 2, "s4": 1, "s5": 2,
        "risk_out": False, "total": 7, "rank": 1,
        "errors": {}, "jev_input_hash": "abc",
        "excluded_reason": None,
    }
    row.update(kw)
    return row


def _run(**kw):
    run = {
        "n_candidates": 1, "n_risk_out": 0, "n_errors": 0,
        "jev_input_tokens": 100, "summary": '{"overview": "x"}',
        "summary_model": None, "notified": 0,
        "prev_date": "20260925", "warnings": ["w1"],
    }
    run.update(kw)
    return run


def _all_cands(path, day):
    with connect_ro(path) as con:
        return [dict(r) for r in con.execute(
            "SELECT * FROM swing_candidates WHERE date_kst = ? ORDER BY ticker",
            (day,))]


def _one_run(path, day):
    with connect_ro(path) as con:
        r = con.execute(
            "SELECT * FROM swing_runs WHERE date_kst = ?", (day,)).fetchone()
    return dict(r) if r else None


class TestSaveDay(unittest.TestCase):
    def test_roundtrip_and_idempotent(self):
        with TemporaryDirectory() as tmp:
            db = f"{tmp}/swing.sqlite3"
            init_db(db)
            save_day(db, "2026-09-28",
                     [_cand("000001"), _cand("000002", risk_out=None,
                                             excluded_reason="jev_error",
                                             errors={"jev": "Timeout"})],
                     _run(n_candidates=2, n_errors=1))
            rows = _all_cands(db, "2026-09-28")
            self.assertEqual(len(rows), 2)
            # JSON 컬럼 왕복
            self.assertEqual(json.loads(rows[0]["sources"]), ["telegram"])
            self.assertEqual(json.loads(rows[0]["themes"])[0]["name"], "반도체")
            self.assertEqual(json.loads(rows[0]["errors"]), {})
            # risk_out None(Jev 실패) → 0으로 저장, jev_error로 구분
            bad = [r for r in rows if r["ticker"] == "000002"][0]
            self.assertEqual(bad["risk_out"], 0)
            self.assertEqual(bad["excluded_reason"], "jev_error")
            self.assertEqual(json.loads(bad["errors"]), {"jev": "Timeout"})
            run = _one_run(db, "2026-09-28")
            self.assertEqual(run["n_candidates"], 2)
            self.assertEqual(json.loads(run["warnings"]), ["w1"])

            # 같은 날 재실행 → 이전 행 사라지고 교체 (멱등)
            save_day(db, "2026-09-28", [_cand("000003")], _run(n_candidates=1))
            rows = _all_cands(db, "2026-09-28")
            self.assertEqual([r["ticker"] for r in rows], ["000003"])
            self.assertEqual(_one_run(db, "2026-09-28")["n_candidates"], 1)

    def test_other_day_kept(self):
        with TemporaryDirectory() as tmp:
            db = f"{tmp}/swing.sqlite3"
            save_day(db, "2026-09-25", [_cand("000001")], _run())
            save_day(db, "2026-09-28", [_cand("000002")], _run())
            # save_day가 스키마도 보장 — init_db 없이 호출 가능
            self.assertEqual(len(_all_cands(db, "2026-09-25")), 1)
            self.assertEqual(len(_all_cands(db, "2026-09-28")), 1)

    def test_mark_notified(self):
        with TemporaryDirectory() as tmp:
            db = f"{tmp}/swing.sqlite3"
            save_day(db, "2026-09-28", [_cand("000001")], _run(notified=0))
            self.assertEqual(_one_run(db, "2026-09-28")["notified"], 0)
            mark_notified(db, "2026-09-28")
            got = _one_run(db, "2026-09-28")
            self.assertEqual(got["notified"], 1)
            # 다른 날짜는 안 건드림
            save_day(db, "2026-09-25", [_cand("000001")], _run(notified=0))
            mark_notified(db, "2026-09-28")
            self.assertEqual(_one_run(db, "2026-09-25")["notified"], 0)


if __name__ == "__main__":
    unittest.main()
