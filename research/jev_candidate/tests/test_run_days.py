"""run_days.py 단위 테스트 — 재실행 가드·리포트 본런 필터.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m unittest discover -s research/jev_candidate/tests -t .
"""
from __future__ import annotations

import contextlib
import io
import unittest

from research.jev_candidate import run_days, store

DAY = "20260409"
RUN = f"backtest-open-{DAY}-v2"


def _mem():
    """빈 :memory: DB."""
    con = store.connect(":memory:")
    store.ensure_schema(con)
    return con


def _state(run_id: str, ticker: str) -> dict:
    """최소 states 행."""
    return {"run_id": run_id, "date": DAY, "ticker": ticker, "name": "테스트",
            "price_pct": 1.0, "state_text": "t", "state_hash": "h",
            "anon_map_json": "{}", "n_candidates": 1, "n_passed": 1,
            "n_ambiguous": 0, "n_included": 1, "truncated": 0}


def _ans(qid: str, type_: str, value: str) -> dict:
    """최소 jev_answers 행."""
    return {"qid": qid, "type": type_, "value": value, "confidence": 1.0,
            "probs": None}


class TestRerunGuard(unittest.TestCase):
    def _guard(self, con, run_id: str, ticker: str) -> bool:
        """_run_day 건너뜀 조건 그대로."""
        return store.has_state(con, run_id, ticker) and run_days._has_answers(
            con, run_id, ticker)

    def test_본런_답없으면재시도(self):
        con = _mem()
        store.save_state(con, _state(RUN, "A"))  # ask 실패 직후 상태
        self.assertFalse(self._guard(con, RUN, "A"))
        store.save_answers(con, RUN, DAY, "A", [_ans("q1", "choice", "a")])
        self.assertTrue(self._guard(con, RUN, "A"))

    def test_I런_답없으면재시도(self):
        con = _mem()
        run_i = RUN + "-I"
        store.save_state(con, _state(run_i, "A"))
        self.assertFalse(self._guard(con, run_i, "A"))
        store.save_answers(con, run_i, DAY, "A", [_ans("q1", "choice", "a")])
        self.assertTrue(self._guard(con, run_i, "A"))


class TestReportMainOnly(unittest.TestCase):
    def test_I런제외(self):
        con = _mem()
        run_i = RUN + "-I"
        store.save_state(con, _state(RUN, "A"))
        store.save_state(con, _state(RUN, "B"))
        for t in "ABC":
            store.save_state(con, _state(run_i, t))
        store.save_answers(con, RUN, DAY, "A", [_ans("q1", "choice", "a")])
        for t in "ABC":
            store.save_answers(con, run_i, DAY, t, [_ans("q1", "choice", "b")])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_days._report(con, [DAY])
        out = buf.getvalue()
        self.assertIn("states n=2", out)  # I런 3건 제외
        self.assertIn("{'a': 1}", out)  # I런 b 3건 제외
        self.assertNotIn("'b'", out)


if __name__ == "__main__":
    unittest.main()
