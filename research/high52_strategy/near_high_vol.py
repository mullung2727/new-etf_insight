"""52주 신고가 근처 거래량 양봉 → 다음날 시가 매수, 매수가 -20% 고정 손절 / 40일 만기.

신호  D 종가 >= 직전 250거래일 고가 x 0.95 (돌파 포함)
      D 거래량 >= 직전 20거래일 평균 x 3 (20일 연속 거래 가드)
      D 종가 > D 시가
진입  D+1 시가 (D+1 이 실제 다음 거래일일 때만, 상한가 시가는 매수 불가로 제외)
청산  매수가 x 0.8 터치(갭하락이면 시가) 또는 40거래일째 종가
중복  보유 중 재신호 무시, 청산일 이후 신호만 다시 받음
--signal gradual 은 거래량 조건만 "폭발 없이 서서히 증가"로 바꾼다 (_GRADUAL_SQL 참조)

비교  A 필터 없음 / B 기존 필터(스팩 제외, 거래대금 10억+, 베이스 깊이 < 2.28, 부채비율 150%+)
      필터를 먼저 걸고 중복 제거 — 걸러진 신호는 실매매에서 포지션을 막지 않는다.

실행 (저장소 루트):
    etl\\.venv\\Scripts\\python.exe -m research.high52_strategy.near_high_vol
    etl\\.venv\\Scripts\\python.exe -m research.high52_strategy.near_high_vol --signal gradual
"""
import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

from research.high52_strategy.backtest import (
    COST, DEBT_MIN, DEPTH_MAX, HEADER, KRX_DB, LIMIT_UP, LOOKBACK, MIN_BARS, MIN_SPAN, SPAC, TVAL_MIN,
    _ADJ, _BASE_SQL, _line, _num, cap_bucket, corp_names, load_debt_ratio,
    size_neutral_index, summarize)

NEAR = 0.95     # 종가 >= 52주 고가 x NEAR
VOL_X = 3.0     # 거래량 >= 20일 평균 x VOL_X
STOP = 0.20     # 매수가 대비 고정 손절
HOLD = 40       # 최대 보유 거래일

# _BASE_SQL 이 만든 b(시장 순번 ms 포함) 위에서 신호를 뽑는다.
_SIG_SQL = f"""
CREATE OR REPLACE TEMP TABLE sig AS
WITH w AS (
  SELECT *, MAX(high) OVER p52 prev_hi, MIN(low) OVER p52 prev_lo,
            MIN(ms) OVER p52 old_ms, COUNT(*) OVER p52 n52,
            AVG(volume) OVER p20 vma20, COUNT(*) OVER p20 n20
  FROM b
  WINDOW p52 AS (PARTITION BY ticker ORDER BY ms RANGE BETWEEN {LOOKBACK} PRECEDING AND 1 PRECEDING),
         p20 AS (PARTITION BY ticker ORDER BY ms RANGE BETWEEN 20 PRECEDING AND 1 PRECEDING)
)
SELECT ticker, date, ms, close, list_shrs, market, market_cap, trading_value,
       close / prev_hi AS near, volume / vma20 AS volx, prev_hi / nullif(prev_lo, 0) AS depth
FROM w
WHERE n52 >= {MIN_BARS} AND ms - old_ms >= {MIN_SPAN} AND n20 = 20
  AND close >= prev_hi * {NEAR} AND volume >= vma20 * {VOL_X} AND close > open;
"""

# --signal gradual: 거래량 폭발 없이 서서히 증가.
#   평균 거래량 정배열  5일 > 20일 > 60일 (D 포함, 60일 연속 거래 가드)
#   폭발 없음          최근 20일(D 포함) 매일 거래량 <= 그날 직전 20일 평균 x GRAD_MAX
GRAD_MAX = 2.0
_GRADUAL_SQL = f"""
CREATE OR REPLACE TEMP TABLE sig AS
WITH d AS (
  SELECT *, volume / nullif(AVG(volume) OVER (PARTITION BY ticker ORDER BY ms
              RANGE BETWEEN 20 PRECEDING AND 1 PRECEDING), 0) AS dvx
  FROM b
), w AS (
  SELECT *, MAX(high) OVER p52 prev_hi, MIN(low) OVER p52 prev_lo,
            MIN(ms) OVER p52 old_ms, COUNT(*) OVER p52 n52,
            AVG(volume) OVER r5 v5, COUNT(*) OVER r5 n5,
            AVG(volume) OVER r20 v20, MAX(dvx) OVER r20 max_dvx,
            AVG(volume) OVER r60 v60, COUNT(*) OVER r60 n60
  FROM d
  WINDOW p52 AS (PARTITION BY ticker ORDER BY ms RANGE BETWEEN {LOOKBACK} PRECEDING AND 1 PRECEDING),
         r5  AS (PARTITION BY ticker ORDER BY ms RANGE BETWEEN 4 PRECEDING AND CURRENT ROW),
         r20 AS (PARTITION BY ticker ORDER BY ms RANGE BETWEEN 19 PRECEDING AND CURRENT ROW),
         r60 AS (PARTITION BY ticker ORDER BY ms RANGE BETWEEN 59 PRECEDING AND CURRENT ROW)
)
SELECT ticker, date, ms, close, list_shrs, market, market_cap, trading_value,
       close / prev_hi AS near, dvx AS volx, prev_hi / nullif(prev_lo, 0) AS depth
FROM w
WHERE n52 >= {MIN_BARS} AND ms - old_ms >= {MIN_SPAN} AND n5 = 5 AND n60 = 60
  AND close >= prev_hi * {NEAR} AND close > open
  AND v5 > v20 AND v20 > v60 AND max_dvx <= {GRAD_MAX};
"""
SIGNALS = {"spike": _SIG_SQL, "gradual": _GRADUAL_SQL}


def fixed_exit(o, h, l, c, stop: float):
    """o[0] 에 매수. 매수가 x (1-stop) 에 닿으면 청산, 아니면 마지막 봉 종가.

    반환 (수익률, 보유일, 손절여부). 갭하락으로 시가가 트리거보다 낮으면 시가 체결.
    """
    entry = o[0]
    trig = entry * (1 - stop)
    for i in range(len(c)):
        if l[i] <= trig:
            return min(o[i], trig) / entry - 1, i + 1, True
    return c[-1] / entry - 1, len(c), False


def dedup_positions(df: pd.DataFrame) -> pd.DataFrame:
    """종목별로 보유 중(청산일 전) 나온 신호를 버린다. 청산일 당일 신호는 다음날 진입이라 받는다."""
    keep, last = [], {}
    for r in df.sort_values(["ticker", "ms"]).itertuples():
        if r.ms >= last.get(r.ticker, -1):
            keep.append(r.Index)
            last[r.ticker] = r.exit_ms
    return df.loc[keep]


def filtered(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df.name.fillna("").str.contains(SPAC) & (df.tval >= TVAL_MIN)
              & (df.depth < DEPTH_MAX) & (df.debt >= DEBT_MIN)]


def run(db_path: Path = KRX_DB, *, sig_sql: str = _SIG_SQL, stop: float = STOP,
        hold: int = HOLD, cost: float = COST) -> pd.DataFrame:
    """신호 전부를 시뮬레이션해 이벤트별 초과수익 DataFrame 반환 (중복 제거 전)."""
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute(_BASE_SQL)
    con.execute(sig_sql)
    idx = size_neutral_index(con)
    path = con.execute(f"""
    SELECT e.ticker, e.date, e.ms, e.market, e.market_cap, e.trading_value,
           e.near, e.volx, e.depth, e.close dc,
           f.ms mms, f.open*{_ADJ} o, f.high*{_ADJ} h, f.low*{_ADJ} l, f.close*{_ADJ} c
    FROM sig e JOIN b f ON f.ticker=e.ticker AND f.ms>e.ms AND f.ms<=e.ms+{hold}
    WHERE e.ms + {hold} <= (SELECT max(ms) FROM b)
    ORDER BY e.ticker, e.ms, f.ms
    """).df()
    con.close()

    names, rows = corp_names(), []
    for (tk, ms), g in path.groupby(["ticker", "ms"], sort=False):
        mms = g.mms.to_numpy(int)
        if mms[0] != ms + 1 or len(g) < hold * 0.8:   # D+1 정지, 또는 봉이 크게 빈 이벤트
            continue
        o, h, l, c = (g[x].to_numpy(float) for x in "ohlc")
        r0 = g.iloc[0]
        if o[0] >= r0.dc * (1 + LIMIT_UP):            # D+1 상한가 시가는 매수 불가
            continue
        ret, days, stopped = fixed_exit(o, h, l, c, stop)
        cap = _num(r0.market_cap) / 1e8
        tbl = idx.get(cap_bucket(cap), {})
        # ponytail: 지수는 종가 기준이라 D 종가부터 잰다. D+1 시가 진입과 하룻밤 갭만큼 어긋남
        x, y = tbl.get(ms), tbl.get(int(mms[days - 1]))
        if not x or not y:
            continue
        rows.append({
            "ticker": tk, "name": names.get(tk, ""), "date": r0.date, "ms": ms,
            "market": r0.market, "cap": cap, "tval": _num(r0.trading_value) / 1e8,
            "near": r0.near, "volx": r0.volx, "depth": r0.depth,
            "days": days, "exit_ms": int(mms[days - 1]), "stopped": stopped,
            "net": ret - cost, "exc": ret - cost - (y / x - 1),
        })
    df = pd.DataFrame(rows)
    df["dt"] = df.date.astype(int)
    df = pd.merge_asof(df.sort_values("dt"), load_debt_ratio(),
                       left_on="dt", right_on="avail", by="ticker",
                       direction="backward").drop(columns=["avail"])
    return df.rename(columns={"val": "debt"})


def main() -> None:
    p = argparse.ArgumentParser(description="52주 신고가 근처 거래량 양봉 백테스트")
    p.add_argument("--signal", choices=SIGNALS, default="spike",
                   help=f"spike: 거래량 {VOL_X:.0f}x 폭발 / gradual: 정배열 서서히 증가, {GRAD_MAX:.0f}x 초과일 없음")
    p.add_argument("--db", type=Path, default=KRX_DB)
    args = p.parse_args()

    df = run(args.db, sig_sql=SIGNALS[args.signal])
    print(f"신호 {len(df)}건 / {df.ticker.nunique()}종목 / {df.date.min()}~{df.date.max()}"
          f" | {args.signal} / 근처 {NEAR:.0%} / 손절 -{STOP:.0%} 고정 / 최대 {HOLD}일\n")
    print(HEADER + "\n" + "-" * 106)
    for label, q in [("A 필터 없음", df), ("B 기존 필터", filtered(df))]:
        q = dedup_positions(q)
        print(_line(label, summarize(q)))
        print(f"{'':<30}절대평균 {q.net.mean()*100:+.2f}% | 손절 {q.stopped.mean()*100:.0f}%"
              f" | 만기 {(~q.stopped).mean()*100:.0f}% | 종목 {q.ticker.nunique()}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
