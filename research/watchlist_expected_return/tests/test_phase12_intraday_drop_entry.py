"""phase12 장중 과도 하락 매수의 인과관계·청산 규칙 합성 분봉 검증."""
from __future__ import annotations

import unittest

from research.watchlist_expected_return.phase12_intraday_drop_entry import (
    COST_RATE,
    combination_metrics,
    find_drop_entry,
    run_combination,
    scan_day,
    simulate_exit,
    split_population,
)

D1, D2, D3, D4, D5, D6, D7, D8 = (f"2026060{n}" for n in range(1, 9))


def bar(date: str, time: str, open_: float, high: float, low: float, close: float) -> dict:
    return {"date": date, "time": time, "timestamp": date + time,
            "open": open_, "high": high, "low": low, "close": close, "volume": 100}


def flat_day(date: str, price: float, times: tuple[str, ...] = ("090000", "100000", "110000")) -> list[dict]:
    return [bar(date, time, price, price, price, price) for time in times]


def daily(date: str, open_: float, high: float, low: float, close: float) -> dict:
    return {"date": date, "open": open_, "high": high, "low": low, "close": close, "volume": 100}


def make_row(future: list[dict], base_close: float = 1000, prior_low: float = 990) -> dict:
    return {"date": "20260529", "ticker": "000001", "base_close": base_close,
            "history": [daily("20260529", base_close, base_close, prior_low, base_close)],
            "future": future}


class ScanDayTest(unittest.TestCase):
    def test_entry_is_next_bar_open_not_signal_close(self):
        bars = [bar(D1, "090000", 1000, 1000, 1000, 1000),
                bar(D1, "090100", 990, 990, 960, 960),      # -4% 신호봉
                bar(D1, "090200", 955, 955, 950, 951)]
        signal = scan_day(bars, 1000, 1000, "prev_close", 0.03)
        self.assertEqual(signal["signal_time"], "090100")
        self.assertEqual(signal["entry_timestamp"], D1 + "090200")
        self.assertEqual(signal["entry_price"], 955)

    def test_running_high_excludes_future_bars(self):
        # 09:02 고가 1200 은 09:01 판정에 들어가면 안 된다 (1200*0.95=1140 > 970)
        bars = [bar(D1, "090000", 1000, 1000, 1000, 1000),
                bar(D1, "090100", 1000, 1000, 970, 970),
                bar(D1, "090200", 970, 1200, 970, 1200),
                bar(D1, "090300", 1150, 1150, 1100, 1100)]
        self.assertIsNone(scan_day(bars, 1000, 1000, "running_high", 0.05))
        # 고점 1200 이 관측된 뒤에는 09:03 종가 1100 이 -5% 선(1140) 아래가 된다
        bars.append(bar(D1, "090400", 1100, 1100, 1000, 1030))
        signal = scan_day(bars, 1000, 1000, "running_high", 0.05)
        self.assertEqual(signal["signal_time"], "090300")
        self.assertEqual(signal["reference_price"], 1200)
        self.assertEqual(signal["entry_price"], 1100)

    def test_gap_down_at_first_bar_counts(self):
        bars = [bar(D1, "090000", 900, 900, 890, 890), bar(D1, "090100", 895, 895, 880, 885)]
        signal = scan_day(bars, 900, 1000, "prev_close", 0.05)
        self.assertEqual(signal["signal_time"], "090000")
        self.assertEqual(signal["entry_price"], 895)

    def test_no_entry_when_next_bar_is_after_1519(self):
        bars = [bar(D1, "151900", 960, 960, 950, 950), bar(D1, "152000", 950, 950, 940, 940)]
        self.assertIsNone(scan_day(bars, 1000, 1000, "prev_close", 0.03))

    def test_signal_bar_after_1519_is_ignored(self):
        bars = [bar(D1, "152000", 900, 900, 890, 890), bar(D1, "152100", 890, 890, 880, 885)]
        self.assertIsNone(scan_day(bars, 1000, 1000, "prev_close", 0.03))

    def test_missing_prev_close_gives_no_signal(self):
        bars = [bar(D1, "090000", 900, 900, 890, 890), bar(D1, "090100", 890, 890, 880, 885)]
        self.assertIsNone(scan_day(bars, 900, None, "prev_close", 0.05))


class WatchWindowTest(unittest.TestCase):
    def _bars(self, days: dict[str, list[dict]]) -> dict[str, list[dict]]:
        return days

    def test_prev_close_reference_moves_each_day(self):
        row = make_row([daily(D1, 1000, 1000, 1000, 1000), daily(D2, 960, 960, 940, 950)])
        bars = self._bars({D1: flat_day(D1, 1000),
                           D2: [bar(D2, "090000", 960, 960, 940, 940), bar(D2, "090100", 945, 950, 940, 945)]})
        # D+1 은 base_close 1000 대비 0%, D+2 는 D+1 종가 1000 대비 -6%
        entry = find_drop_entry(row, bars, "prev_close", 0.05)
        self.assertEqual(entry["entry_date"], D2)
        self.assertEqual(entry["watch_day"], 2)
        self.assertEqual(entry["reference_price"], 1000)

    def test_day_open_reference_uses_daily_open_per_day(self):
        row = make_row([daily(D1, 1000, 1000, 995, 1000), daily(D2, 800, 800, 750, 760)])
        bars = self._bars({D1: flat_day(D1, 1000),
                           D2: [bar(D2, "090000", 800, 800, 750, 755), bar(D2, "090100", 756, 760, 750, 758)]})
        entry = find_drop_entry(row, bars, "day_open", 0.05)
        self.assertEqual(entry["reference_price"], 800)   # 전일 종가 1000 이 아니다
        self.assertEqual(entry["entry_price"], 756)

    def test_watchlist_high_reference_stays_fixed_across_days(self):
        row = make_row([daily(D1, 1100, 1100, 1100, 1100), daily(D2, 985, 990, 940, 950)])
        row["history"] = [daily("20260529", 1150, 1200, 1050, 1100)]     # 편입일 고가 1200
        bars = {D1: flat_day(D1, 1100),                                   # 1200*0.8=960 미달
                D2: [bar(D2, "090000", 985, 985, 940, 950), bar(D2, "090100", 952, 960, 940, 945)]}
        entry = find_drop_entry(row, bars, "watchlist_high", 0.20)
        self.assertEqual(entry["entry_date"], D2)
        self.assertEqual(entry["reference_price"], 1200)   # 편입일 고가로 고정
        self.assertEqual(entry["trigger_price"], 960.0)
        self.assertEqual(entry["entry_price"], 952)
        # 같은 경로를 전일 종가(1100) 기준으로 보면 -20% 선이 880 이라 신호가 없다
        self.assertIsNone(find_drop_entry(row, bars, "prev_close", 0.20))

    def test_up_direction_buys_on_breakout_above_reference(self):
        row = make_row([daily(D1, 1180, 1180, 1180, 1180), daily(D2, 1010, 1300, 1000, 1290)])
        row["history"] = [daily("20260529", 1000, 1200, 950, 1100)]      # 편입일 고가 1200
        bars = {D1: flat_day(D1, 1180),                                   # 1200*1.05=1260 미달
                D2: [bar(D2, "090000", 1010, 1270, 1000, 1265),
                     bar(D2, "090100", 1268, 1300, 1260, 1290)]}
        entry = find_drop_entry(row, bars, "watchlist_high", 0.05, "up")
        self.assertEqual(entry["entry_date"], D2)
        self.assertEqual(entry["trigger_price"], 1260.0)
        self.assertEqual(entry["entry_price"], 1268)
        # 같은 경로를 하락 방향으로 보면 신호가 없다
        self.assertIsNone(find_drop_entry(row, bars, "watchlist_high", 0.05, "down"))

    def test_watches_five_days_only(self):
        future = [daily(day, 1000, 1000, 1000, 1000) for day in (D1, D2, D3, D4, D5)]
        future.append(daily(D6, 800, 800, 700, 700))
        bars = {day: flat_day(day, 1000) for day in (D1, D2, D3, D4, D5)}
        bars[D6] = [bar(D6, "090000", 800, 800, 700, 700), bar(D6, "090100", 700, 700, 690, 695)]
        self.assertIsNone(find_drop_entry(make_row(future), bars, "prev_close", 0.05))


class ExitTest(unittest.TestCase):
    def entry(self, price: float, timestamp: str, include: bool = True, date: str = D1) -> dict:
        return {"entry_price": price, "entry_timestamp": timestamp,
                "include_entry_bar": include, "entry_date": date}

    def test_entry_bar_can_hit_tp_same_day(self):
        bars = {D1: [bar(D1, "090000", 1000, 1000, 1000, 1000),
                     bar(D1, "090100", 1000, 1060, 995, 1050)],
                D2: flat_day(D2, 1050), D3: flat_day(D3, 1050), D4: flat_day(D4, 1050)}
        outcome = simulate_exit(bars, [D1, D2, D3, D4], self.entry(1000, D1 + "090100"))
        self.assertEqual(outcome["exit_reason"], "tp")
        self.assertEqual(outcome["exit_timestamp"], D1 + "090100")
        self.assertEqual(outcome["holding_days"], 0)

    def test_signal_bar_low_is_not_used_after_entry(self):
        # 신호봉(09:00) 저가 900 은 SL(950) 아래지만 매수 전이라 청산 판정에 못 쓴다
        bars = {D1: [bar(D1, "090000", 1000, 1000, 900, 960), bar(D1, "090100", 1000, 1010, 990, 1000)],
                D2: flat_day(D2, 1000), D3: flat_day(D3, 1000), D4: flat_day(D4, 1000)}
        outcome = simulate_exit(bars, [D1, D2, D3, D4], self.entry(1000, D1 + "090100"))
        self.assertEqual(outcome["exit_reason"], "forced_1519")

    def test_same_bar_tp_and_sl_prefers_sl(self):
        bars = {D1: [bar(D1, "090100", 1000, 1060, 940, 1000)],
                D2: flat_day(D2, 1000), D3: flat_day(D3, 1000), D4: flat_day(D4, 1000)}
        outcome = simulate_exit(bars, [D1, D2, D3, D4], self.entry(1000, D1 + "090100"))
        self.assertEqual(outcome["exit_reason"], "same_minute_both_sl")
        self.assertAlmostEqual(outcome["gross_return"], -0.05)

    def test_gap_through_level_exits_at_bar_open(self):
        bars = {D1: [bar(D1, "090100", 1000, 1000, 1000, 1000)],
                D2: [bar(D2, "090000", 900, 905, 880, 890)] + flat_day(D2, 890, ("100000",)),
                D3: flat_day(D3, 890), D4: flat_day(D4, 890)}
        outcome = simulate_exit(bars, [D1, D2, D3, D4], self.entry(1000, D1 + "090100"))
        self.assertEqual(outcome["exit_reason"], "gap_sl")
        self.assertEqual(outcome["exit_price"], 900)
        self.assertAlmostEqual(outcome["gross_return"], -0.1)

    def test_forced_exit_at_1519_of_third_trading_day(self):
        bars = {D1: [bar(D1, "090100", 1000, 1000, 1000, 1000)], D2: flat_day(D2, 1010),
                D3: flat_day(D3, 1020),
                D4: [bar(D4, "151800", 1030, 1030, 1030, 1030),
                     bar(D4, "151900", 1030, 1030, 1030, 1031),
                     bar(D4, "152000", 1031, 1200, 1031, 1200)]}     # 15:20 이후는 무시
        outcome = simulate_exit(bars, [D1, D2, D3, D4], self.entry(1000, D1 + "090100"))
        self.assertEqual(outcome["exit_reason"], "forced_1519")
        self.assertEqual(outcome["exit_timestamp"], D4 + "151900")
        self.assertEqual(outcome["holding_days"], 3)

    def test_missing_hold_day_bars_returns_none(self):
        bars = {D1: [bar(D1, "090100", 1000, 1000, 1000, 1000)], D2: flat_day(D2, 1000),
                D3: [], D4: flat_day(D4, 1000)}
        self.assertIsNone(simulate_exit(bars, [D1, D2, D3, D4], self.entry(1000, D1 + "090100")))

    def test_max_adverse_stops_at_exit(self):
        bars = {D1: [bar(D1, "090100", 1000, 1000, 980, 990)], D2: [bar(D2, "090000", 1000, 1060, 970, 1055)],
                D3: [bar(D3, "090000", 1055, 1055, 500, 600)], D4: flat_day(D4, 600)}
        outcome = simulate_exit(bars, [D1, D2, D3, D4], self.entry(1000, D1 + "090100"))
        self.assertEqual(outcome["exit_reason"], "tp")
        self.assertAlmostEqual(outcome["max_adverse"], -0.03)   # D3 폭락은 청산 이후라 제외


class CombinationTest(unittest.TestCase):
    def _row(self, watchlist_date: str, ticker: str, future: list[dict]) -> dict:
        row = make_row(future)
        row["date"], row["ticker"] = watchlist_date, ticker
        return row

    def _drop_day_bars(self, date: str) -> list[dict]:
        return [bar(date, "090000", 1000, 1000, 940, 940), bar(date, "090100", 945, 950, 940, 945)]

    def test_cost_deducted_once_and_second_entry_blocked_while_held(self):
        future = [daily(D1, 1000, 1000, 940, 945), daily(D2, 945, 950, 940, 945),
                  daily(D3, 945, 950, 940, 945), daily(D4, 945, 950, 940, 945),
                  daily(D5, 945, 950, 940, 945), daily(D6, 945, 950, 940, 945),
                  daily(D7, 945, 950, 940, 945), daily(D8, 945, 950, 940, 945)]
        rows = [self._row("20260528", "000001", future), self._row("20260529", "000001", future)]
        bars = {D1: self._drop_day_bars(D1)}
        bars.update({day: flat_day(day, 945) for day in (D2, D3, D4, D5, D6, D7, D8)})
        by_row = {f"{row['ticker']}_{row['date']}": bars for row in rows}
        result = run_combination(
            rows, by_row, lambda row, day_bars: find_drop_entry(row, day_bars, "prev_close", 0.05),
            "prev_close_5%",
        )
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(result["blocked_by_holding"], 1)
        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertAlmostEqual(trade["net_return"], trade["gross_return"] - COST_RATE)
        metrics = combination_metrics(result, len(rows))
        self.assertEqual(metrics["count"], 1)
        self.assertAlmostEqual(metrics["mean"], trade["gross_return"] - COST_RATE, places=6)

    def test_incomplete_path_is_not_counted_as_no_buy(self):
        future = [daily(D1, 1000, 1000, 940, 945), daily(D2, 945, 950, 940, 945),
                  daily(D3, 945, 950, 940, 945), daily(D4, 945, 950, 940, 945)]
        rows = [self._row("20260529", "000001", future)]
        bars = {D1: self._drop_day_bars(D1), D2: flat_day(D2, 945), D3: [], D4: flat_day(D4, 945)}
        result = run_combination(
            rows, {"000001_20260529": bars},
            lambda row, day_bars: find_drop_entry(row, day_bars, "prev_close", 0.05), "prev_close_5%",
        )
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["incomplete_exit_path"], 1)
        self.assertEqual(result["trades"], [])


class SplitTest(unittest.TestCase):
    def test_boundary_rows_are_dropped(self):
        dates = [f"202605{day:02d}" for day in range(1, 11)]
        rows = []
        for index, date in enumerate(dates):
            future = [daily(f"2026060{n}", 1000, 1000, 1000, 1000) for n in range(1, 9)]
            row = make_row(future)
            row["date"], row["ticker"] = date, f"00000{index}"
            rows.append(row)
        split_date, development, validation, dropped = split_population(rows)
        self.assertEqual(split_date, dates[6])
        self.assertEqual(len(validation), 4)
        self.assertEqual(len(development) + dropped, 6)
        self.assertTrue(all(row["date"] < split_date for row in development))


if __name__ == "__main__":
    unittest.main()
