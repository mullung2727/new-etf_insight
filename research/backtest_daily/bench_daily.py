"""일봉 전용 벤치마크 일별 수익률 사전 계산 — 12종 동일가중, 세 조각(r_cc/r_on/r_in)."""
from __future__ import annotations

import datetime
import os
import uuid
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from . import adjust, bench, data, universe

BENCH_DB = Path(__file__).resolve().parents[2] / "etl/db/bench_daily.duckdb"
CODE_VERSION = "2"

BUCKETS = ["CAP1", "CAP2", "CAP3", "CAP4", "CAP5"]
UNIVERSES = ["ALL", "LIQ10"]
LIQ_MIN = 1e9
BENCH_IDS = [f"{b}_{u}" for b in BUCKETS + ["MKT"] for u in UNIVERSES]

_CAP_KO = {"CAP1": "1천억 미만", "CAP2": "1천억~3천억", "CAP3": "3천억~1조",
           "CAP4": "1조~5조", "CAP5": "5조 이상", "MKT": "시장 전체"}
_CAP_RULE = {"CAP1": "전일 시총 1천억 미만", "CAP2": "전일 시총 1천억~3천억",
             "CAP3": "전일 시총 3천억~1조", "CAP4": "전일 시총 1조~5조",
             "CAP5": "전일 시총 5조 이상", "MKT": "전일 시총 전체"}


def bench_id(cap_eok_prev, universe):
    """전일 시총(억) + 모집단 → CAPn_ALL/LIQ10. MKT는 직접 지정."""
    if universe not in UNIVERSES:
        raise ValueError(f"universe는 {UNIVERSES} 중 하나: {universe!r}")
    try:
        c = float(cap_eok_prev)
    except (TypeError, ValueError):
        raise ValueError(f"cap이 숫자가 아님: {cap_eok_prev!r}")
    if not np.isfinite(c) or c <= 0:
        raise ValueError(f"cap은 양수 유한값: {cap_eok_prev!r}")
    edges = bench.CAP_EDGES
    if c < edges[1]:
        b = "CAP1"
    elif c < edges[2]:
        b = "CAP2"
    elif c < edges[3]:
        b = "CAP3"
    elif c < edges[4]:
        b = "CAP4"
    else:
        b = "CAP5"
    return f"{b}_{universe}"


def build(db=data.DB, out=BENCH_DB) -> dict:
    """일봉 전체로 12종 세 조각 평균을 구해 out에 저장. 임시 파일 후 교체."""
    px = data.load_px(db)
    r = np.asarray(adjust.adj_returns(px), dtype=float)
    names = data.stock_names(db)
    n = len(px)
    tic = px["ticker"].to_numpy() if n else np.empty(0, object)
    ms_arr = np.asarray(px["ms"]) if n else np.empty(0, int)
    close_arr = px["close"].to_numpy(dtype=float) if n else np.empty(0, float)
    open_arr = px["open"].to_numpy(dtype=float) if n else np.empty(0, float)
    cap_arr = px["market_cap"].to_numpy(dtype=float) if n else np.empty(0, float)
    tv_arr = px["trading_value"].to_numpy(dtype=float) if n else np.empty(0, float)
    date_arr = px["date"].to_numpy() if n else np.empty(0, object)

    spac = set()
    if n:
        for t in np.unique(tic):
            if universe.SPAC_RE.search(names.get(str(t), "") or ""):
                spac.add(t)
    same = np.zeros(n, dtype=bool)
    if n:
        same[1:] = tic[1:] == tic[:-1]
    contig = np.zeros(n, dtype=bool)
    if n:
        contig[1:] = same[1:] & (ms_arr[1:] == ms_arr[:-1] + 1)
    prev_close = np.full(n, np.nan)
    prev_cap = np.full(n, np.nan)
    prev_tv = np.full(n, np.nan)
    if n > 1:
        prev_close[1:] = np.where(same[1:], close_arr[:-1], np.nan)
        prev_cap[1:] = np.where(same[1:], cap_arr[:-1], np.nan)
        prev_tv[1:] = np.where(same[1:], tv_arr[:-1], np.nan)
    # 클립된 행 판정: 보정이 적용된 행은 |r|<0.05 라 정확히 ±0.30 인 행은
    # 보정 없이 클립된 것. 그 중 원 가격 움직임이 ±31% 이상만 불가능이라 제외.
    with np.errstate(divide="ignore", invalid="ignore"):
        raw_pr = close_arr / prev_close
    hit_clip = (r == 0.30) | (r == -0.30)
    impossible = hit_clip & (np.abs(raw_pr - 1.0) >= 0.31)
    valid = (contig & np.isfinite(r) & np.isfinite(prev_cap) & (prev_cap > 0)
             & np.isfinite(prev_close) & (prev_close > 0)
             & np.isfinite(close_arr) & (close_arr > 0)
             & np.isfinite(open_arr) & (open_arr > 0)
             & ~impossible)
    if n and spac:
        is_spac = np.array([t in spac for t in tic])
        valid &= ~is_spac
    # 스팩 가격 행동: 직전 60행(당일 포함) 종가가 전부 1900~2600원이고
    # 최대/최소-1 < 5%면 스팩 구간으로 제외 (합병 후 개명한 종목의 과거 구간용).
    # 60행 미만 초기 행은 이름 기준만 적용.
    spac_px = np.zeros(n, dtype=bool)
    if n:
        grp = pd.Series(close_arr).groupby(pd.Series(tic), sort=False)
        roll = grp.rolling(60, min_periods=60)
        mx = (roll.max().reset_index(level=0, drop=True)
              .sort_index().to_numpy(dtype=float))
        mn = (roll.min().reset_index(level=0, drop=True)
              .sort_index().to_numpy(dtype=float))
        with np.errstate(divide="ignore", invalid="ignore"):
            spac_px = ((mn >= 1900.0) & (mx <= 2600.0)
                       & (mx / mn - 1.0 < 0.05))
    valid &= ~spac_px
    with np.errstate(divide="ignore", invalid="ignore"):
        pr = close_arr / prev_close
        f = (1.0 + r) / pr
        r_on = open_arr / prev_close * f - 1.0
        r_in = close_arr / open_arr - 1.0
    # r_on: 시가는 가격제한폭 안이라 ±30%(부동소수 여유 1e-9) 밖만 제외.
    # r_in: 하한가 시가→상한가 종가(+85.7%)도 정상이라 가격제한 조합 범위 밖만 제외.
    r_in_lo, r_in_hi = 0.7 / 1.3 - 1.0, 1.3 / 0.7 - 1.0
    valid &= (np.isfinite(r_on) & (np.abs(r_on) <= 0.30 + 1e-9)
              & np.isfinite(r_in) & (r_in >= r_in_lo) & (r_in <= r_in_hi))
    if valid.any():
        cap_eok = prev_cap[valid] / 1e8
        bins = list(bench.CAP_EDGES[1:-1])
        bkt = np.digitize(cap_eok, bins).astype(int)
        df = pd.DataFrame({
            "date": np.asarray(date_arr[valid]).astype(str),
            "ms": np.asarray(ms_arr[valid]).astype(int),
            "bkt": bkt,
            "liq": np.isfinite(prev_tv[valid]) & (prev_tv[valid] >= LIQ_MIN),
            "r_cc": np.asarray(r[valid], dtype=float),
            "r_on": np.asarray(r_on[valid], dtype=float),
            "r_in": np.asarray(r_in[valid], dtype=float),
        })
    else:
        df = pd.DataFrame(columns=["date", "ms", "bkt", "liq", "r_cc", "r_on", "r_in"])

    parts = []
    if len(df):
        agg = {"n": ("r_cc", "size"), "r_cc": ("r_cc", "mean"),
               "r_on": ("r_on", "mean"), "r_in": ("r_in", "mean"), "ms": ("ms", "first")}
        for b in range(5):
            sub = df[df["bkt"] == b]
            if len(sub) == 0:
                continue
            g = sub.groupby("date", sort=True).agg(**agg).reset_index()
            g["bench_id"] = f"{BUCKETS[b]}_ALL"
            parts.append(g)
            lq = sub[sub["liq"]]
            if len(lq):
                gl = lq.groupby("date", sort=True).agg(**agg).reset_index()
                gl["bench_id"] = f"{BUCKETS[b]}_LIQ10"
                parts.append(gl)
        gm = df.groupby("date", sort=True).agg(**agg).reset_index()
        gm["bench_id"] = "MKT_ALL"
        parts.append(gm)
        dfl = df[df["liq"]]
        if len(dfl):
            gl = dfl.groupby("date", sort=True).agg(**agg).reset_index()
            gl["bench_id"] = "MKT_LIQ10"
            parts.append(gl)
    if parts:
        bdf = pd.concat(parts, ignore_index=True)
        bdf = bdf[["date", "ms", "bench_id", "n", "r_cc", "r_on", "r_in"]]
        bdf["ms"] = bdf["ms"].astype(int)
        bdf["n"] = bdf["n"].astype(int)
    else:
        bdf = pd.DataFrame(columns=["date", "ms", "bench_id", "n", "r_cc", "r_on", "r_in"])

    meta_rows = []
    for bid in BENCH_IDS:
        cap, uni = bid.rsplit("_", 1)
        if uni == "ALL":
            label = f"{_CAP_KO[cap]} · 전체(스팩 제외)"
            rule = f"{_CAP_RULE[cap]}, 스팩 제외, 동일가중"
        else:
            label = f"{_CAP_KO[cap]} · 유동성(전일 거래대금 10억+)"
            rule = f"{_CAP_RULE[cap]} + 전일 거래대금 10억 이상, 스팩 제외, 동일가중"
        meta_rows.append((bid, uni, cap, label, rule))
    mdf = pd.DataFrame(meta_rows, columns=["bench_id", "universe", "cap_bucket",
                                           "label_ko", "rule_ko"])
    try:
        md = data.market_dates(db)
        src_max = str(md[-1]) if md else ""
    except Exception:
        src_max = str(np.asarray(date_arr).astype(str).max()) if n else ""
    built_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bld = pd.DataFrame([{"source_max_date": src_max, "built_at": built_at,
                         "row_count": int(len(bdf)), "code_version": CODE_VERSION}])

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # 동시 빌드 충돌 방지: 호출마다 고유한 임시 파일, 실패 시 자기 것만 지움
    tmp = out.with_name(f"{out.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        con = duckdb.connect(str(tmp))
        try:
            con.execute("CREATE TABLE bench_daily(date VARCHAR, ms INTEGER, bench_id VARCHAR,"
                        " n INTEGER, r_cc DOUBLE, r_on DOUBLE, r_in DOUBLE)")
            con.execute("CREATE TABLE bench_meta(bench_id VARCHAR, universe VARCHAR,"
                        " cap_bucket VARCHAR, label_ko VARCHAR, rule_ko VARCHAR)")
            con.execute("CREATE TABLE bench_build(source_max_date VARCHAR, built_at VARCHAR,"
                        " row_count INTEGER, code_version VARCHAR)")
            if len(bdf):
                con.register("bdf", bdf)
                con.execute("INSERT INTO bench_daily SELECT date, ms, bench_id, n, r_cc, r_on,"
                            " r_in FROM bdf")
                con.unregister("bdf")
            con.register("mdf", mdf)
            con.execute("INSERT INTO bench_meta SELECT bench_id, universe, cap_bucket, label_ko,"
                        " rule_ko FROM mdf")
            con.unregister("mdf")
            con.register("bld", bld)
            con.execute("INSERT INTO bench_build SELECT source_max_date, built_at, row_count,"
                        " code_version FROM bld")
            con.unregister("bld")
        finally:
            con.close()
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return {"source_max_date": src_max, "built_at": built_at,
            "row_count": int(len(bdf)), "code_version": CODE_VERSION, "out": str(out)}


def ensure(force=False, db=data.DB, out=BENCH_DB):
    """bench 파일이 없거나 스테일이면 build. 반환 'built' 또는 'fresh'."""
    outp = Path(out)
    if not force and outp.exists():
        try:
            con = duckdb.connect(str(outp), read_only=True)
            try:
                row = con.execute(
                    "SELECT source_max_date, code_version FROM bench_build LIMIT 1").fetchone()
            finally:
                con.close()
            if row is not None and row[1] == CODE_VERSION:
                md = data.market_dates(db)
                latest = str(md[-1]) if md else ""
                if row[0] is not None and str(row[0]) >= latest:
                    return "fresh"
        except Exception:
            pass
    build(db=db, out=outp)
    return "built"


def load(ids=None, out=BENCH_DB) -> pd.DataFrame:
    """bench_daily 읽기 전용 로드. ids None이면 전체."""
    outp = Path(out)
    if not outp.exists():
        raise FileNotFoundError(f"bench 파일 없음: {outp} (ensure()로 생성)")
    con = duckdb.connect(str(outp), read_only=True)
    try:
        if ids is None:
            return con.execute("SELECT date, ms, bench_id, n, r_cc, r_on, r_in"
                               " FROM bench_daily ORDER BY bench_id, date").df()
        if isinstance(ids, str):
            ids = [ids]
        idf = pd.DataFrame({"bid": [str(i) for i in ids]})
        con.register("ids_df", idf)
        try:
            return con.execute("SELECT b.date, b.ms, b.bench_id, b.n, b.r_cc, b.r_on, b.r_in"
                               " FROM bench_daily b JOIN ids_df ON b.bench_id = ids_df.bid"
                               " ORDER BY b.bench_id, b.date").df()
        finally:
            con.unregister("ids_df")
    finally:
        con.close()


def _frame_for(bid, table):
    if isinstance(table, pd.DataFrame):
        if "bench_id" in table.columns:
            df = table[table["bench_id"] == bid]
        else:
            df = table
        return df.sort_values("ms" if "ms" in df.columns else "date").reset_index(drop=True)
    if isinstance(table, dict):
        if bid in table:
            v = table[bid]
            if isinstance(v, pd.DataFrame):
                return _frame_for(bid, v)
            if isinstance(v, dict):
                rows = []
                for d, rv in v.items():
                    if isinstance(rv, (list, tuple)) and len(rv) == 3:
                        cc, on, inn = rv
                    elif isinstance(rv, dict):
                        cc, on, inn = rv["r_cc"], rv["r_on"], rv["r_in"]
                    else:
                        raise ValueError(f"table dict 값 형식 오류: {d!r}")
                    rows.append((str(d), float(cc), float(on), float(inn)))
                df = pd.DataFrame(rows, columns=["date", "r_cc", "r_on", "r_in"])
                return df.sort_values("date").reset_index(drop=True)
        raise ValueError(f"table에 bench 없음: {bid!r}")
    raise ValueError(f"table은 DataFrame/딕트: {type(table).__name__}")


def _cols(df):
    dates = df["date"].astype(str).tolist()
    return (dates, {d: i for i, d in enumerate(dates)},
            df["r_cc"].to_numpy(dtype=float),
            df["r_on"].to_numpy(dtype=float),
            df["r_in"].to_numpy(dtype=float))


def bench_return(bench_id, entry_date, entry_at, exit_date, exit_at, table=None):
    """보유 구간 벤치 수익. table None이면 BENCH_DB에서 읽기."""
    if entry_at not in ("open", "close"):
        raise ValueError(f"entry_at은 open/close: {entry_at!r}")
    if exit_at not in ("open", "close"):
        raise ValueError(f"exit_at은 open/close: {exit_at!r}")
    d1, d2 = str(entry_date), str(exit_date)
    df = load(ids=[bench_id]) if table is None else _frame_for(bench_id, table)
    if len(df) == 0:
        raise ValueError(f"bench에 행 없음: {bench_id!r}")
    dates, pos, cc, on, inn = _cols(df)
    if d1 not in pos:
        raise ValueError(f"진입일 없음: {d1}")
    if d2 not in pos:
        raise ValueError(f"청산일 없음: {d2}")
    i1, i2 = pos[d1], pos[d2]
    if i1 > i2:
        raise ValueError(f"진입({d1})이 청산({d2})보다 늦음")
    if i1 == i2:
        if entry_at == "close" and exit_at == "open":
            raise ValueError("같은 날 close→open 불가")
        if entry_at == "open" and exit_at == "close":
            v = float(inn[i1])
            if not np.isfinite(v):
                raise ValueError(f"벤치값 비정상: {d1} r_in")
            return v
        return 0.0
    pieces = []
    if entry_at == "open":
        pieces.append(float(inn[i1]))
    for k in range(i1 + 1, i2):
        pieces.append(float(cc[k]))
    pieces.append(float(cc[i2]) if exit_at == "close" else float(on[i2]))
    if not all(np.isfinite(p) for p in pieces):
        raise ValueError(f"벤치값 비정상: {d1}~{d2}")
    return float(np.prod([1.0 + p for p in pieces]) - 1.0)


def bench_returns(ids, entry_dates, entry_at, exit_dates, exit_at, table=None):
    """bench_return 벡터 버전. entry_at/exit_at은 단일 open/close."""
    if entry_at not in ("open", "close"):
        raise ValueError(f"entry_at은 open/close: {entry_at!r}")
    if exit_at not in ("open", "close"):
        raise ValueError(f"exit_at은 open/close: {exit_at!r}")
    ids = list(ids)
    ed = [str(d) for d in entry_dates]
    xd = [str(d) for d in exit_dates]
    if not (len(ids) == len(ed) == len(xd)):
        raise ValueError("ids/entry_dates/exit_dates 길이 불일치")
    if table is None:
        uniq = sorted(set(str(i) for i in ids))
        base = load(ids=uniq) if uniq else load(ids=[])
    elif isinstance(table, pd.DataFrame):
        base = table
    elif isinstance(table, dict):
        base = None
    else:
        raise ValueError(f"table은 DataFrame/딕트: {type(table).__name__}")
    cache = {}

    def prep(bid):
        if bid in cache:
            return cache[bid]
        df = base if base is None else None
        if df is None:
            df = _frame_for(bid, table) if base is None else None
        if df is None:
            if "bench_id" in base.columns:
                df = base[base["bench_id"] == bid]
            else:
                df = base
            df = df.sort_values("ms" if "ms" in df.columns else "date").reset_index(drop=True)
        if len(df) == 0:
            raise ValueError(f"bench에 행 없음: {bid!r}")
        dates, pos, cc, on, inn = _cols(df)
        rec = (pos, cc, on, inn)
        cache[bid] = rec
        return rec

    out = np.empty(len(ids), dtype=float)
    for i, (bid, d1, d2) in enumerate(zip([str(v) for v in ids], ed, xd)):
        pos, cc, on, inn = prep(bid)
        if d1 not in pos:
            raise ValueError(f"진입일 없음: {d1}")
        if d2 not in pos:
            raise ValueError(f"청산일 없음: {d2}")
        i1, i2 = pos[d1], pos[d2]
        if i1 > i2:
            raise ValueError(f"진입({d1})이 청산({d2})보다 늦음")
        if i1 == i2:
            if entry_at == "close" and exit_at == "open":
                raise ValueError("같은 날 close→open 불가")
            if entry_at == "open" and exit_at == "close":
                v = float(inn[i1])
                if not np.isfinite(v):
                    raise ValueError(f"벤치값 비정상: {d1}")
                out[i] = v
            else:
                out[i] = 0.0
            continue
        f = 1.0
        if entry_at == "open":
            v = float(inn[i1])
            if not np.isfinite(v):
                raise ValueError(f"벤치값 비정상: {d1}")
            f *= 1.0 + v
        a, b = i1 + 1, i2 - 1
        if a <= b:
            # 조회 구간만 곱한다: 전체 누적곱은 구간 앞 NaN 에 오염됨
            seg = cc[a:b + 1]
            if not np.all(np.isfinite(seg)):
                raise ValueError(f"벤치값 비정상: {d1}~{d2}")
            f *= float(np.prod(1.0 + seg))
        last = float(cc[i2]) if exit_at == "close" else float(on[i2])
        if not np.isfinite(last):
            raise ValueError(f"벤치값 비정상: {d2}")
        f *= 1.0 + last
        out[i] = float(f - 1.0)
    return out


class BenchCache:
    """반복 호출용 미리 로드 캐시."""

    def __init__(self, ids=None, out=BENCH_DB, table=None):
        self.table = table if table is not None else load(ids=ids, out=out)

    def bench_return(self, bench_id, entry_date, entry_at, exit_date, exit_at):
        return bench_return(bench_id, entry_date, entry_at, exit_date, exit_at,
                             table=self.table)

    def bench_returns(self, ids, entry_dates, entry_at, exit_dates, exit_at):
        return bench_returns(ids, entry_dates, entry_at, exit_dates, exit_at,
                             table=self.table)
