"""주간수익률 TDD — 수정주가(상장주식수 밴드) + 일별 ±30% 클립 + 주간 복리.

실행: repo root에서 `python -m unittest research.cluster_closebet.tests.test_weekly`
"""
import unittest

from research.cluster_closebet.weekly import daily_returns, weekly_returns


def _bar(day, close, shrs=1_000_000):
    return {"date": f"202401{day:02d}", "close": close, "list_shrs": shrs}


class TestDailyReturns(unittest.TestCase):
    def test_plain_return(self):
        out = daily_returns([_bar(1, 10000), _bar(2, 10100)])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0][1], 0.01)

    def test_split_adjusted_to_market_cap_ratio(self):
        # 2:1 분할 — 원시 -50%가 아니라 시총 비율 +2%가 돼야 한다
        out = daily_returns([_bar(1, 10000, 1_000_000), _bar(2, 5100, 2_000_000)])
        self.assertAlmostEqual(out[0][1], 0.02)

    def test_small_share_change_not_adjusted(self):
        # +5% 주식수 변동(CB 전환 등)은 실제 희석이라 건드리지 않는다
        out = daily_returns([_bar(1, 10000, 1_000_000), _bar(2, 9000, 1_050_000)])
        self.assertAlmostEqual(out[0][1], -0.10)

    def test_daily_clip_at_30pct(self):
        # 주식수 그대로인데 +200%면 데이터 잔재 → +30%로 클립
        out = daily_returns([_bar(1, 10000), _bar(2, 30000)])
        self.assertAlmostEqual(out[0][1], 0.30)

    def test_zero_close_skipped(self):
        # 거래정지 0값 행은 건너뛰고 직전 유효봉과 잇는다
        out = daily_returns([_bar(1, 10000), _bar(2, 0), _bar(3, 10100)])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0][1], 0.01)

    def test_zero_shares_falls_back_to_raw(self):
        out = daily_returns([
            {"date": "20240101", "close": 10000, "list_shrs": 0},
            {"date": "20240102", "close": 10100, "list_shrs": 0},
        ])
        self.assertAlmostEqual(out[0][1], 0.01)


class TestWeeklyReturns(unittest.TestCase):
    def test_compounds_five_days(self):
        daily = [(f"202401{day:02d}", 0.01) for day in (1, 2, 3, 4, 5)]
        out = weekly_returns(daily)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(list(out.values())[0], 1.01 ** 5 - 1)


if __name__ == "__main__":
    unittest.main()
