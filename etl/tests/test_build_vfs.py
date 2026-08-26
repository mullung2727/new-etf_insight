"""build_vfs 단위 테스트 — VFS 신호 SQL 조건 검증.

D일 거래량 폭발 + 장대양봉, D+1 눌림(음봉/거래량 급감/몸통중간 위) 전부 만족할 때만
(D+1, ticker) 가 나오는지, 조건 하나씩 깨뜨려 제외되는지 확인한다.
"""
import unittest
from datetime import datetime, timedelta

import duckdb

from scripts.build_krx_ohlcv import _CREATE_OHLCV
from scripts.build_vfs import compute_vfs, upsert_vfs
from scripts.wl_sqlite import connect_rw

BASE = datetime(2026, 1, 5)
WARMUP = 20                  # 이동평균 윈도우 확보용 평범한 날 수


def _d(i: int) -> str:
    return (BASE + timedelta(days=i)).strftime("%Y%m%d")


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(_CREATE_OHLCV)
    return con


def _insert(con, date, ticker, open_, close, volume):
    con.execute(
        "INSERT INTO ohlcv (date, ticker, market, open, high, low, close, volume) "
        "VALUES (?, ?, 'KOSPI', ?, ?, ?, ?, ?)",
        [date, ticker, open_, max(open_, close), min(open_, close), close, volume],
    )


def _seed(con, ticker="005930", *, spike_volume=1000, d_close=120,
          nx_open=118, nx_close=115, nx_volume=150, warmup=WARMUP):
    """평범한 warmup 일 + D(폭등 양봉) + D+1(눌림) 을 넣는다."""
    for i in range(warmup):
        _insert(con, _d(i), ticker, 100, 100, 100)
    _insert(con, _d(warmup), ticker, 100, d_close, spike_volume)          # D
    _insert(con, _d(warmup + 1), ticker, nx_open, nx_close, nx_volume)    # D+1
    return _d(0), _d(warmup + 1)


class TestComputeVfs(unittest.TestCase):
    def test_all_conditions_met(self):
        con = _con()
        frm, to = _seed(con)
        # MA20 = (100*19 + 1000)/20 = 145 → 1000 > 725 ✓ / 120 > 110 ✓
        # D+1: 115 > (120+100)/2=110 ✓, 115 < 118*0.99=116.82 ✓, 150 < 200 ✓
        self.assertEqual(compute_vfs(con, frm, to), {_d(WARMUP + 1): ["005930"]})

    def test_volume_spike_too_small(self):
        con = _con()
        frm, to = _seed(con, spike_volume=400)   # MA=115, 400 < 575
        self.assertEqual(compute_vfs(con, frm, to), {})

    def test_not_long_white_candle(self):
        con = _con()
        frm, to = _seed(con, d_close=105, nx_open=104, nx_close=101, nx_volume=150)
        self.assertEqual(compute_vfs(con, frm, to), {})   # 105 < 100*1.1

    def test_next_day_is_white_candle(self):
        con = _con()
        frm, to = _seed(con, nx_open=112, nx_close=118)   # 118 > 112 → 양봉
        self.assertEqual(compute_vfs(con, frm, to), {})

    def test_next_day_below_body_mid(self):
        con = _con()
        frm, to = _seed(con, nx_open=112, nx_close=105)   # 105 < mid 110
        self.assertEqual(compute_vfs(con, frm, to), {})

    def test_next_day_volume_not_dry(self):
        con = _con()
        frm, to = _seed(con, nx_volume=500)               # 500 >= 1000*0.2
        self.assertEqual(compute_vfs(con, frm, to), {})

    def test_warmup_shorter_than_ma_window_excluded(self):
        con = _con()
        frm, to = _seed(con, warmup=WARMUP - 3)           # 윈도우 20행 미달
        self.assertEqual(compute_vfs(con, frm, to), {})

    def test_no_next_day_row(self):
        con = _con()
        ticker = "005930"
        for i in range(WARMUP):
            _insert(con, _d(i), ticker, 100, 100, 100)
        _insert(con, _d(WARMUP), ticker, 100, 120, 1000)  # D 만 있고 D+1 없음
        self.assertEqual(compute_vfs(con, _d(0), _d(WARMUP)), {})


class TestHaltGuard(unittest.TestCase):
    """거래정지 가드 — 종목 행이 빠진 구간을 윈도우가 건너뛴 신호는 제외한다.

    다른 종목(FILLER)이 그 날짜에 거래되므로 시장 거래일 달력에는 그 날이 존재한다.
    """

    FILLER = "000001"

    def _with_filler(self, con, last_index):
        for i in range(last_index + 1):
            _insert(con, _d(i), self.FILLER, 100, 100, 100)

    def test_next_day_not_market_next_trading_day_is_excluded(self):
        con = _con()
        self._with_filler(con, WARMUP + 2)
        for i in range(WARMUP):
            _insert(con, _d(i), "005930", 100, 100, 100)
        _insert(con, _d(WARMUP), "005930", 100, 120, 1000)          # D
        # _d(WARMUP + 1) 은 거래정지 — 눌림 조건은 하루 건너뛴 _d(WARMUP + 2) 에 성립
        _insert(con, _d(WARMUP + 2), "005930", 118, 115, 150)
        self.assertEqual(compute_vfs(con, _d(0), _d(WARMUP + 2)), {})

    def test_gap_inside_moving_average_window_is_excluded(self):
        con = _con()
        self._with_filler(con, WARMUP + 1)
        for i in range(WARMUP):
            if i == 5:
                continue                                             # 이동평균 구간 안의 거래정지
            _insert(con, _d(i), "005930", 100, 100, 100)
        _insert(con, _d(WARMUP), "005930", 100, 120, 1000)          # D
        _insert(con, _d(WARMUP + 1), "005930", 118, 115, 150)       # D+1 눌림 성립
        self.assertEqual(compute_vfs(con, _d(0), _d(WARMUP + 1)), {})

    def test_contiguous_history_still_passes(self):
        con = _con()
        self._with_filler(con, WARMUP + 1)
        frm, to = _seed(con)
        self.assertEqual(compute_vfs(con, frm, to), {_d(WARMUP + 1): ["005930"]})


class TestUpsertVfs(unittest.TestCase):
    def test_upsert_is_idempotent(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "watchlist.sqlite3"
            signals = {"20260205": ["005930", "000660"]}
            with connect_rw(db) as con:
                self.assertEqual(upsert_vfs(con, signals), 2)
            with connect_rw(db) as con:
                upsert_vfs(con, signals)
                rows = con.execute("SELECT date, stock_code FROM vfs ORDER BY stock_code").fetchall()
            self.assertEqual(rows, [("20260205", "000660"), ("20260205", "005930")])


if __name__ == "__main__":
    unittest.main()
