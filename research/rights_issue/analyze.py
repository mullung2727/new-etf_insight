"""유·무상증자 불기둥 분석 (SPEC: research/rights_issue/SPEC.md).

실행: cd etl && PYTHONPATH=.. uv run python ../research/rights_issue/analyze.py
"""
import bisect
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from research.backtest_daily import data, guards, stats, universe

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
OUT = ROOT / "out"
THS = (0.03, 0.05, 0.07, 0.10)
KINDS = ("유상", "무상", "유무상")
WORDER = ["발표전", "발표직후", "발표후_권리락전", "권리락당일", "권리락이후",
          "권리락당일_유상", "권리락이후_유상", "권리락당일_무상", "권리락이후_무상"]
GROUPS = ["≤0", "0~20%", "20%초과", "미상"]
APPLIED = ("적용값: th=3/5/7/10%(S6) · B=고가/기준가-1, 기준가=close-cmp_prev(S7) · "
           "C=종가/시가-1(S8) · 구간=발표전(D0-20~D0-1)/발표직후(D0~D0+1)/"
           "발표후_권리락전(D0+2~X-1)/권리락당일(X)/권리락이후(X+1~X+5), 유무상=X1·X2 각각(S9) · "
           "발표전상승=(close/ref)곱-1, ≤0/0~20%/20%초과/미상(S10) · "
           "cmp_prev없음: 권리락일B=NaN·그외=전일종가(ms연속시)(S11) · 비교군없음(S13) · "
           "가드=0값제외+halt_ok(20일)+SPAC제외, 유동성10억=전체/통과병기, jump_ok미사용(S14) · "
           "D+1: 진입 D0종가, 청산 D+1시가/종가, limit_up_close 제외, "
           "비용 stats.COSTS(기준 0.35%), 일가중 cost_table · "
           "시각구분=장전(휴장일공시·<09:00)/장중(09:00~15:20)/마감직전(15:20~15:30)/장후(≥15:30) · "
           "공개후진입=장전·장중 D0종가→D+1시가/종가, 장후·마감직전 D+1시가→D+1종가/D+2시가")


def norm_date(s):
    """'YYYY-MM-DD'/'YYYY.MM.DD' → 'YYYYMMDD'. 실패 시 None."""
    if not s:
        return None
    d = re.sub(r"\D", "", str(s))
    return d[:8] if len(d) >= 8 else None


def load_cache():
    """acptno → (유형, paid행, free행, viewer)."""
    paid, free, viewers = {}, {}, {}
    for p in sorted(CACHE.glob("list_paid_*.json")):
        for r in json.loads(p.read_text(encoding="utf-8")).get("rows", []):
            if r.get("acptno") and r["acptno"] not in paid:
                paid[r["acptno"]] = r
    for p in sorted(CACHE.glob("list_free_*.json")):
        for r in json.loads(p.read_text(encoding="utf-8")).get("rows", []):
            if r.get("acptno") and r["acptno"] not in free:
                free[r["acptno"]] = r
    for p in sorted(CACHE.glob("viewer_*.json")):
        v = json.loads(p.read_text(encoding="utf-8"))
        viewers[v.get("acptno") or p.stem.removeprefix("viewer_")] = v
    out = {}
    for ac in paid.keys() | free.keys():
        kind = "유무상" if (ac in paid and ac in free) else ("유상" if ac in paid else "무상")
        out[ac] = (kind, paid.get(ac), free.get(ac), viewers.get(ac))
    return out


def locate(mdates, announce, base):
    """D0=발표일 이상 첫 거래일 ms, X=기준일 미만 마지막 거래일 ms. 없으면 None."""
    d0 = bisect.bisect_left(mdates, announce)
    d0 = d0 if d0 < len(mdates) else None
    x = bisect.bisect_left(mdates, base) - 1
    return d0, (x if x >= 0 else None)


def event_windows(kind, d0, x1, x2=None):
    """[(구간명, 시작ms, 끝ms)]. 양끝 포함."""
    w = [("발표전", d0 - 20, d0 - 1), ("발표직후", d0, d0 + 1),
         ("발표후_권리락전", d0 + 2, x1 - 1)]
    if kind == "유무상":
        w += [("권리락당일_유상", x1, x1), ("권리락이후_유상", x1 + 1, x1 + 5),
              ("권리락당일_무상", x2, x2), ("권리락이후_무상", x2 + 1, x2 + 5)]
    else:
        w += [("권리락당일", x1, x1), ("권리락이후", x1 + 1, x1 + 5)]
    return w


def add_ref(df_t, x_set):
    """단일 종목 df(ms 오름차순)에 ref 컬럼 추가. x_set=X ms 집합."""
    df = df_t.sort_values("ms").reset_index(drop=True)
    ms = df["ms"].to_numpy()
    close = df["close"].to_numpy(float)
    if "cmp_prev" in df:
        cmpv = pd.to_numeric(df["cmp_prev"], errors="coerce").to_numpy(dtype=float)
    else:
        cmpv = np.full(len(df), np.nan)
    ref = np.full(len(df), np.nan)
    has = ~np.isnan(cmpv)
    ref[has] = close[has] - cmpv[has]
    prev = np.full(len(df), np.nan)
    idx = np.where(ms[1:] == ms[:-1] + 1)[0] + 1
    prev[idx] = close[idx - 1]
    need = ~has & ~np.isin(ms, list(x_set))
    ref[need] = prev[need]
    df["ref"] = ref
    return df


def hit_stats(vals, ms_arr, start_ms, th):
    """구간 결과 (hit 1/0/NaN, first_off). 판정불가만 있으면 (NaN, NaN)."""
    v = np.asarray(vals, float)
    ok = ~np.isnan(v)
    if not ok.any():
        return np.nan, np.nan
    hits = ok & (v >= th)
    if not hits.any():
        return 0, np.nan
    return 1, int(np.asarray(ms_arr)[np.where(hits)[0][0]] - start_ms)


def pre20_and_group(df, d0):
    """(pre20, group). 20행+ref유효 아니면 (NaN, 미상)."""
    w = df[(df["ms"] >= d0 - 20) & (df["ms"] <= d0 - 1)]
    pre = np.nan
    if len(w) == 20 and (w["ref"] > 0).all() and (w["close"] > 0).all():
        pre = float(np.prod(w["close"].to_numpy(float) / w["ref"].to_numpy(float)) - 1)
    if np.isnan(pre):
        g = "미상"
    elif pre <= 0:
        g = "≤0"
    elif pre <= 0.20:
        g = "0~20%"
    else:
        g = "20%초과"
    return pre, g


def verify_x(df, x):
    """X행 검증: 'ok'/'anomaly'/'unverified'."""
    i = np.where(df["ms"].to_numpy() == x)[0]
    if not len(i):
        return "unverified"
    i = int(i[0])
    cmpv = pd.to_numeric(df["cmp_prev"], errors="coerce").to_numpy(dtype=float)
    if np.isnan(cmpv[i]) or i == 0:
        return "unverified"
    ref = float(df["close"].iloc[i]) - cmpv[i]
    return "ok" if ref < float(df["close"].iloc[i - 1]) else "anomaly"


def d1_trade(df, d0, x1):
    """D+1 거래 계산. 성공 시 {"op","cl","hi"} dict, 제외 시 사유 문자열."""
    if x1 <= d0 + 1:
        return "D+1 권리락"
    ms = df["ms"].to_numpy()
    di = np.where(ms == d0)[0]
    ni = np.where(ms == d0 + 1)[0]
    if not len(di) or not len(ni):
        return "D+1 행 없음"
    dpos = int(di[0])
    r = guards.limit_up_close(df, dpos)
    if r is not None:
        return r
    c0 = float(df["close"].iloc[dpos])
    npos = int(ni[0])
    return {"op": float(df["open"].iloc[npos]) / c0 - 1,
            "cl": float(df["close"].iloc[npos]) / c0 - 1,
            "hi": float(df["high"].iloc[npos]) / c0 - 1}


def load_search():
    """cache/search_*_*.json 행 전부 로드."""
    rows = []
    for p in sorted(CACHE.glob("search_*_*.json")):
        rows.extend(json.loads(p.read_text(encoding="utf-8")).get("rows", []))
    return rows


def match_time(pool, company, kind, ann):
    """(회사명, 발표일) 일치 + 유형 제목 조건 → 가장 이른 HH:MM. 없으면 None."""
    want = "유무상증자결정" if kind == "유무상" else ("유상증자결정" if kind == "유상" else "무상증자결정")
    best = None
    for r in pool:
        if r.get("company") != company:
            continue
        if norm_date(r.get("date")) != ann:
            continue
        t = r.get("title") or ""
        if t.startswith("[정정]") or "자회사" in t:
            continue
        if kind == "유무상":
            if want not in t:
                continue
        elif want not in t or "유무상" in t:
            continue
        tm = r.get("time")
        if tm and (best is None or tm < best):
            best = tm
    return best


def time_class(ann, d0date, tm):
    """공시시각 구분. ann·d0date='YYYYMMDD', tm='HH:MM'/None."""
    if ann != d0date:
        return "장전"
    if tm is None:
        return "시각미상"
    if tm < "09:00":
        return "장전"
    if tm < "15:20":
        return "장중"
    if tm < "15:30":
        return "마감직전"
    return "장후"


def post_trade(df, d0, x1, cls):
    """공개후 진입 거래. 성공 시 {"op","cl","hi"} dict, 제외 시 사유 문자열."""
    if cls == "시각미상":
        return "시각미상"
    if cls in ("장전", "장중"):
        return d1_trade(df, d0, x1)
    if x1 <= d0 + 2:
        return "D+2 권리락"
    ms = df["ms"].to_numpy()
    di = np.where(ms == d0)[0]
    if not len(di):
        return "D+1 행 없음"
    dpos = int(di[0])
    if dpos + 1 >= len(df) or ms[dpos + 1] != d0 + 1:
        return "D+1 행 없음"
    r = guards.limit_up_open(df, dpos)
    if r is not None:
        return r
    ni = np.where(ms == d0 + 2)[0]
    if not len(ni):
        return "D+2 행 없음"
    o1 = float(df["open"].iloc[dpos + 1])
    return {"op": float(df["open"].iloc[int(ni[0])]) / o1 - 1,
            "cl": float(df["close"].iloc[dpos + 1]) / o1 - 1,
            "hi": float(df["high"].iloc[dpos + 1]) / o1 - 1}


def main():
    print(APPLIED)
    raw = load_cache()
    skip = {}
    d1skip = {}

    def drop(reason):
        skip[reason] = skip.get(reason, 0) + 1

    evs = []
    for ac in sorted(raw):
        kind, pr, fr, vw = raw[ac]
        if vw is None:
            drop("viewer 없음")
            continue
        code = vw.get("stock_code")
        ann = norm_date(vw.get("first_announce_date"))
        if not code or not ann:
            drop("viewer stock_code/발표일 결측")
            continue
        bp = norm_date(pr.get("기준일")) if pr else None
        bf = norm_date(fr.get("배정기준일")) if fr else None
        bx = (bp, bf) if kind == "유무상" else ((bp,) if kind == "유상" else (bf,))
        if not all(bx):
            drop("기준일 없음")
            continue
        lname = pr.get("회사명") if kind in ("유상", "유무상") else fr.get("회사명")
        evs.append((ac, kind, code, ann, bx[0], bx[1] if kind == "유무상" else None, lname))

    px = data.load_px()
    mdates = data.market_dates()
    names = data.stock_names()
    halt = guards.halt_ok(px, mdates)
    tk = px["ticker"].to_numpy()
    spool = load_search()

    erecs, wrecs, trecs, precs = [], [], [], []
    for ac, kind, code, ann, b1, b2, lname in evs:
        d0, x1 = locate(mdates, ann, b1)
        x2 = locate(mdates, ann, b2)[1] if b2 else None
        if d0 is None:
            drop("D0 없음(범위 밖)")
            continue
        if x1 is None or (b2 and x2 is None):
            drop("X 없음(범위 밖)")
            continue
        wins = event_windows(kind, d0, x1, x2)
        if x1 < d0 or (x2 is not None and x2 < d0):
            drop("X가 D0보다 앞")
            continue
        lo = int(np.searchsorted(tk, code, side="left"))
        hi = int(np.searchsorted(tk, code, side="right"))
        if lo == hi:
            drop("D0 행 없음")
            continue
        sub = px.iloc[lo:hi]
        sms = sub["ms"].to_numpy()
        di = np.where(sms == d0)[0]
        if not len(di):
            drop("D0 행 없음")
            continue
        if not halt[sub.index.to_numpy()[int(di[0])]]:
            drop("halt 제외")
            continue
        name = names.get(code, "")
        if universe.SPAC_RE.search(name or ""):
            drop("SPAC 제외")
            continue
        xs = {x1} | ({x2} if x2 is not None else set())
        df = add_ref(sub, xs)
        ms = df["ms"].to_numpy()
        dpos = int(np.where(ms == d0)[0][0])
        liq_ok = guards.liquidity(df, dpos) is None
        ref = df["ref"].to_numpy(float)
        op = df["open"].to_numpy(float)
        cl = df["close"].to_numpy(float)
        B = np.full(len(df), np.nan)
        B[ref > 0] = df["high"].to_numpy(float)[ref > 0] / ref[ref > 0] - 1
        C = np.full(len(df), np.nan)
        C[op > 0] = cl[op > 0] / op[op > 0] - 1
        pre, grp = pre20_and_group(df, d0)
        vs = [verify_x(df, x) for x in sorted(xs)]
        tm = match_time(spool, lname, kind, ann)
        cls = time_class(ann, mdates[d0], tm)
        erecs.append({"acptno": ac, "유형": kind, "code": code, "name": name,
                      "발표일": ann, "D0": mdates[d0],
                      "X": (mdates[x1] if x2 is None else ""),
                      "X1": (mdates[x1] if x2 is not None else ""),
                      "X2": (mdates[x2] if x2 is not None else ""),
                      "pre20": pre, "group": grp, "liq_ok": liq_ok,
                      "x_anomaly": "anomaly" in vs, "x_unverified": "unverified" in vs,
                      "공시시각": tm or "", "시각구분": cls})
        d1 = d1_trade(df, d0, min(xs))
        if isinstance(d1, str):
            d1skip[d1] = d1skip.get(d1, 0) + 1
        else:
            dd = mdates[d0]
            trecs.append({"acptno": ac, "유형": kind, "group": grp,
                          "liq_ok": liq_ok, "date": dd, "year": dd[:4], **d1})
        pt = post_trade(df, d0, min(xs), cls)
        if not isinstance(pt, str):
            dd = mdates[d0]
            precs.append({"acptno": ac, "유형": kind, "cls": cls,
                          "entry": "D0종가" if cls in ("장전", "장중") else "D+1시가",
                          "liq_ok": liq_ok, "date": dd, "year": dd[:4], **pt})
        for wn, s, e in wins:
            sel = (ms >= s) & (ms <= e)
            if not sel.any():
                continue
            msm = ms[sel]
            for metric, vals in (("B", B), ("C", C)):
                v = vals[sel]
                for th in THS:
                    hit, fo = hit_stats(v, msm, s, th)
                    wrecs.append({"acptno": ac, "유형": kind, "group": grp,
                                  "liq_ok": liq_ok, "window": wn, "metric": metric,
                                  "th": th, "hit": hit, "first_off": fo})

    OUT.mkdir(parents=True, exist_ok=True)
    E = pd.DataFrame(erecs, columns=["acptno", "유형", "code", "name", "발표일", "D0",
                                     "X", "X1", "X2", "pre20", "group", "liq_ok",
                                     "x_anomaly", "x_unverified", "공시시각", "시각구분"])
    W = pd.DataFrame(wrecs, columns=["acptno", "유형", "group", "liq_ok", "window",
                                     "metric", "th", "hit", "first_off"])
    if not W.empty:
        W["_wo"] = W["window"].map({w: i for i, w in enumerate(WORDER)})
        W = W.sort_values(["acptno", "_wo", "metric", "th"]).drop(columns="_wo")
        W = W.reset_index(drop=True)

    srecs = []
    for kind in KINDS:
        for wn in WORDER:
            for metric in ("B", "C"):
                for th in THS:
                    gdf = W[(W["유형"] == kind) & (W["window"] == wn)
                            & (W["metric"] == metric) & (W["th"] == th)]
                    if gdf.empty:
                        continue
                    for liq_label in ("전체", "통과"):
                        ldf = gdf if liq_label == "전체" else gdf[gdf["liq_ok"] == True]
                        for grp_label in ["전체"] + GROUPS:
                            sdf = ldf if grp_label == "전체" else ldf[ldf["group"] == grp_label]
                            ok = sdf.dropna(subset=["hit"])
                            n = len(ok)
                            hr = float(ok["hit"].mean()) if n else np.nan
                            fo = ok.loc[ok["hit"] == 1, "first_off"].to_numpy(float)
                            med = float(np.median(fo)) if len(fo) else np.nan
                            c = [int((fo == i).sum()) for i in range(5)] + [int((fo >= 5).sum())]
                            srecs.append({"유형": kind, "window": wn, "metric": metric,
                                          "th": th, "liq": liq_label, "group": grp_label,
                                          "n": n, "hit_rate": hr, "first_off_med": med,
                                          "fo0": c[0], "fo1": c[1], "fo2": c[2],
                                          "fo3": c[3], "fo4": c[4], "fo5p": c[5]})
    S = pd.DataFrame(srecs, columns=["유형", "window", "metric", "th", "liq", "group",
                                     "n", "hit_rate", "first_off_med",
                                     "fo0", "fo1", "fo2", "fo3", "fo4", "fo5p"])
    E.to_csv(OUT / "events.csv", index=False, encoding="utf-8")
    if not W.empty:
        W["hit"] = W["hit"].astype("Int64")
        W["first_off"] = W["first_off"].astype("Int64")
    W.to_csv(OUT / "windows.csv", index=False, encoding="utf-8")
    S.to_csv(OUT / "summary.csv", index=False, encoding="utf-8")

    T = pd.DataFrame(trecs, columns=["acptno", "유형", "group", "liq_ok", "date",
                                     "year", "op", "cl", "hi"])
    T.to_csv(OUT / "d1_trades.csv", index=False, encoding="utf-8")

    sufs = [(c, f"{c * 100:g}") for c in stats.COSTS]

    def d1_row(sdf):
        n = len(sdf)
        row = {"n": n, "n_days": int(sdf["date"].nunique()) if n else 0}
        for lbl, th in (("H3", 0.03), ("H5", 0.05), ("H7", 0.07), ("H10", 0.10)):
            row[lbl] = float((sdf["hi"] >= th).mean()) if n else np.nan
        for col in ("op", "cl"):
            ct = stats.cost_table(sdf[col].to_numpy(float),
                                  stats.day_key(sdf["date"]))
            for c, suf in sufs:
                cc = ct["costs"][str(c)]
                row[f"{col}_mean_{suf}"] = cc["mean"]
                row[f"{col}_t_{suf}"] = cc["t"]
            row[f"{col}_med"] = ct["med"]
            row[f"{col}_win"] = ct["win"]
            net = sdf.assign(_net=sdf[col] - 0.0035)
            row[f"{col}_daymed"] = stats.day_median(net, "_net")
            row[f"{col}_daywin"] = stats.day_win_rate(net, "_net")
        return row

    dcols = ["cut", "유형", "liq", "group", "year", "n", "n_days",
             "H3", "H5", "H7", "H10"]
    for col in ("op", "cl"):
        for _, suf in sufs:
            dcols += [f"{col}_mean_{suf}", f"{col}_t_{suf}"]
        dcols += [f"{col}_med", f"{col}_win", f"{col}_daymed", f"{col}_daywin"]
    drecs = []
    for kind in KINDS:
        kdf = T[T["유형"] == kind]
        for liq_label in ("전체", "통과"):
            ldf = kdf if liq_label == "전체" else kdf[kdf["liq_ok"] == True]
            for grp_label in ["전체"] + GROUPS:
                sdf = ldf if grp_label == "전체" else ldf[ldf["group"] == grp_label]
                drecs.append({"cut": "group", "유형": kind, "liq": liq_label,
                              "group": grp_label, "year": "전체", **d1_row(sdf)})
            for yr in sorted(ldf["year"].dropna().unique()):
                sdf = ldf[ldf["year"] == yr]
                drecs.append({"cut": "year", "유형": kind, "liq": liq_label,
                              "group": "전체", "year": yr, **d1_row(sdf)})
    D1 = pd.DataFrame(drecs, columns=dcols)
    D1.to_csv(OUT / "d1_summary.csv", index=False, encoding="utf-8")

    P = pd.DataFrame(precs, columns=["acptno", "유형", "cls", "entry", "liq_ok",
                                     "date", "year", "op", "cl", "hi"])
    P.to_csv(OUT / "post_trades.csv", index=False, encoding="utf-8")

    CLS4 = ["장전", "장중", "마감직전", "장후"]
    pcols = ["cut", "유형", "liq", "cls", "year", "n", "n_days",
             "H3", "H5", "H7", "H10"]
    for col in ("op", "cl"):
        for _, suf in sufs:
            pcols += [f"{col}_mean_{suf}", f"{col}_t_{suf}"]
        pcols += [f"{col}_med", f"{col}_win", f"{col}_daymed", f"{col}_daywin"]
    prcs = []
    for kind in KINDS:
        kdf = P[P["유형"] == kind]
        for liq_label in ("전체", "통과"):
            ldf = kdf if liq_label == "전체" else kdf[kdf["liq_ok"] == True]
            for c in CLS4:
                sdf = ldf[ldf["cls"] == c]
                prcs.append({"cut": "cls", "유형": kind, "liq": liq_label,
                             "cls": c, "year": "전체", **d1_row(sdf)})
    mdf = P[P["유형"] == "무상"]
    for liq_label in ("전체", "통과"):
        ldf = mdf if liq_label == "전체" else mdf[mdf["liq_ok"] == True]
        for yr in sorted(ldf["year"].dropna().unique()):
            ydf = ldf[ldf["year"] == yr]
            prcs.append({"cut": "year", "유형": "무상", "liq": liq_label,
                         "cls": "공개전종가가능", "year": yr,
                         **d1_row(ydf[ydf["cls"].isin(["장전", "장중"])])})
            prcs.append({"cut": "year", "유형": "무상", "liq": liq_label,
                         "cls": "장후", "year": yr,
                         **d1_row(ydf[ydf["cls"].isin(["장후", "마감직전"])])})
    PS = pd.DataFrame(prcs, columns=pcols)
    PS.to_csv(OUT / "post_summary.csv", index=False, encoding="utf-8")

    nk = {k: int((E["유형"] == k).sum()) for k in KINDS}
    print(f"후보 acptno={len(raw)} → 포함={len(E)} "
          f"(유상={nk['유상']}, 무상={nk['무상']}, 유무상={nk['유무상']})")
    print("제외: " + (", ".join(f"{k}={v}" for k, v in skip.items()) if skip else "없음"))
    for kind in KINDS:
        sub = E[E["유형"] == kind]
        a = int(sub["x_anomaly"].sum()) if len(sub) else 0
        u = int(sub["x_unverified"].sum()) if len(sub) else 0
        print(f"[검증] {kind} n={len(sub)} x_anomaly={a} x_unverified={u}")
    cols = [("B", t) for t in THS] + [("C", t) for t in THS]
    for kind in KINDS:
        print(f"[{kind}] hit_rate (group=전체, liq=전체)")
        if not (E["유형"] == kind).any():
            print("(없음)")
            continue
        cell = {(r["window"], r["metric"], r["th"]): r["hit_rate"]
                for r in S[(S["유형"] == kind) & (S["liq"] == "전체")
                           & (S["group"] == "전체")].to_dict("records")}
        order = (WORDER[:3] + WORDER[5:]) if kind == "유무상" else WORDER[:5]
        print("window".ljust(18) + "".join(f"{m}{int(t * 100):02d}".rjust(8) for m, t in cols))
        for wn in order:
            line = wn.ljust(18)
            for m, t in cols:
                v = cell.get((wn, m, t), np.nan)
                line += ("-" if pd.isna(v) else f"{v:.3f}").rjust(8)
            print(line)

    print("D+1 제외: " + (", ".join(f"{k}={v}" for k, v in d1skip.items())
                          if d1skip else "없음"))

    def pct(v):
        return "-" if pd.isna(v) else f"{float(v) * 100:.1f}%"

    def t2(v):
        return "-" if pd.isna(v) else f"{float(v):.2f}"

    d1idx = {(r["cut"], r["유형"], r["liq"], r["group"], r["year"]): r
             for r in D1.to_dict("records")}

    def d1line(r):
        return (f"{r['n']:>5} {pct(r['H10']):>7} {pct(r['op_mean_0.35']):>7} "
                f"{t2(r['op_t_0.35']):>7} {pct(r['op_med']):>7} "
                f"{pct(r['op_daywin']):>7} {pct(r['cl_mean_0.35']):>7} "
                f"{pct(r['cl_med']):>7}")

    chead = "    n     H10  op_m35    op_t  op_med op_dayw  cl_m35  cl_med"
    print("[D+1] 유형 × group (liq=전체)")
    print("유형   group     " + chead)
    for kind in KINDS:
        for grp_label in ["전체"] + GROUPS:
            r = d1idx.get(("group", kind, "전체", grp_label, "전체"))
            print(f"{kind:<6}{grp_label:<10}"
                  + (d1line(r) if r is not None else "(없음)"))
    print("[D+1 무상] 연도별 (liq=전체)")
    print("year  " + chead)
    yrs = sorted(T[T["유형"] == "무상"]["year"].unique()) if len(T) else []
    if not yrs:
        print("(없음)")
    for yr in yrs:
        r = d1idx.get(("year", "무상", "전체", "전체", yr))
        print(f"{yr:<6}" + (d1line(r) if r is not None else "(없음)"))

    CLS5 = CLS4 + ["시각미상"]
    print("[매칭] 유형 × 시각구분")
    print("유형  " + "".join(c.rjust(8) for c in CLS5))
    for kind in KINDS:
        sub = E[E["유형"] == kind]
        print(f"{kind:<6}" + "".join(f"{int((sub['시각구분'] == c).sum()):>8}" for c in CLS5))

    def acline(r):
        return (f"{r['n']:>5} {pct(r['op_mean_0.35']):>7} "
                f"{t2(r['op_t_0.35']):>7} {pct(r['op_med']):>7} "
                f"{pct(r['cl_mean_0.35']):>7}")

    ahead = "    n  op_m35    op_t  op_med  cl_m35"
    print("[표A D+1] 유형 × 시각구분 (liq=전체)")
    print("유형   시각구분" + ahead)
    for kind in KINDS:
        for c in CLS5:
            acs = set(E[(E["유형"] == kind) & (E["시각구분"] == c)]["acptno"])
            print(f"{kind:<6}{c:<8}" + acline(d1_row(T[T["acptno"].isin(acs)])))
    print("[표B 공개후] 유형 × cls (liq=전체)")
    print("유형   cls     " + ahead)
    for kind in KINDS:
        for c in CLS4:
            print(f"{kind:<6}{c:<8}"
                  + acline(d1_row(P[(P["유형"] == kind) & (P["cls"] == c)])))
    print("[표C 공개후 무상] 연도별 × 그룹 (liq=전체)")
    print("year  그룹      " + ahead)
    pyrs = sorted(P[P["유형"] == "무상"]["year"].unique()) if len(P) else []
    if not pyrs:
        print("(없음)")
    for yr in pyrs:
        ydf = P[(P["유형"] == "무상") & (P["year"] == yr)]
        for g, members in (("공개전종가가능", ["장전", "장중"]), ("장후", ["장후", "마감직전"])):
            print(f"{yr:<6}{g:<10}" + acline(d1_row(ydf[ydf["cls"].isin(members)])))


def selfcheck():
    n = 30
    ms = np.arange(n)
    op = np.full(n, 100.0)
    hi = np.full(n, 101.0)
    cl = np.full(n, 100.0)
    hi[5] = 110.0
    cl[5] = 107.0
    hi[6] = 104.0
    hi[7] = 102.0
    cmpv = np.full(n, np.nan)
    cl[20] = 60.0
    op[20] = 55.0
    hi[20] = 62.0
    cmpv[20] = 10.0
    base = pd.DataFrame({"ms": ms, "open": op, "high": hi, "close": cl, "cmp_prev": cmpv})
    X = 20
    df = add_ref(base, {X})
    r = df["ref"].to_numpy()
    assert r[20] == 50.0, r[20]
    assert r[5] == 100.0 and r[21] == 60.0, r
    df_nan = add_ref(base.assign(cmp_prev=np.nan), {X})
    assert np.isnan(df_nan["ref"].iloc[20])
    assert df_nan["ref"].iloc[21] == 60.0
    gap = add_ref(base.drop(index=[10]).reset_index(drop=True), {X})
    assert np.isnan(gap.loc[gap["ms"] == 11, "ref"].iloc[0])

    B = df["high"].to_numpy() / r - 1
    C = df["close"].to_numpy() / df["open"].to_numpy() - 1
    assert abs(B[5] - 0.10) < 1e-12 and abs(C[5] - 0.07) < 1e-12
    h, fo = hit_stats(B[4:7], ms[4:7], 4, 0.05)
    assert (h, fo) == (1, 1), (h, fo)
    h, fo = hit_stats(B[6:8], ms[6:8], 6, 0.05)
    assert h == 0 and np.isnan(fo), (h, fo)
    h, fo = hit_stats(C[5:6], ms[5:6], 5, 0.10)
    assert h == 0 and np.isnan(fo), (h, fo)
    h, fo = hit_stats(B[20:21], ms[20:21], 20, 0.10)
    assert (h, fo) == (1, 0), (h, fo)
    h, fo = hit_stats(np.array([np.nan, np.nan]), np.array([20, 21]), 20, 0.03)
    assert np.isnan(h) and np.isnan(fo), (h, fo)
    h, fo = hit_stats(np.array([np.nan, 0.05]), np.array([20, 21]), 20, 0.03)
    assert (h, fo) == (1, 1), (h, fo)

    w = {n: (s, e) for n, s, e in event_windows("유상", 10, 20)}
    assert w["발표전"] == (-10, 9) and w["발표직후"] == (10, 11)
    assert w["발표후_권리락전"] == (12, 19) and w["권리락당일"] == (20, 20)
    assert w["권리락이후"] == (21, 25)
    wm = {n: (s, e) for n, s, e in event_windows("유무상", 10, 20, 26)}
    assert wm["발표후_권리락전"] == (12, 19)
    assert wm["권리락당일_유상"] == (20, 20) and wm["권리락이후_무상"] == (27, 31)
    we = {n: (s, e) for n, s, e in event_windows("유상", 10, 10)}
    assert we["발표후_권리락전"][0] > we["발표후_권리락전"][1]
    sel = (ms >= 5) & (ms <= 6)
    assert ms[sel].tolist() == [5, 6]
    assert not ((ms >= 100) & (ms <= 105)).any()

    t = pd.DataFrame({"ms": np.arange(20), "close": 100.0, "ref": 100.0})
    pre, g = pre20_and_group(t, 20)
    assert pre == 0.0 and g == "≤0", (pre, g)
    t2 = t.copy()
    t2.loc[19, "close"] = 120.0
    pre, g = pre20_and_group(t2, 20)
    assert abs(pre - 0.2) < 1e-9 and g == "0~20%", (pre, g)
    t3 = t.copy()
    t3.loc[19, "close"] = 120.01
    pre, g = pre20_and_group(t3, 20)
    assert pre > 0.2 and g == "20%초과", (pre, g)
    t4 = t.copy()
    t4.loc[0, "close"] = 90.0
    pre, g = pre20_and_group(t4, 20)
    assert pre < 0 and g == "≤0", (pre, g)
    pre, g = pre20_and_group(t.iloc[:19], 20)
    assert np.isnan(pre) and g == "미상", (pre, g)
    t6 = t.copy()
    t6.loc[5, "ref"] = np.nan
    pre, g = pre20_and_group(t6, 20)
    assert np.isnan(pre) and g == "미상", (pre, g)

    assert verify_x(df, 20) == "ok"
    assert verify_x(df_nan, 20) == "unverified"
    assert verify_x(df, 99) == "unverified"
    bad = add_ref(base.assign(cmp_prev=np.where(ms == 20, -50.0, np.nan)), {X})
    assert verify_x(bad, 20) == "anomaly"
    assert norm_date("2024-03-11") == "20240311"
    assert norm_date("2024.03.11") == "20240311"
    assert norm_date(None) is None
    assert locate(["20240102", "20240103"], "20240105", "20240105") == (None, 1)
    assert locate(["20240102", "20240103"], "20240101", "20240102") == (0, None)

    m = np.arange(10)
    o = np.full(10, 100.0)
    h = np.full(10, 101.0)
    c = np.full(10, 100.0)
    o[6] = 101.0
    h[6] = 103.0
    c[6] = 102.0
    f = pd.DataFrame({"ms": m, "open": o, "high": h, "close": c})
    r = d1_trade(f, 5, 8)
    assert (abs(r["op"] - 0.01) < 1e-12 and abs(r["cl"] - 0.02) < 1e-12
            and abs(r["hi"] - 0.03) < 1e-12), r
    f2 = f.copy()
    f2.loc[5, "close"] = 130.0
    assert d1_trade(f2, 5, 8) == guards.LIMIT_UP
    assert d1_trade(f, 5, 6) == "D+1 권리락"
    assert d1_trade(f.drop(index=[6]).reset_index(drop=True), 5, 8) == "D+1 행 없음"

    assert time_class("20240311", "20240311", "08:59") == "장전"
    assert time_class("20240311", "20240311", "09:00") == "장중"
    assert time_class("20240311", "20240311", "15:19") == "장중"
    assert time_class("20240311", "20240311", "15:20") == "마감직전"
    assert time_class("20240311", "20240311", "15:30") == "장후"
    assert time_class("20240309", "20240311", "10:00") == "장전"
    assert time_class("20240311", "20240311", None) == "시각미상"

    pool = [
        {"company": "A", "date": "2024-03-11", "time": "08:10", "title": "[정정]무상증자결정"},
        {"company": "A", "date": "2024-03-11", "time": "08:30", "title": "무상증자결정(자회사)"},
        {"company": "A", "date": "2024-03-11", "time": "08:00", "title": "유무상증자결정"},
        {"company": "A", "date": "2024-03-11", "time": "16:30", "title": "무상증자결정"},
        {"company": "A", "date": "2024-03-11", "time": "09:10", "title": "무상증자결정"},
    ]
    assert match_time(pool, "A", "무상", "20240311") == "09:10"
    assert match_time(pool, "A", "유무상", "20240311") == "08:00"
    assert match_time(pool, "A", "유상", "20240311") is None
    assert match_time(pool, "B", "무상", "20240311") is None

    pt = post_trade(f, 5, 8, "장후")
    assert abs(pt["op"] - (100.0 / 101.0 - 1)) < 1e-12 and abs(pt["cl"] - (102.0 / 101.0 - 1)) < 1e-12, pt
    f3 = f.copy()
    f3.loc[5, "close"] = 70.0
    assert post_trade(f3, 5, 8, "장후") == guards.LIMIT_UP
    assert post_trade(f, 5, 7, "장후") == "D+2 권리락"
    assert post_trade(f, 5, 8, "시각미상") == "시각미상"


if __name__ == "__main__":
    if "--selfcheck" in sys.argv[1:]:
        selfcheck()
        print("selfcheck passed")
    else:
        main()
