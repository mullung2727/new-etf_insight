"""research/us_oas/defense.py 단위 테스트 — 합성 데이터만, 실 DB·네트워크 없음 (unittest)."""
import math
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from research.us_oas import defense


def _synthetic(n=30, seed=7):
    dates = [f"202401{d:02d}" for d in range(1, n + 1)]
    rng = np.random.default_rng(seed)
    ret = pd.Series(rng.normal(0.001, 0.01, n), index=dates)
    level = (1 + ret).cumprod()
    vix = pd.Series(20.0, index=dates)
    vix3m = pd.Series(21.0, index=dates)
    dfii = pd.Series(np.linspace(1.0, 2.0, n), index=dates)
    return dates, level, ret, vix, vix3m, dfii


class TestDefense(unittest.TestCase):
    def test_d1_indicator_shift(self):
        dates, level, ret, vix, vix3m, dfii = _synthetic()
        ind1 = defense.indicators(level, ret, vix, vix3m, dfii, dates)
        self.assertEqual(ind1.columns.tolist(),
                         ["rv20", "vix", "vix_ratio", "below200", "mom20", "dfii_d20_bp"])
        self.assertEqual(ind1.index.tolist(), dates)
        t = dates[25]
        expect_rv = ret.loc[dates[5:25]].std(ddof=1) * math.sqrt(252)
        self.assertAlmostEqual(ind1.loc[t, "rv20"], expect_rv)
        ret2 = ret.copy()
        ret2.loc[t] += 0.5
        level2 = (1 + ret2).cumprod()
        ind2 = defense.indicators(level2, ret2, vix, vix3m, dfii, dates)
        pd.testing.assert_series_equal(ind1.loc[t], ind2.loc[t])

    def test_d2_fast_cap(self):
        ind = pd.DataFrame({
            "rv20": [0.10, 0.25, 0.25, 0.25, 0.10],
            "vix": [20.0, 20.0, 35.0, 35.0, 20.0],
            "vix_ratio": [0.95, 0.95, 0.95, 1.10, float("nan")],
        })
        self.assertEqual(defense.fast_cap(ind).tolist(), [1.0, 0.7, 0.5, 0.5, 1.0])

    def test_d3_persistent_cap(self):
        ind = pd.DataFrame({
            "below200": [True, True, False, True, True],
            "mom20": [-0.05, -0.05, -0.05, -0.01, float("nan")],
            "dfii_d20_bp": [10.0, 45.0, 50.0, 50.0, 50.0],
        })
        self.assertEqual(defense.persistent_cap(ind).tolist(), [0.5, 0.2, 1.0, 1.0, 1.0])
        self.assertEqual(defense.persistent_cap(ind, use_real_rate=False).tolist(),
                         [0.5, 0.5, 1.0, 1.0, 1.0])

    def test_d4_final_weights_severe(self):
        # severe는 below·mom≤-10%를 내포해 persistent도 걸리므로,
        # 0.5 분할만 분리해 보기 위해 persistent 블록을 끈다.
        ind = pd.DataFrame({
            "rv20": [0.35], "vix": [20.0], "vix_ratio": [0.95],
            "below200": [True], "mom20": [-0.12], "dfii_d20_bp": [0.0],
        }, index=["t"])
        out = defense.final_weights(pd.Series([1.0], index=["t"]), ind,
                                    use_persistent=False)
        self.assertEqual(out.columns.tolist(), ["TQQQ", "QQQ", "BIL"])
        self.assertAlmostEqual(out.loc["t", "TQQQ"], 0.35)
        self.assertAlmostEqual(out.loc["t", "QQQ"], 0.15)
        self.assertAlmostEqual(out.loc["t", "BIL"], 0.5)

    def test_d5_block_off(self):
        ind = pd.DataFrame({
            "rv20": [0.35], "vix": [35.0], "vix_ratio": [1.10],
            "below200": [False], "mom20": [0.0], "dfii_d20_bp": [0.0],
        }, index=["t"])
        w = pd.Series([1.0], index=["t"])
        off = defense.final_weights(w, ind, use_fast=False)
        self.assertAlmostEqual(off.loc["t", "TQQQ"], 1.0)
        on = defense.final_weights(w, ind)
        self.assertAlmostEqual(on.loc["t", "TQQQ"], 0.5)

    def test_d6_fred_series_has_dfii10(self):
        root = Path(__file__).resolve().parents[3]
        if str(root / "etl") not in sys.path:
            sys.path.insert(0, str(root / "etl"))
        from scripts.build_us_macro import FRED_SERIES
        self.assertIn("DFII10", tuple(FRED_SERIES))


if __name__ == "__main__":
    unittest.main()
