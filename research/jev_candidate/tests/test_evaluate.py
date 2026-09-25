"""evaluate.py 단위 테스트 — 합성 데이터만 (:memory: sqlite + 작은 프레임).

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m unittest discover -s research/jev_candidate/tests -t .
"""
from __future__ import annotations

import unittest

import pandas as pd

from research.jev_candidate import evaluate, store

DAY = "20260409"
VER = "v2"
RUN = f"backtest-open-{DAY}-{VER}"


def _oc(ticker: str, ret: float | None, excl: str | None = None) -> dict:
    """합성 outcomes 행."""
    return {"date": DAY, "ticker": ticker, "d0_close": 1000, "d1_open": 1000,
            "d1_0930": 1000, "d1_close": 1000, "d1_high": 1000, "d1_low": 1000,
            "ret_open_0930": None, "ret_open_close": ret, "max_ret_o": ret,
            "min_ret_o": ret, "excluded_open": excl}


def _db(picks: dict[str, list[str]], outcomes: list[dict]):
    """decisions + outcomes 담은 :memory: DB."""
    con = store.connect(":memory:")
    store.ensure_schema(con)
    rows = [{"run_id": RUN, "date": DAY, "ticker": t, "arm": a,
             "score": 1.0, "rank": i + 1, "decision": "BUY"}
            for a, ts in picks.items() for i, t in enumerate(ts)]
    store.save_decisions(con, rows)
    store.save_outcomes(con, outcomes)
    return con


class TestDrops(unittest.TestCase):
    def test_limit_up은현금_gap은전arm우주제외(self):
        picks = {"A": ["L", "U", "G"], "E": ["L", "U", "G", "X"]}
        con = _db(picks, [_oc("L", 0.28, "limit_up"), _oc("U", None, "gap_artifact"),
                          _oc("G", 0.05), _oc("X", -0.01)])
        out = {t: o for (d, t), o in store.load_outcomes(con, [DAY]).items()}
        bench = {t: 0.01 for t in "LUGX"}
        r = evaluate.score_day(evaluate.load_picks(con, RUN), ["L", "U", "G", "X"],
                               out, bench)
        self.assertEqual(r["drops"], {"U"})
        self.assertEqual(r["arm"]["A"], [0.0, 0.04])  # limit_up 정확히 0, 벤치 미차감
        self.assertEqual(len(r["arm"]["E"]), 3)  # U 제외
        self.assertEqual(len(r["universe"]), 3)


class TestWeighting(unittest.TestCase):
    def test_일별가중_3픽일과1픽일이동일(self):
        means = [0.03, 0.0]  # 3픽일 평균, 1픽일 평균
        s = evaluate.summarize(means, 2, 4)
        self.assertAlmostEqual(s["mean"], 0.015)  # 종목가중이면 0.0225

    def test_NOTRADE는평균제외하고계수(self):
        s = evaluate.summarize([0.02], 3, 1)
        self.assertEqual(s["n_days"], 1)
        self.assertEqual(s["no_trade"], 2)
        self.assertAlmostEqual(s["no_trade_ratio"], 2 / 3)
        self.assertAlmostEqual(s["mean"], 0.02)


class TestPfMdd(unittest.TestCase):
    def test_알려진계열(self):
        s = evaluate.summarize([0.02, -0.01, 0.03, -0.02], 4, 4)
        self.assertAlmostEqual(s["profit_factor"], 0.05 / 0.03)
        self.assertAlmostEqual(s["mdd"], -0.02)  # 누적 0.04→0.02


class TestPlacebo(unittest.TestCase):
    # 호재 1개 + 악재 5개 — k=2 일평균은 0.025(1/3) 또는 -0.05(2/3) 둘 중 하나
    UNI = {"d1": [0.10, -0.05, -0.05, -0.05, -0.05, -0.05],
           "d2": [0.10, -0.05, -0.05, -0.05, -0.05, -0.05]}

    def test_동일콤보2개_p95가단일이상_결정적(self):
        one = {"c1": {"d1": 2, "d2": 2}}
        two = {"c1": {"d1": 2, "d2": 2}, "c2": {"d1": 2, "d2": 2}}
        p1, _ = evaluate.placebo_maxstat(self.UNI, one, draws=3000)
        p2, _ = evaluate.placebo_maxstat(self.UNI, two, draws=3000)
        self.assertGreaterEqual(p2, p1)
        pa, _ = evaluate.placebo_maxstat(self.UNI, two, draws=3000)
        pb, _ = evaluate.placebo_maxstat(self.UNI, two, draws=3000)
        self.assertEqual(pa, pb)


class TestPriceControlled(unittest.TestCase):
    def test_손계산일치(self):
        groups = {"b1": [(3.0, 0.05), (2.0, 0.04), (1.0, 0.0)],  # 상위 0.05 − 하위 0.0
                  "b2": [(1.0, 0.02), (0.0, 0.01)]}  # 0.02 − 0.01
        self.assertAlmostEqual(evaluate.price_controlled_day(groups), (0.05 + 0.01) / 2)


class TestBenchFrame(unittest.TestCase):
    def test_소수프레임평균(self):
        df = pd.DataFrame([
            {"eday": "E", "ticker": "a", "bucket": "<1천억", "r": 0.02},
            {"eday": "E", "ticker": "b", "bucket": "<1천억", "r": 0.04},
            {"eday": "E", "ticker": "c", "bucket": "5조+", "r": -0.01},
        ])
        bench, tick, overall = evaluate.bench_means(df)
        self.assertAlmostEqual(bench[("E", "<1천억")], 0.03)
        self.assertEqual(tick[("E", "c")], "5조+")
        self.assertAlmostEqual(overall["E"], 0.05 / 3)


if __name__ == "__main__":
    unittest.main()
