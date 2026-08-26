"""52주 신고가 전략 — 신호 생성 · 필터 · 트레일링 청산 · 사이즈 중립 평가.

전략(측정으로 확정된 정의):
    신호  당일 고가 > 직전 250거래일 고가
          가드: 그 250일 중 실제 거래 200일 이상, 구간이 시장 달력상 240일 이상
    필터  스팩 제외 / 거래대금 >= 10억 / 베이스 깊이 < 2.28 / 부채비율 >= 150%
    진입  신호일 종가
    청산  보유 중 최고가 대비 -20% 또는 120거래일 만기 (실측 99%가 트레일링)

평가 규약(전부 필수. 하나라도 빠지면 유령 팩터가 나온다 — BACKTEST_DATA.md 참조):
    · 분할/병합 보정 : list_shrs 비율이 [0.67,1.5] 밖이면 가격에 반영
    · 사이즈 중립     : 같은 시총 구간 동일가중 지수 대비 초과수익
    · point-in-time  : 재무는 stlm_dt + 45일(분기·반기) / +90일(사업보고서) 이후만 사용

Usage (저장소 루트에서):
    etl\\.venv\\Scripts\\python.exe -m research.high52_strategy.backtest
    etl\\.venv\\Scripts\\python.exe -m research.high52_strategy.backtest --stop 0.15 --hold 60
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
KRX_DB = ROOT / "etl" / "db" / "krx_ohlcv.duckdb"
FIN_DB = ROOT / "etl" / "db" / "financial_indicators.sqlite3"

LOOKBACK = 250          # 52주 (거래일)
MIN_BARS = 200          # 룩백 구간 내 최소 실거래일
MIN_SPAN = 240          # 룩백 구간의 시장 달력상 최소 길이
COST = 0.006            # 왕복 거래비용
STOP = 0.20             # 고점 대비 트레일링 스탑
HOLD = 120              # 최대 보유 거래일
TVAL_MIN = 10.0         # 최소 거래대금(억)
DEPTH_MAX = 2.28        # 베이스 깊이 상한 (52주 고가/저가). 저·중변동 3분위 경계
DEBT_MIN = 150.0        # 최소 부채비율(%)
SPLIT_LO, SPLIT_HI = 0.67, 1.5                   # 이 밖이면 분할/병합으로 보고 보정
CAP_EDGES = [0, 1000, 3000, 10000, 50000, 1e12]  # 억
CAP_LABELS = ["<1천억", "1~3천억", "3천~1조", "1~5조", "5조+"]
SPAC = re.compile(r"스팩|기업인수목적")
DEBT_IDX = "M221100"    # DART 부채비율

# 시장 전체 거래일에 순번(ms)을 붙여 종목별 연속성을 검사한다.
_BASE_SQL = f"""
CREATE OR REPLACE TEMP TABLE b AS
WITH mkt AS (SELECT DISTINCT date FROM ohlcv),
     m AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) ms FROM mkt)
SELECT o.ticker,o.date,o.open,o.high,o.low,o.close,o.volume,o.trading_value,
       o.market_cap,o.list_shrs,o.market,m.ms
FROM ohlcv o JOIN m USING(date)
WHERE o.volume > 0 AND o.open > 0 AND o.close > 0;

CREATE OR REPLACE TEMP TABLE ev AS
WITH w AS (
  SELECT *, MAX(high) OVER p52 prev_hi, MIN(low) OVER p52 prev_lo,
            MIN(ms) OVER p52 old_ms, COUNT(*) OVER p52 n52,
            AVG(volume) OVER p20 vma20, LAG(close) OVER pt pc
  FROM b
  WINDOW p52 AS (PARTITION BY ticker ORDER BY ms RANGE BETWEEN {LOOKBACK} PRECEDING AND 1 PRECEDING),
         p20 AS (PARTITION BY ticker ORDER BY ms RANGE BETWEEN 20 PRECEDING AND 1 PRECEDING),
         pt  AS (PARTITION BY ticker ORDER BY ms)
)
SELECT ticker, date, ms, close, list_shrs, market, market_cap, trading_value,
       close > prev_hi AS conf,
       ms - LAG(ms) OVER (PARTITION BY ticker ORDER BY ms) AS gap,
       volume / nullif(vma20, 0) AS volx,
       close / nullif(pc, 0) - 1 AS chg,
       prev_hi / nullif(prev_lo, 0) AS depth
FROM w
WHERE n52 >= {MIN_BARS} AND ms - old_ms >= {MIN_SPAN} AND high > prev_hi;
"""

# 분할/병합 보정 계수. e=진입행, f=이후행.
_ADJ = (f"CASE WHEN e.list_shrs>0 AND f.list_shrs>0 "
        f"AND (f.list_shrs*1.0/e.list_shrs NOT BETWEEN {SPLIT_LO} AND {SPLIT_HI}) "
        f"THEN f.list_shrs*1.0/e.list_shrs ELSE 1 END")

BAND_LABELS = ["연속(1~3일)", "단기재돌파(4~20일)", "베이스돌파(21일+)", "첫신고가"]


def base_band(gap) -> str:
    """직전 신고가 이후 경과 거래일 → 베이스 길이 밴드."""
    if gap is None or pd.isna(gap):      # pandas nullable Int → pd.NA, np.isnan 안 먹음
        return BAND_LABELS[3]
    return BAND_LABELS[0] if gap <= 3 else BAND_LABELS[1] if gap <= 20 else BAND_LABELS[2]


def cap_bucket(cap_eok: float) -> str:
    return str(pd.cut([cap_eok], bins=CAP_EDGES, labels=CAP_LABELS, right=False)[0])


def size_neutral_index(con) -> dict:
    """시총 구간별 일별 동일가중 지수.

    전날 시총으로 버킷을 배정해 룩어헤드를 막고, 일별수익률은 가격제한폭(±30%)으로
    클립한다. 그 밖으로 튀는 값은 남아 있는 기업행위 잔재라 지수를 오염시킨다.
    """
    d = con.execute(f"""
    WITH s AS (SELECT ticker, ms, close, list_shrs, market_cap,
                 LAG(close) OVER w pc, LAG(list_shrs) OVER w ps,
                 LAG(ms) OVER w pms, LAG(market_cap) OVER w pcap
               FROM b WINDOW w AS (PARTITION BY ticker ORDER BY ms))
    SELECT ms, pcap/1e8 cap,
           greatest(-0.3, least(0.3, close * CASE WHEN ps>0 AND list_shrs>0
             AND (list_shrs*1.0/ps NOT BETWEEN {SPLIT_LO} AND {SPLIT_HI})
             THEN list_shrs*1.0/ps ELSE 1 END / pc - 1)) r
    FROM s WHERE pc IS NOT NULL AND ms = pms + 1 AND pcap > 0
    """).df()
    d["bkt"] = pd.cut(d.cap, bins=CAP_EDGES, labels=CAP_LABELS, right=False)
    out = {}
    for k, g in d.groupby("bkt", observed=True):
        s = g.groupby("ms").r.mean().sort_index()
        out[str(k)] = dict(zip(s.index.astype(int), (1 + s).cumprod()))
    return out


def trailing_exit(o, h, l, c, entry: float, stop: float):
    """고점 대비 -stop 에 닿으면 청산, 아니면 마지막 봉 종가. 반환 (수익률, 보유일).

    하루 안에서는 저가를 먼저 본다(보수적). 갭하락으로 시가가 트리거보다 낮으면 시가 체결.
    """
    peak, trig = entry, entry * (1 - stop)
    for i in range(len(c)):
        if l[i] <= trig:
            return min(o[i], trig) / entry - 1, i + 1
        if h[i] > peak:
            peak, trig = h[i], h[i] * (1 - stop)
    return c[-1] / entry - 1, len(c)


def load_debt_ratio() -> pd.DataFrame:
    """부채비율 point-in-time 테이블. avail = 공시 가능일 (결산일 + 45/90일)."""
    with sqlite3.connect(f"file:{FIN_DB}?mode=ro", uri=True) as fi:
        f = pd.read_sql(
            "SELECT stock_code ticker, reprt_code, stlm_dt, CAST(idx_val AS REAL) val "
            "FROM indicators WHERE idx_code=? AND idx_val IS NOT NULL AND idx_val<>''",
            fi, params=[DEBT_IDX])
    lag = np.where(f.reprt_code == "11011", 90, 45)   # 사업보고서 90일, 분기·반기 45일
    f["avail"] = (pd.to_datetime(f.stlm_dt)
                  + pd.to_timedelta(lag, "D")).dt.strftime("%Y%m%d").astype(int)
    return f[["ticker", "avail", "val"]].sort_values("avail")


def corp_names() -> dict:
    with sqlite3.connect(f"file:{FIN_DB}?mode=ro", uri=True) as fi:
        return {s: n for n, s in fi.execute(
            "SELECT corp_name, stock_code FROM corps WHERE stock_code<>''")}


def run(db_path: Path = KRX_DB, *, stop: float = STOP, hold: int = HOLD,
        cost: float = COST, from_date: str = None) -> pd.DataFrame:
    """신고가 신호를 전부 시뮬레이션해 이벤트별 초과수익을 낸 DataFrame 반환."""
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute(_BASE_SQL)
    idx = size_neutral_index(con)
    where = f"AND e.date >= '{from_date}'" if from_date else ""
    path = con.execute(f"""
    SELECT e.ticker,e.date,e.ms,e.gap,e.conf,e.close entry,e.market_cap,e.trading_value,
           e.depth,e.volx,e.chg,e.market,
           f.ms mms, f.open*{_ADJ} o, f.high*{_ADJ} h, f.low*{_ADJ} l, f.close*{_ADJ} c
    FROM ev e JOIN b f ON f.ticker=e.ticker AND f.ms>e.ms AND f.ms<=e.ms+{hold}
    WHERE e.ms + {hold} <= (SELECT max(ms) FROM b) {where}
    ORDER BY e.ticker, e.ms, f.ms
    """).df()
    con.close()

    names, rows = corp_names(), []
    for (tk, ms), g in path.groupby(["ticker", "ms"], sort=False):
        if len(g) < hold * 0.8:      # 정지로 봉이 크게 빈 이벤트는 표본에서 제외
            continue
        o, h, l, c = (g[x].to_numpy(float) for x in "ohlc")
        r0 = g.iloc[0]
        cap = (r0.market_cap or 0) / 1e8
        tbl = idx.get(cap_bucket(cap), {})
        ret, days = trailing_exit(o, h, l, c, float(r0.entry), stop)
        x, y = tbl.get(ms), tbl.get(int(g.mms.to_numpy(int)[days - 1]))
        if not x or not y:
            continue
        rows.append({
            "ticker": tk, "name": names.get(tk, ""), "date": r0.date, "ms": ms,
            "band": base_band(r0.gap), "conf": bool(r0.conf), "market": r0.market,
            "cap": cap, "tval": (r0.trading_value or 0) / 1e8,
            "depth": r0.depth, "volx": r0.volx, "chg": r0.chg,
            "days": days, "net": ret - cost, "exc": ret - cost - (y / x - 1),
        })
    df = pd.DataFrame(rows)
    df["dt"] = df.date.astype(int)
    df = pd.merge_asof(df.sort_values("dt"), load_debt_ratio(),
                       left_on="dt", right_on="avail", by="ticker",
                       direction="backward").drop(columns=["avail"])
    return df.rename(columns={"val": "debt"})


def apply_filters(df: pd.DataFrame, *, tval_min: float = TVAL_MIN,
                  depth_max: float = DEPTH_MAX,
                  debt_min: float = DEBT_MIN) -> pd.DataFrame:
    """매매 가능성 + 확정된 선별 조건. 종목당 첫 신호만 남긴다(표본 독립성)."""
    q = df[~df.name.fillna("").str.contains(SPAC) & (df.tval >= tval_min)]
    if depth_max is not None:
        q = q[q.depth < depth_max]
    if debt_min is not None:
        q = q[q.debt.notna() & (q.debt >= debt_min)]
    return q.sort_values("ms").drop_duplicates(["ticker", "band"])


def summarize(df: pd.DataFrame, col: str = "exc"):
    """n / 평균 / 중앙 / 승률 / t / 전·후반. 전후반 둘 다 양수여야 '통과'."""
    if len(df) < 10:
        return None
    v = df[col].to_numpy()
    n, mean = len(v), float(v.mean())
    sd = float(v.std()) or 1e-9
    mid = df.ms.median()
    h1, h2 = df[df.ms < mid][col].mean(), df[df.ms >= mid][col].mean()
    return {"n": n, "mean": mean, "median": float(np.median(v)),
            "win": float((v > 0).mean()), "t": mean / (sd / n ** 0.5),
            "h1": float(h1), "h2": float(h2), "days": float(df.days.mean()),
            "pass": bool(h1 > 0 and h2 > 0)}


def _line(label: str, s, width: int = 30) -> str:
    if not s:
        return f"{label:<{width}}  표본부족"
    return (f"{label:<{width}}{s['n']:>6}{s['mean']*100:>9.2f}%{s['median']*100:>9.2f}%"
            f"{s['win']*100:>7.1f}%{s['t']:>7.2f}{s['h1']*100:>9.2f}%{s['h2']*100:>9.2f}%"
            f"{s['days']:>7.0f}   {'O' if s['pass'] else '-'}")


HEADER = (f"{'구분':<30}{'n':>6}{'초과평균':>10}{'초과중앙':>10}{'승률':>8}{'t':>7}"
          f"{'전반':>10}{'후반':>10}{'보유일':>7}  통과")

STEPS = [
    ("① 신고가 전체", dict(tval_min=0, depth_max=None, debt_min=None)),
    ("② + 스팩제외·거래대금10억", dict(depth_max=None, debt_min=None)),
    ("③ + 베이스깊이 < 2.28", dict(debt_min=None)),
    ("④ + 부채비율 >= 150% (최종)", dict()),
]


def main() -> None:
    p = argparse.ArgumentParser(description="52주 신고가 전략 백테스트")
    p.add_argument("--stop", type=float, default=STOP, help="트레일링 스탑 폭 (기본 0.20)")
    p.add_argument("--hold", type=int, default=HOLD, help="최대 보유 거래일 (기본 120)")
    p.add_argument("--from-date", help="신호 시작일 YYYYMMDD")
    p.add_argument("--db", type=Path, default=KRX_DB)
    p.add_argument("--json", type=Path, help="최종 표본 저장 경로")
    args = p.parse_args()

    df = run(args.db, stop=args.stop, hold=args.hold, from_date=args.from_date)
    print(f"신호 {len(df)}건 / {df.ticker.nunique()}종목 / {df.date.min()}~{df.date.max()}"
          f" | 스탑 -{args.stop*100:.0f}% / 최대 {args.hold}일\n")
    print(HEADER + "\n" + "-" * 106)
    for label, kw in STEPS:
        print(_line(label, summarize(apply_filters(df, **kw))))
    final = apply_filters(df)
    print(f"\n최종 표본: 종목 {final.ticker.nunique()}개 | 거래대금 중앙 {final.tval.median():.0f}억"
          f" | 시총 중앙 {final.cap.median():.0f}억 | KOSPI {(final.market=='KOSPI').mean()*100:.0f}%")
    if args.json:
        args.json.write_text(final.to_json(orient="records", force_ascii=False), encoding="utf-8")
        print(f"저장: {args.json}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
