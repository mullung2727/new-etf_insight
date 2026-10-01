"""분봉 전용 벤치 래퍼 — 여러 날 보유는 일봉과 동일(M7)."""
from __future__ import annotations

from research.backtest_daily.bench_daily import bench_return


def hold_bench(bench_id, entry_date, entry_time, exit_date, exit_at, table=None):
    """시가 체결(90000)이면 open, 장중이면 close 부터 벤치 수익."""
    try:
        is_open = int(entry_time) == 90000
    except (TypeError, ValueError):
        is_open = False
    entry_at = "open" if is_open else "close"
    return bench_return(bench_id, entry_date, entry_at, exit_date, exit_at,
                        table=table)
