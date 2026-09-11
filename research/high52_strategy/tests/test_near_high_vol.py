"""신고가 근처 + 거래량 양봉 전략 회귀 테스트 — 합성 데이터.

검증 항목:
  - 신호: 52주 고가 95% 이내 / 거래량 20일 평균 3배 / 양봉, 하나라도 빠지면 무신호
  - gradual 신호: 평균 거래량 5>20>60 정배열 / 최근 20일 2배 초과일 없음 / 양봉
  - 고정 손절: 매수가 기준 트리거, 진입 당일 손절, 갭하락 시가 체결, 만기 종가
  - 중복: 보유 중 재신호 무시, 청산일 당일 신호는 받음
"""
import tempfile
import unittest
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from research.high52_strategy.backtest import LOOKBACK, _BASE_SQL
from research.high52_strategy.near_high_vol import (_GRADUAL_SQL, _SIG_SQL, dedup_positions,
                                                    fixed_exit)

FILLER = "000001"
TICKER = "005930"


def _signals(o, h, l, c, v, vol=lambda i, n: 1000, sql=_SIG_SQL):
    """룩백 구간(고가 100, 거래량 vol(i, n)) 뒤 마지막 날 봉 하나로 신호 여부 확인."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "krx.duckdb"
        con = duckdb.connect(str(path))
        con.execute("""CREATE TABLE ohlcv(date VARCHAR, ticker VARCHAR, market VARCHAR,
            open INTEGER, high INTEGER, low INTEGER, close INTEGER, volume BIGINT,
            trading_value BIGINT, market_cap BIGINT, list_shrs BIGINT)""")
        dates = [d.strftime("%Y%m%d") for d in
                 pd.date_range("2024-01-01", periods=LOOKBACK + 6, freq="D")]
        rows = [(d, FILLER, 100, 100, 100, 100, 1000) for d in dates]
        n = len(dates)
        rows += [(d, TICKER, 95, 100, 90, 95, vol(i, n)) for i, d in enumerate(dates[:-1])]
        rows.append((dates[-1], TICKER, o, h, l, c, v))
        con.executemany("INSERT INTO ohlcv VALUES (?,?,'KOSPI',?,?,?,?,?,"
                        "100000000000,500000000000,1000000)", rows)
        con.close()
        con = duckdb.connect(str(path), read_only=True)
        con.execute(_BASE_SQL)
        con.execute(sql)
        out = [r[0] for r in con.execute("SELECT ticker FROM sig").fetchall()]
        con.close()
        return out


class TestSignalSql(unittest.TestCase):
    def test_near_high_volume_bullish_is_signal(self):
        self.assertEqual(_signals(93, 98, 92, 97, 3000), [TICKER])

    def test_breakout_is_included(self):
        self.assertEqual(_signals(99, 112, 98, 110, 3000), [TICKER])

    def test_too_far_below_high(self):
        self.assertEqual(_signals(90, 95, 89, 94, 3000), [])   # 94 < 100 x 0.95

    def test_volume_below_3x(self):
        self.assertEqual(_signals(93, 98, 92, 97, 2999), [])

    def test_bearish_candle(self):
        self.assertEqual(_signals(98, 99, 95, 97, 3000), [])   # 종가 < 시가


def _ramp(i, n):
    """마지막 60일 동안 하루 +20씩 완만 증가 (마지막 전날 2180)."""
    return 1000 + max(0, i - (n - 61)) * 20


class TestGradualSql(unittest.TestCase):
    def _sig(self, v=2200, vol=_ramp, o=93, c=97):
        return _signals(o, 98, 92, c, v, vol=vol, sql=_GRADUAL_SQL)

    def test_gradual_ramp_is_signal(self):
        self.assertEqual(self._sig(), [TICKER])

    def test_flat_volume_is_not_aligned(self):
        self.assertEqual(self._sig(v=1000, vol=lambda i, n: 1000), [])   # 5 = 20 = 60일 평균

    def test_spike_in_last_20_days(self):
        # 최근 5일 안 폭발이라 정배열은 유지됨 → 폭발 조건만으로 탈락해야 함
        spiked = lambda i, n: 6000 if i == n - 3 else _ramp(i, n)
        self.assertEqual(self._sig(vol=spiked), [])

    def test_spike_on_signal_day(self):
        self.assertEqual(self._sig(v=5000), [])                        # 당일 2배 초과

    def test_bearish_candle(self):
        self.assertEqual(self._sig(o=98, c=97), [])


class TestFixedExit(unittest.TestCase):
    def _run(self, bars, stop=0.20):
        o, h, l, c = (np.array([b[i] for b in bars], float) for i in range(4))
        return fixed_exit(o, h, l, c, stop)

    def test_stop_is_fixed_to_entry(self):
        # 고점이 200까지 가도 트리거는 매수가 100 x 0.8 = 80 그대로
        ret, days, stopped = self._run([(100, 200, 99, 190), (190, 190, 79, 85)])
        self.assertAlmostEqual(ret, 80 / 100 - 1, places=6)
        self.assertEqual((days, stopped), (2, True))

    def test_stop_on_entry_day(self):
        ret, days, stopped = self._run([(100, 101, 75, 78), (78, 90, 70, 88)])
        self.assertAlmostEqual(ret, 80 / 100 - 1, places=6)
        self.assertEqual((days, stopped), (1, True))

    def test_gap_down_fills_at_open(self):
        ret, days, _ = self._run([(100, 105, 95, 100), (60, 65, 55, 58)])
        self.assertAlmostEqual(ret, 60 / 100 - 1, places=6)
        self.assertEqual(days, 2)

    def test_expiry_closes_at_last_bar(self):
        ret, days, stopped = self._run([(100, 110, 85, 108), (108, 130, 81, 125)])
        self.assertAlmostEqual(ret, 125 / 100 - 1, places=6)
        self.assertEqual((days, stopped), (2, False))


class TestDedupPositions(unittest.TestCase):
    def test_signal_while_holding_is_ignored(self):
        df = pd.DataFrame([
            {"ticker": "A", "ms": 10, "exit_ms": 20},
            {"ticker": "A", "ms": 15, "exit_ms": 30},   # 보유 중 → 무시
            {"ticker": "A", "ms": 20, "exit_ms": 40},   # 청산일 당일 신호 → 받음
            {"ticker": "B", "ms": 15, "exit_ms": 25},   # 다른 종목은 독립
        ])
        out = dedup_positions(df)
        self.assertEqual(sorted(zip(out.ticker, out.ms)), [("A", 10), ("A", 20), ("B", 15)])


if __name__ == "__main__":
    unittest.main()
