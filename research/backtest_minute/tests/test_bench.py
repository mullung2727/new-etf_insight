"""bench 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

import pandas as pd

from research.backtest_daily.bench_daily import bench_return
from research.backtest_minute.bench import hold_bench


def _table():
    return pd.DataFrame([
        {"date": "20251201", "ms": 0, "bench_id": "MKT_ALL", "n": 10,
         "r_cc": 0.01, "r_on": 0.004, "r_in": 0.006},
        {"date": "20251202", "ms": 1, "bench_id": "MKT_ALL", "n": 10,
         "r_cc": 0.02, "r_on": 0.008, "r_in": 0.012},
    ])


class TestHoldBench(unittest.TestCase):
    def test_open_vs_close(self):
        t = _table()
        self.assertAlmostEqual(
            hold_bench("MKT_ALL", "20251201", 90000, "20251202", "close", table=t),
            bench_return("MKT_ALL", "20251201", "open", "20251202", "close", table=t))
        self.assertAlmostEqual(
            hold_bench("MKT_ALL", "20251201", 100500, "20251202", "close", table=t),
            bench_return("MKT_ALL", "20251201", "close", "20251202", "close", table=t))


if __name__ == "__main__":
    unittest.main()
