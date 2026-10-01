"""paths 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

import numpy as np
import pandas as pd

from research.backtest_daily.paths import event_paths


def _px(tickers, ms, closes, caps):
    return pd.DataFrame({"ticker": tickers, "ms": ms, "close": closes,
                         "market_cap": caps})


class TestEventPathsD0(unittest.TestCase):
    def test_d0_return_excluded(self):
        # 리뷰 #1 재현: ms0=1, 종가 100→110→121 → R = 0%, 10%
        px = _px(["A"] * 3, [0, 1, 2], [100.0, 110.0, 121.0], [1e8] * 3)
        r = np.array([np.nan, 0.10, 0.10])
        uni = pd.DataFrame({"ticker": ["A"], "ms0": [1], "cap0": [1.0]})
        R, X, B = event_paths(px, r, uni, K=1)
        self.assertAlmostEqual(float(R[0, 0]), 0.0, places=12)
        self.assertAlmostEqual(float(R[0, 1]), 0.10, places=12)
        self.assertAlmostEqual(float(X[0, 0]), 0.0, places=12)

    def test_d0_nan_same_as_before(self):
        # 신규상장(D0 행 수익 NaN): NaN 은 곱하지 않아 수정 전과 같은 결과
        px = _px(["A"] * 2, [1, 2], [110.0, 121.0], [1e8] * 2)
        r = np.array([np.nan, 0.10])
        uni = pd.DataFrame({"ticker": ["A"], "ms0": [1], "cap0": [1.0]})
        R, X, B = event_paths(px, r, uni, K=1)
        self.assertAlmostEqual(float(R[0, 0]), 0.0, places=12)
        self.assertAlmostEqual(float(R[0, 1]), 0.10, places=12)


if __name__ == "__main__":
    unittest.main()
