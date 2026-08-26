"""52주 신고가 전략 회귀 테스트 — 네트워크 없이 합성 데이터로 검증.

검증 항목:
  - 신호: 직전 250거래일 고가 돌파, 거래정지로 룩백이 늘어난 경우 배제
  - 트레일링 청산: 트리거·고점갱신·갭하락·미발동 만기
  - 베이스 길이 밴드 경계, 시총 버킷
  - 필터: 스팩 제외 / 거래대금 / 깊이 / 부채비율 / 종목·밴드당 dedup
"""
import tempfile
import unittest
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from research.high52_strategy.backtest import (BAND_LABELS, LOOKBACK, _BASE_SQL,
                                               apply_filters, base_band, cap_bucket,
                                               summarize, trailing_exit)

FILLER = "000001"
TICKER = "005930"


def _dates(n):
    d = pd.date_range("2024-01-01", periods=n, freq="D")
    return [x.strftime("%Y%m%d") for x in d]


class _Db:
    """합성 ohlcv. 기본값은 마지막 날 신고가가 성립하는 형태."""

    def __init__(self, tmp, *, break_high=True, halt_gap=0, warmup=LOOKBACK + 5):
        self.path = Path(tmp) / "krx.duckdb"
        con = duckdb.connect(str(self.path))
        con.execute("""CREATE TABLE ohlcv(date VARCHAR, ticker VARCHAR, market VARCHAR,
            open INTEGER, high INTEGER, low INTEGER, close INTEGER, volume BIGINT,
            trading_value BIGINT, market_cap BIGINT, list_shrs BIGINT,
            PRIMARY KEY(date, ticker))""")
        total = warmup + halt_gap + 1
        dates = _dates(total)

        def ins(date, ticker, hi, lo, close):
            con.execute("INSERT INTO ohlcv VALUES (?,?,'KOSPI',?,?,?,?,1000,"
                        "100000000000,500000000000,1000000)",
                        [date, ticker, close, hi, lo, close])

        for d in dates:                       # 시장 거래일 달력 유지용
            ins(d, FILLER, 100, 100, 100)
        for d in dates[:warmup]:              # 룩백 구간: 고가 100 고정
            ins(d, TICKER, 100, 90, 95)
        # halt_gap 만큼 거래 없음 → 룩백 구간이 달력상 늘어남
        last = dates[-1]
        ins(last, TICKER, 120 if break_high else 99, 95, 110 if break_high else 96)
        con.close()
        self.signal_date = last


class TestSignalSql(unittest.TestCase):
    def _signals(self, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            db = _Db(tmp, **kw)
            con = duckdb.connect(str(db.path), read_only=True)
            con.execute(_BASE_SQL)
            rows = con.execute("SELECT ticker, date FROM ev").fetchall()
            con.close()
            return [r[0] for r in rows]

    def test_new_high_is_detected(self):
        self.assertEqual(self._signals(), [TICKER])

    def test_no_signal_without_break(self):
        self.assertEqual(self._signals(break_high=False), [])

    def test_short_history_is_dropped(self):
        # 룩백 구간에 실거래일이 MIN_BARS 미만
        self.assertEqual(self._signals(warmup=150), [])

    def test_halt_stretched_lookback_is_dropped(self):
        # 거래정지로 룩백 250거래일이 달력상 훨씬 긴 구간이 되면 제외
        self.assertEqual(self._signals(warmup=205, halt_gap=60), [])


class TestTrailingExit(unittest.TestCase):
    def _run(self, bars, entry=100.0, stop=0.20):
        o, h, l, c = (np.array([b[i] for b in bars], float) for i in range(4))
        return trailing_exit(o, h, l, c, entry, stop)

    def test_triggers_at_stop_price(self):
        ret, days = self._run([(100, 105, 99, 104), (104, 104, 78, 80)])
        self.assertAlmostEqual(ret, 84 / 100 - 1, places=6)   # 고점 105 x 0.8 = 84
        self.assertEqual(days, 2)

    def test_peak_updates_raise_trigger(self):
        ret, days = self._run([(100, 200, 99, 199), (199, 199, 150, 155)])
        self.assertAlmostEqual(ret, 160 / 100 - 1, places=6)  # 고점 200 x 0.8 = 160
        self.assertEqual(days, 2)

    def test_gap_down_fills_at_open(self):
        ret, _ = self._run([(60, 65, 55, 58)])                # 시가 60 < 트리거 80
        self.assertAlmostEqual(ret, 60 / 100 - 1, places=6)

    def test_no_trigger_closes_at_last_bar(self):
        ret, days = self._run([(100, 110, 95, 108), (108, 115, 100, 112)])
        self.assertAlmostEqual(ret, 112 / 100 - 1, places=6)
        self.assertEqual(days, 2)

    def test_low_checked_before_high_same_bar(self):
        # 같은 봉에서 저가가 트리거를 깨고 고가가 신고점이면 청산이 우선(보수적)
        ret, days = self._run([(100, 500, 70, 400)])
        self.assertAlmostEqual(ret, 80 / 100 - 1, places=6)
        self.assertEqual(days, 1)


class TestBands(unittest.TestCase):
    def test_base_band_boundaries(self):
        self.assertEqual(base_band(1), BAND_LABELS[0])
        self.assertEqual(base_band(3), BAND_LABELS[0])
        self.assertEqual(base_band(4), BAND_LABELS[1])
        self.assertEqual(base_band(20), BAND_LABELS[1])
        self.assertEqual(base_band(21), BAND_LABELS[2])

    def test_base_band_missing_gap_is_first_high(self):
        self.assertEqual(base_band(None), BAND_LABELS[3])
        self.assertEqual(base_band(pd.NA), BAND_LABELS[3])
        self.assertEqual(base_band(float("nan")), BAND_LABELS[3])

    def test_cap_bucket(self):
        self.assertEqual(cap_bucket(500), "<1천억")
        self.assertEqual(cap_bucket(1000), "1~3천억")
        self.assertEqual(cap_bucket(60000), "5조+")


def _frame(**over):
    base = {"ticker": "000010", "name": "가나", "date": "20260101", "ms": 1,
            "band": BAND_LABELS[0], "tval": 100.0, "depth": 2.0, "debt": 200.0,
            "days": 10, "exc": 0.05}
    base.update(over)
    return base


class TestFilters(unittest.TestCase):
    def test_spac_is_excluded(self):
        df = pd.DataFrame([_frame(), _frame(ticker="000020", name="미래에셋스팩5호")])
        self.assertEqual(list(apply_filters(df).ticker), ["000010"])

    def test_thresholds(self):
        df = pd.DataFrame([
            _frame(),
            _frame(ticker="000020", tval=5.0),      # 거래대금 미달
            _frame(ticker="000030", depth=3.0),     # 깊이 초과
            _frame(ticker="000040", debt=100.0),    # 부채비율 미달
            _frame(ticker="000050", debt=None),     # 재무 미매칭
        ])
        self.assertEqual(list(apply_filters(df).ticker), ["000010"])

    def test_dedup_keeps_first_signal_per_ticker_and_band(self):
        df = pd.DataFrame([
            _frame(ms=5, date="20260105"),
            _frame(ms=2, date="20260102"),                     # 같은 종목·밴드 → 이른 것만
            _frame(ms=3, date="20260103", band=BAND_LABELS[2]),  # 밴드 다르면 별도
        ])
        out = apply_filters(df)
        self.assertEqual(len(out), 2)
        self.assertEqual(sorted(out.ms), [2, 3])

    def test_filters_can_be_disabled(self):
        df = pd.DataFrame([_frame(depth=9.0, debt=1.0, tval=0.5)])
        self.assertEqual(len(apply_filters(df, tval_min=0, depth_max=None, debt_min=None)), 1)


class TestSummarize(unittest.TestCase):
    def test_returns_none_below_minimum(self):
        self.assertIsNone(summarize(pd.DataFrame([_frame()] * 5)))

    def test_pass_requires_both_halves_positive(self):
        rows = [_frame(ms=i, exc=0.1) for i in range(10)]
        rows += [_frame(ms=i, exc=-0.3) for i in range(10, 20)]
        s = summarize(pd.DataFrame(rows))
        self.assertFalse(s["pass"])
        self.assertGreater(s["h1"], 0)
        self.assertLess(s["h2"], 0)

    def test_win_rate_and_median(self):
        rows = [_frame(ms=i, exc=e) for i, e in enumerate([0.1] * 6 + [-0.1] * 4)]
        s = summarize(pd.DataFrame(rows))
        self.assertEqual(s["n"], 10)
        self.assertAlmostEqual(s["win"], 0.6)
        self.assertAlmostEqual(s["median"], 0.1)


if __name__ == "__main__":
    unittest.main()
