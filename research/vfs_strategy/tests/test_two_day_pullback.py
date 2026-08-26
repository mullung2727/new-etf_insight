"""2일 눌림확인 전략 회귀 테스트 — 네트워크 없이 합성 일봉/분봉으로 검증.

검증 항목:
  - 신호: D 거래량폭발+장대양봉, D+1 거래량급감, D+2 음봉+거래량연속감소+중간값 위
  - 조건 하나씩 깨뜨리면 신호 제외
  - 거래정지 가드: D->D+1->D+2 가 시장 연속 거래일이 아니면 제외
  - 진입: D+2 종가 위 AND 당일 시가 대비 문턱 이상인 첫 봉, 미달이면 매수 없음
  - horizon 미확보 신호는 후보에서 제외
"""
import tempfile
import unittest
from pathlib import Path

import duckdb

from research.vfs_strategy.two_day_pullback import (
    VOL_MA_DAYS,
    find_entry,
    load_candidates,
)

FILLER = "000001"
TICKER = "005930"
WARMUP = VOL_MA_DAYS


def _dates(count):
    return [f"202606{i + 1:02d}" for i in range(count)]


def _bar(date, time, open_, high, low, close):
    return {"timestamp": f"{date}{time}", "date": date, "time": time,
            "open": open_, "high": high, "low": low, "close": close, "volume": 10}


class _Db:
    """합성 ohlcv — 기본값은 신호가 성립하는 형태."""

    def __init__(self, tmp, *, d_volume=1000, d_close=120, v1=150, v2=100,
                 d2_open=118, d2_close=115, halt_after_d=False, tail=3):
        self.path = Path(tmp) / "krx.duckdb"
        con = duckdb.connect(str(self.path))
        from scripts.build_krx_ohlcv import _CREATE_OHLCV

        con.execute(_CREATE_OHLCV)
        total = WARMUP + 3 + tail
        dates = _dates(total)

        def insert(date, ticker, open_, close, volume):
            con.execute(
                "INSERT INTO ohlcv (date, ticker, market, open, high, low, close, volume) "
                "VALUES (?, ?, 'KOSPI', ?, ?, ?, ?, ?)",
                [date, ticker, open_, max(open_, close), min(open_, close), close, volume])

        for date in dates:                      # 시장 거래일 달력 유지용
            insert(date, FILLER, 100, 100, 100)
        for i in range(WARMUP):
            insert(dates[i], TICKER, 100, 100, 100)
        insert(dates[WARMUP], TICKER, 100, d_close, d_volume)            # D
        d1_index = WARMUP + 2 if halt_after_d else WARMUP + 1            # 정지 하루 삽입
        insert(dates[d1_index], TICKER, 119, 118, v1)                    # D+1
        insert(dates[d1_index + 1], TICKER, d2_open, d2_close, v2)       # D+2
        for i in range(d1_index + 2, total):
            insert(dates[i], TICKER, 115, 115, 100)
        con.close()
        self.signal_date = dates[d1_index + 1]


class TestSignal(unittest.TestCase):
    def _codes(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            db = _Db(tmp, **kwargs)
            return [c["ticker"] for c in load_candidates(db.path)], db.signal_date

    def test_all_conditions_met(self):
        codes, signal_date = self._codes()
        self.assertEqual(codes, [TICKER])

    def test_volume_spike_too_small(self):
        # MA20 = (100*19 + 250)/20 = 107.5 → 250 < 215 이 아니라 250 >= 215 이므로
        # x2 를 못 넘기려면 더 낮춰야 한다
        codes, _ = self._codes(d_volume=150)
        self.assertEqual(codes, [])

    def test_d1_volume_not_dry(self):
        codes, _ = self._codes(v1=900)          # 1000 * 0.2 = 200 이상
        self.assertEqual(codes, [])

    def test_d2_volume_not_decreasing(self):
        codes, _ = self._codes(v2=200)          # v1(150) 보다 큼
        self.assertEqual(codes, [])

    def test_d2_is_white_candle(self):
        codes, _ = self._codes(d2_open=115, d2_close=118)
        self.assertEqual(codes, [])

    def test_d2_close_below_body_mid(self):
        codes, _ = self._codes(d2_close=105)    # (120+100)/2 = 110 아래
        self.assertEqual(codes, [])

    def test_halt_between_d_and_d1_is_excluded(self):
        codes, _ = self._codes(halt_after_d=True)
        self.assertEqual(codes, [])

    def test_horizon_shortfall_is_dropped(self):
        codes, _ = self._codes(tail=0)          # 진입일 다음 거래일이 없음
        self.assertEqual(codes, [])

    def test_trading_dates_start_at_entry_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _Db(tmp)
            candidate = load_candidates(db.path)[0]
            self.assertGreater(candidate["trading_dates"][0], candidate["signal_date"])
            self.assertEqual(len(candidate["trading_dates"]), 2)


class TestFindEntry(unittest.TestCase):
    DATE = "20260701"

    def _day(self):
        return [
            _bar(self.DATE, "090000", 100, 101, 99, 100),     # 시가 100
            _bar(self.DATE, "090100", 100, 102, 100, 101),    # +1%
            _bar(self.DATE, "090200", 101, 103, 101, 103),    # +3%
        ]

    def test_threshold_picks_first_qualifying_bar(self):
        entry = find_entry(self._day(), signal_close=99, threshold=0.01)
        self.assertEqual(entry["entry_price"], 101)

    def test_higher_threshold_delays_entry(self):
        entry = find_entry(self._day(), signal_close=99, threshold=0.03)
        self.assertEqual(entry["entry_price"], 103)

    def test_no_entry_when_threshold_never_reached(self):
        self.assertIsNone(find_entry(self._day(), signal_close=99, threshold=0.10))

    def test_no_entry_while_below_signal_close(self):
        self.assertIsNone(find_entry(self._day(), signal_close=200, threshold=0.0))

    def test_empty_day_returns_none(self):
        self.assertIsNone(find_entry([], signal_close=99, threshold=0.0))


if __name__ == "__main__":
    unittest.main()
