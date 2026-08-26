"""VFS 1분봉 백테스트 회귀 테스트 — 네트워크 호출 없이 합성 분봉으로 검증.

검증 항목:
  - 진입: 신호일 15:19 봉 종가로 체결, 해당 봉 없으면 표본 제외
  - 청산: TP 선터치 / SL 선터치 / 동일봉 동시 터치 SL 우선 / 3거래일 강제청산
  - 비용: net_return = gross_return - cost_rate
  - 후보: 보유 horizon(3거래일) 미확보 신호는 제외
"""
import tempfile
import unittest
from pathlib import Path

import duckdb

from research.vfs_strategy.backtest import (
    SWEEP_DAYS,
    SWEEP_SL,
    SWEEP_TP,
    run_sweep,
    COST_RATE,
    ENTRY_TIME,
    STRATEGY,
    find_entry,
    load_candidates,
    run_backtest,
)

DATES = ["20260601", "20260602", "20260603", "20260604"]
ENTRY_PRICE = 1000


def _bar(date, time, open_, high, low, close, volume=100):
    return {
        "timestamp": f"{date}{time}", "date": date, "time": time,
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    }


def _flat_day(date, price=ENTRY_PRICE):
    """TP도 SL도 닿지 않는 하루치(장 시작/마감 2봉)."""
    return [
        _bar(date, "090000", price, price, price, price),
        _bar(date, "153000", price, price, price, price),
    ]


def _sample(after_entry_bars, dates=DATES):
    """신호일 15:19 진입봉 + 이후 경로로 표본 하나를 만든다."""
    bars = [_bar(dates[0], ENTRY_TIME, ENTRY_PRICE, ENTRY_PRICE, ENTRY_PRICE, ENTRY_PRICE)]
    bars += after_entry_bars
    return {
        "ticker": "005930", "signal_date": dates[0], "trading_dates": dates,
        "bars": bars,
        "entry": {"entry_price": ENTRY_PRICE, "entry_timestamp": f"{dates[0]}{ENTRY_TIME}"},
    }


class TestFindEntry(unittest.TestCase):
    def test_uses_entry_time_close(self):
        bars = [
            _bar("20260601", "151800", 990, 995, 985, 990),
            _bar("20260601", ENTRY_TIME, 1000, 1010, 998, 1005),
        ]
        entry = find_entry(bars, "20260601")
        self.assertEqual(entry, {"entry_price": 1005, "entry_timestamp": f"20260601{ENTRY_TIME}"})

    def test_missing_entry_bar_returns_none(self):
        bars = [_bar("20260601", "151800", 990, 995, 985, 990)]
        self.assertIsNone(find_entry(bars, "20260601"))

    def test_entry_bar_of_other_date_is_ignored(self):
        bars = [_bar("20260602", ENTRY_TIME, 1000, 1000, 1000, 1000)]
        self.assertIsNone(find_entry(bars, "20260601"))


class TestRunBacktest(unittest.TestCase):
    def test_take_profit_hit(self):
        path = _flat_day(DATES[1]) + [
            _bar(DATES[2], "100000", 1000, 1060, 1000, 1055),   # high >= 1050 → TP
        ] + _flat_day(DATES[3])
        result = run_backtest([_sample(path)])
        self.assertEqual(result["simulated"], 1)
        trade = result["trades"][0]
        self.assertEqual(trade["exit_reason"], "tp")
        self.assertAlmostEqual(trade["gross_return"], STRATEGY["tp"])
        self.assertAlmostEqual(trade["net_return"], STRATEGY["tp"] - COST_RATE)

    def test_stop_loss_hit(self):
        path = [_bar(DATES[1], "090000", 1000, 1000, 960, 965)] + _flat_day(DATES[2]) + _flat_day(DATES[3])
        trade = run_backtest([_sample(path)])["trades"][0]
        self.assertEqual(trade["exit_reason"], "sl")
        self.assertAlmostEqual(trade["gross_return"], -STRATEGY["sl"])

    def test_same_minute_both_touch_uses_stop_loss(self):
        path = [_bar(DATES[1], "090000", 1000, 1060, 960, 1000)] + _flat_day(DATES[2]) + _flat_day(DATES[3])
        trade = run_backtest([_sample(path)])["trades"][0]
        self.assertEqual(trade["exit_reason"], "same_minute_both_sl")
        self.assertAlmostEqual(trade["gross_return"], -STRATEGY["sl"])

    def test_forced_close_on_last_holding_day(self):
        path = _flat_day(DATES[1]) + _flat_day(DATES[2]) + [
            _bar(DATES[3], "090000", 1000, 1000, 1000, 1000),
            _bar(DATES[3], "153000", 1000, 1020, 1000, 1020),   # 마지막 봉 종가로 청산
        ]
        trade = run_backtest([_sample(path)])["trades"][0]
        self.assertEqual(trade["exit_reason"], "forced_close")
        self.assertAlmostEqual(trade["gross_return"], 0.02)

    def test_missing_horizon_bars_are_dropped(self):
        path = _flat_day(DATES[1])                              # DATES[2], DATES[3] 없음
        result = run_backtest([_sample(path)])
        self.assertEqual(result["simulated"], 0)
        self.assertEqual(result["dropped_no_exit_path"], 1)

    def test_exit_reason_counts_match_simulated(self):
        tp = _sample(_flat_day(DATES[1]) + [_bar(DATES[2], "100000", 1000, 1060, 1000, 1055)] + _flat_day(DATES[3]))
        sl = _sample([_bar(DATES[1], "090000", 1000, 1000, 960, 965)] + _flat_day(DATES[2]) + _flat_day(DATES[3]))
        result = run_backtest([tp, sl])
        self.assertEqual(sum(result["exit_reasons"].values()), result["simulated"])
        self.assertEqual(result["overall"]["count"], 2)


class TestRunSweep(unittest.TestCase):
    def test_grid_size_and_shorter_days_reuse_same_bars(self):
        path = _flat_day(DATES[1]) + [
            _bar(DATES[2], "100000", 1000, 1060, 1000, 1055),   # D+2 에 TP 터치
        ] + _flat_day(DATES[3])
        sweep = run_sweep([_sample(path)])
        self.assertEqual(sweep["combo_count"], len(SWEEP_TP) * len(SWEEP_SL) * len(SWEEP_DAYS))
        by_id = {combo["id"]: combo for combo in sweep["combos"]}
        # 보유 1일은 D+2 봉을 보지 못하므로 같은 표본이라도 TP 로 못 끝난다
        self.assertNotIn("tp", by_id["tp5%_sl3%_d1"]["exit_reasons"])
        self.assertIn("tp", by_id["tp5%_sl3%_d3"]["exit_reasons"])

    def test_ranked_by_overall_mean_desc(self):
        path = _flat_day(DATES[1]) + [
            _bar(DATES[2], "100000", 1000, 1080, 1000, 1075),
        ] + _flat_day(DATES[3])
        means = [combo["overall"]["mean"] or -1 for combo in run_sweep([_sample(path)])["combos"]]
        self.assertEqual(means, sorted(means, reverse=True))


class TestLoadCandidates(unittest.TestCase):
    """합성 ohlcv 로 VFS 신호를 만들고 horizon 컷을 확인한다."""

    def _build_db(self, path: Path, trailing_days: int) -> None:
        from scripts.build_krx_ohlcv import _CREATE_OHLCV

        con = duckdb.connect(str(path))
        con.execute(_CREATE_OHLCV)
        day = 0

        def insert(open_, close, volume):
            nonlocal day
            date = f"202606{day + 1:02d}"
            con.execute(
                "INSERT INTO ohlcv (date, ticker, market, open, high, low, close, volume) "
                "VALUES (?, '005930', 'KOSPI', ?, ?, ?, ?, ?)",
                [date, open_, max(open_, close), min(open_, close), close, volume],
            )
            day += 1

        for _ in range(20):
            insert(100, 100, 100)
        insert(100, 120, 1000)          # D: 거래량 폭발 + 장대양봉
        insert(118, 115, 150)           # D+1: 신호일
        for _ in range(trailing_days):
            insert(115, 115, 100)
        con.close()

    def test_signal_with_full_horizon_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "krx.duckdb"
            self._build_db(db, trailing_days=STRATEGY["days"])
            candidates = load_candidates(db, "20260601", "20260630")
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["ticker"], "005930")
            self.assertEqual(len(candidates[0]["trading_dates"]), STRATEGY["days"] + 1)
            self.assertEqual(candidates[0]["trading_dates"][0], candidates[0]["signal_date"])

    def test_signal_without_full_horizon_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "krx.duckdb"
            self._build_db(db, trailing_days=STRATEGY["days"] - 1)
            self.assertEqual(load_candidates(db, "20260601", "20260630"), [])


if __name__ == "__main__":
    unittest.main()
