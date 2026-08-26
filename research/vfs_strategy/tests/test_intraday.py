"""다음날 장중 진입 스윕 회귀 테스트 — 네트워크 없이 합성 분봉으로 검증.

검증 항목:
  - opening_range_breakout: 09:30 이후 개장 30분 고가 돌파 첫 봉에서만 진입, 미돌파면 진입 없음
  - find_entries: 진입은 D+2(신호 다음 거래일) 봉만 보고, 신호 없는 표본은 매수 안 함
  - simulate: 진입일부터 보유일수만큼만 사용(신호일 제외), horizon 부족하면 표본 제외
  - 스윕 격자 크기와 정렬
"""
import unittest

from research.vfs_strategy.intraday import (
    ENTRY_RULES,
    OPENING_RANGE_END,
    SWEEP_DAYS,
    SWEEP_SL,
    SWEEP_TP,
    find_entries,
    opening_range_breakout,
    run_sweep,
    simulate,
)

SIGNAL_DATE = "20260601"
DATES = [SIGNAL_DATE, "20260602", "20260603", "20260604"]
ENTRY_DAY = DATES[1]
SIGNAL_LOW = 950


def _bar(date, time, open_, high, low, close, volume=100):
    return {
        "timestamp": f"{date}{time}", "date": date, "time": time,
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }


def _opening_range(date, high=1010):
    """09:00~09:30 구간 — 고가 high 로 개장 레인지를 만든다. 저가 940 은 SIGNAL_LOW 이탈용."""
    return [
        _bar(date, "090000", 960, high, 940, 955),
        _bar(date, OPENING_RANGE_END, 955, high, 950, 960),
    ]


def _flat_day(date, price=1000):
    return [
        _bar(date, "090000", price, price, price, price),
        _bar(date, "153000", price, price, price, price),
    ]


def _sample(entry_day_bars, later_days=(DATES[2], DATES[3])):
    bars = list(entry_day_bars)
    for date in later_days:
        bars += _flat_day(date)
    return {
        "ticker": "005930", "signal_date": SIGNAL_DATE, "trading_dates": DATES,
        "signal_low": SIGNAL_LOW, "signal_close": 1000, "bars": bars,
    }


class TestOpeningRangeBreakout(unittest.TestCase):
    def test_breakout_after_opening_range(self):
        bars = _opening_range(ENTRY_DAY, high=1010) + [
            _bar(ENTRY_DAY, "100000", 1005, 1012, 1005, 1011),   # 1010 돌파
            _bar(ENTRY_DAY, "101000", 1011, 1030, 1011, 1029),
        ]
        entry = opening_range_breakout(bars, SIGNAL_LOW)
        self.assertEqual(entry["entry_price"], 1011)
        self.assertEqual(entry["entry_timestamp"], f"{ENTRY_DAY}100000")

    def test_no_breakout_returns_none(self):
        bars = _opening_range(ENTRY_DAY, high=1010) + [
            _bar(ENTRY_DAY, "100000", 1005, 1009, 1000, 1005),
        ]
        self.assertIsNone(opening_range_breakout(bars, SIGNAL_LOW))

    def test_bar_inside_opening_range_never_triggers(self):
        bars = [_bar(ENTRY_DAY, "090000", 1000, 1010, 990, 1009)]
        self.assertIsNone(opening_range_breakout(bars, SIGNAL_LOW))


class TestFindEntries(unittest.TestCase):
    def test_uses_next_trading_day_bars_only(self):
        # 신호일에는 돌파가 있어도 진입하면 안 되고, D+2 에는 돌파가 없다
        bars = _opening_range(SIGNAL_DATE, high=1010) + [
            _bar(SIGNAL_DATE, "100000", 1005, 1050, 1005, 1049),
        ] + _flat_day(ENTRY_DAY) + _flat_day(DATES[2]) + _flat_day(DATES[3])
        sample = {**_sample([]), "bars": bars}
        self.assertEqual(find_entries([sample], ENTRY_RULES["opening_range_breakout"]), [])

    def test_entry_date_is_next_trading_day(self):
        sample = _sample(_opening_range(ENTRY_DAY, high=1010) + [
            _bar(ENTRY_DAY, "100000", 1005, 1012, 1005, 1011),
        ])
        entries = find_entries([sample], ENTRY_RULES["opening_range_breakout"])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["entry_date"], ENTRY_DAY)

    def test_sample_without_signal_low_is_skipped(self):
        sample = {**_sample(_opening_range(ENTRY_DAY)), "signal_low": None}
        self.assertEqual(find_entries([sample], ENTRY_RULES["prior_low_reclaim"]), [])


class TestSimulate(unittest.TestCase):
    def _entered_sample(self):
        # 레인지 고가 995 → 종가 1000 이 돌파, 진입가 1000
        return _sample(_opening_range(ENTRY_DAY, high=995) + [
            _bar(ENTRY_DAY, "100000", 990, 1000, 990, 1000),
        ])

    def test_take_profit_on_following_day(self):
        sample = self._entered_sample()
        sample["bars"] += [_bar(DATES[2], "110000", 1000, 1050, 1000, 1045)]
        entries = find_entries([sample], ENTRY_RULES["opening_range_breakout"])
        outcomes = simulate(entries, {"kind": "tp_sl", "tp": 0.04, "sl": 0.03, "days": 1}, 0.006)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["exit_reason"], "tp")
        self.assertAlmostEqual(outcomes[0]["net_return"], 0.04 - 0.006)

    def test_holding_horizon_beyond_available_dates_is_dropped(self):
        sample = self._entered_sample()
        entries = find_entries([sample], ENTRY_RULES["opening_range_breakout"])
        # trading_dates 는 4일(신호일 포함) → 진입일 기준 보유 3일치 날짜가 없다
        self.assertEqual(simulate(entries, {"kind": "tp_sl", "tp": 0.04, "sl": 0.03, "days": 3}, 0.006), [])


class TestRunSweep(unittest.TestCase):
    def test_grid_size_and_ranking(self):
        sample = _sample(_opening_range(ENTRY_DAY, high=1010) + [
            _bar(ENTRY_DAY, "100000", 1005, 1012, 1005, 1000),
        ])
        result = run_sweep([sample], 0.006)
        expected = len(ENTRY_RULES) * len(SWEEP_TP) * len(SWEEP_SL) * len(SWEEP_DAYS)
        self.assertEqual(result["combo_count"], expected)
        means = [combo["overall"]["mean"] or -1 for combo in result["combos"]]
        self.assertEqual(means, sorted(means, reverse=True))

    def test_rule_stats_cover_every_rule(self):
        sample = _sample(_flat_day(ENTRY_DAY))
        result = run_sweep([sample], 0.006)
        self.assertEqual(set(result["rule_stats"]), set(ENTRY_RULES))
        for stat in result["rule_stats"].values():
            self.assertEqual(stat["entry_found"] + stat["no_entry"], 1)


if __name__ == "__main__":
    unittest.main()
