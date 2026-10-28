"""MAD 리서치 실행 — 패널 → 분석표 → RESULTS_{KR,US}.md."""
from __future__ import annotations

import argparse
import sys
import warnings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

from research.backtest_daily.data_us import load_etf_tr
from research.backtest_daily.stats import COSTS as KR_COSTS
from research.ccc.sort import nw_tstat
from research.mad import panel as P

US_COSTS = (0.001, 0.002)
H_LIST = (1, 3, 6)
RET_COLS = [f"ret_{k}" for k in range(1, 7)]


def _nw(x) -> tuple[float, float]:
    v = np.asarray(x, dtype=float)
    v = v[np.isfinite(v)]
    if v.size < 2:
        return (float(v.mean()) if v.size else float("nan"), float("nan"))
    b, t = nw_tstat(v, lags=6)
    return (float(b[0]), float(t[0]))


def _p(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v * 100:.2f}" if np.isfinite(v) else "nan"


def _t(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "nan"
    return f"{v:.2f}" if np.isfinite(v) else "nan"


def _mt(m, t) -> str:
    return f"{_p(m)} ({_t(t)})"


def _ym_next(ym: int) -> int:
    y, m = divmod(int(ym), 100)
    return y * 100 + m + 1 if m < 12 else (y + 1) * 100 + 1


def _qbin(v: pd.Series, q: int) -> pd.Series:
    out = pd.Series(np.nan, index=v.index)
    vv = pd.to_numeric(v, errors="coerce")
    ok = vv.notna()
    if int(ok.sum()) < q:
        return out
    try:
        out[ok] = pd.qcut(vv[ok].rank(method="first"), q,
                          labels=list(range(1, q + 1))).astype(int).to_numpy()
    except ValueError:
        pass
    return out


def _add_bins(df: pd.DataFrame, size_col: str) -> pd.DataFrame:
    df = df.copy()
    for name in ("_d10", "_m5", "_s5", "_s3", "_h3", "_h10", "_m3", "_m10"):
        df[name] = np.nan
    for _, g in df.groupby("_fi", sort=False):
        gi = g.index
        df.loc[gi, "_d10"] = _qbin(g["mad"], 10).to_numpy()
        df.loc[gi, "_m5"] = _qbin(g["mad"], 5).to_numpy()
        df.loc[gi, "_s5"] = _qbin(g[size_col], 5).to_numpy()
        df.loc[gi, "_s3"] = _qbin(g[size_col], 3).to_numpy()
        df.loc[gi, "_h3"] = _qbin(g["hi52"], 3).to_numpy()
        df.loc[gi, "_h10"] = _qbin(g["hi52"], 10).to_numpy()
        df.loc[gi, "_m3"] = _qbin(g["mom"], 3).to_numpy()
        df.loc[gi, "_m10"] = _qbin(g["mom"], 10).to_numpy()
    df["_a01"] = pd.to_numeric(df["align"], errors="coerce")
    return df


def _prep(panel: pd.DataFrame, flag: str, size_col: str):
    df = panel[panel[flag]].copy().reset_index(drop=True)
    df["ticker"] = df["ticker"].astype(str)
    yms = sorted(int(y) for y in df["ym"].unique().tolist())
    for a, b in zip(yms, yms[1:]):
        if _ym_next(a) != b:
            print(f"[warn] {flag}: 형성월 간격 {a}->{b}", flush=True)
    df["_fi"] = df["ym"].map({y: i for i, y in enumerate(yms)}).astype(int)
    return _add_bins(df, size_col), yms


def _jt(R: np.ndarray, H: int) -> np.ndarray:
    """형성월×k 보유수익 → 수익월 JT 평균. R[fi,k], 반환 j=1..F-1."""
    F, K = R.shape
    if F < 2:
        return np.full(0, np.nan)
    out = np.full(F - 1, np.nan)
    for jj in range(F - 1):
        j = jj + 1
        acc = [R[j - k, k - 1] for k in range(1, min(H, K) + 1) if j - k >= 0]
        v = np.asarray(acc, dtype=float)
        v = v[np.isfinite(v)]
        if v.size:
            out[jj] = float(v.mean())
    return out


def _jt_cube(E: np.ndarray, H: int) -> np.ndarray:
    F, D, _ = E.shape
    if F < 2:
        return np.full((0, D), np.nan)
    return np.column_stack([_jt(E[:, d, :], H) for d in range(D)])


def _decile_mats(df: pd.DataFrame, w_col: str):
    F = int(df["_fi"].max()) + 1 if len(df) else 0
    E = np.full((F, 10, 6), np.nan)
    V = np.full((F, 10, 6), np.nan)
    U = np.full((F, 6), np.nan)
    if len(df) == 0:
        return E, V, U
    g = df.dropna(subset=["_d10"]).copy()
    g["_d10"] = g["_d10"].astype(int)
    for (fi, d), row in g.groupby(["_fi", "_d10"])[RET_COLS].mean().iterrows():
        E[int(fi), int(d) - 1, :] = row.to_numpy(dtype=float)
    w = pd.to_numeric(g[w_col], errors="coerce")
    okw = w.notna() & (w > 0)
    gw = g[okw].assign(_w=w[okw].to_numpy(dtype=float))
    if len(gw):
        fiw = gw["_fi"].to_numpy()
        diw = gw["_d10"].to_numpy()
        ww = gw["_w"].to_numpy(dtype=float)
        for ci, c in enumerate(RET_COLS):
            m = gw[c].notna().to_numpy()
            num = pd.Series((gw[c].to_numpy(dtype=float) * ww)[m]).groupby(
                [fiw[m], diw[m]]).sum()
            den = pd.Series(ww[m]).groupby([fiw[m], diw[m]]).sum()
            for (fi, d), vv in (num / den).items():
                V[int(fi), int(d) - 1, ci] = float(vv)
    for fi, row in df.groupby("_fi")[RET_COLS].mean().iterrows():
        U[int(fi), :] = row.to_numpy(dtype=float)
    return E, V, U


def _size_bench(df: pd.DataFrame) -> pd.DataFrame:
    b = df.dropna(subset=["_s5"]).copy()
    b["_s5"] = b["_s5"].astype(int)
    return b.groupby(["_fi", "_s5"])[RET_COLS].mean()


def _excess_mat(df: pd.DataFrame, bench: pd.DataFrame, mask) -> np.ndarray:
    """mask 종목의 ret_k − size5 벤치, 형성월 평균 → [F,6]."""
    F = int(df["_fi"].max()) + 1 if len(df) else 0
    out = np.full((F, 6), np.nan)
    d = df[mask].dropna(subset=["_s5"]).copy()
    if len(d) == 0:
        return out
    d["_s5"] = d["_s5"].astype(int)
    m = d.merge(bench, left_on=["_fi", "_s5"], right_index=True,
                suffixes=("", "_b"), how="inner")
    if len(m) == 0:
        return out
    e = {c: (m[c] - m[c + "_b"]).to_numpy(dtype=float) for c in RET_COLS}
    for fi, row in pd.DataFrame(e).groupby(m["_fi"].to_numpy()).mean().iterrows():
        out[int(fi), :] = row.to_numpy(dtype=float)
    return out


def _d10_sets(df: pd.DataFrame, F: int) -> list[set]:
    g = df[df["_d10"] == 10].groupby("_fi")["ticker"].apply(set)
    return [set(g.get(i, set())) for i in range(F)]


def _new_ratio(sets: list[set], H: int) -> np.ndarray:
    nr = np.full(len(sets), np.nan)
    for i, s in enumerate(sets):
        if not s:
            continue
        if i - H < 0 or not sets[i - H]:
            nr[i] = 1.0
        else:
            nr[i] = len(set(s) - set(sets[i - H])) / len(s)
    return nr


def _spy_monthly() -> dict[int, float]:
    tr = load_etf_tr(["SPY"])
    idx = [str(d) for d in tr.index.tolist()]
    ym = pd.Series(idx).str[:6].to_numpy()
    v = np.nan_to_num(tr["SPY"].to_numpy(dtype=float), nan=0.0)
    g = pd.Series(v).groupby(ym).apply(lambda x: float(np.prod(1 + x.to_numpy()) - 1))
    return {int(k): float(vv) for k, vv in g.items()}


def _analyze(df: pd.DataFrame, yms: list[int], flag: str, *, size_col: str,
             w_col: str, costs: tuple, ref_cost: float,
             spy_m: dict | None) -> tuple[list[str], list[str], dict]:
    F = len(yms)
    n_avg = float(df.groupby("_fi").size().mean()) if len(df) else float("nan")
    L = [f"## {flag} (월평균 {n_avg:.1f}종목)", ""]
    keys = [f"[{flag}] n={n_avg:.1f}"]
    if F < 2 or len(df) == 0:
        L.append("표본 부족.")
        return L, keys, {}
    E, V, U = _decile_mats(df, w_col)
    bench = _size_bench(df)
    D10E = _excess_mat(df, bench, df["_d10"] == 10)
    sets = _d10_sets(df, F)

    L.append("### 1. MAD 10분위 월수익")
    L.append("")
    per_h: dict = {}
    for H in H_LIST:
        JE, JV, JU = _jt_cube(E, H), _jt_cube(V, H), _jt(U, H)
        XE = _jt(D10E, H)
        nr = _new_ratio(sets, H)
        per_h[H] = {"JE": JE, "JV": JV, "JU": JU, "XE": XE, "nr": nr}
        L.append(f"H={H} (수익월 {len(JU)}개월)")
        L.append("")
        L.append("| 분위 | EW | VW |")
        L.append("|---|---|---|")
        for d in range(10):
            me, _ = _nw(JE[:, d])
            mv, _ = _nw(JV[:, d])
            L.append(f"| D{d + 1} | {_p(me)} | {_p(mv)} |")
        se, te = _nw(JE[:, 9] - JE[:, 0])
        sv, tv_ = _nw(JV[:, 9] - JV[:, 0])
        ee, tet = _nw(JE[:, 9] - JU)
        ev, tvt = _nw(JV[:, 9] - JU)
        L.append(f"| D10-D1 | {_mt(se, te)} | {_mt(sv, tv_)} |")
        L.append(f"| D10-U | {_mt(ee, tet)} | {_mt(ev, tvt)} |")
        L.append("")
        if H == 1:
            keys.append(f"[{flag}] H=1 D10-D1 EW {_mt(se, te)} / D10-U {_mt(ee, tet)}")

    L.append("### 2. 사이즈 중립 초과 (D10, EW)")
    L.append("")
    L.append("| H | 초과 |")
    L.append("|---|---|")
    for H in H_LIST:
        me, te = _nw(per_h[H]["XE"])
        L.append(f"| {H} | {_mt(me, te)} |")
    L.append("")

    L.append("### 3. 롱온리 D10 비용 후 (사이즈중립 초과)")
    L.append("")
    L.append("| H | 비용 | 순초과 | 연회전율 |")
    L.append("|---|---|---|---|")
    for H in H_LIST:
        ph = per_h[H]
        to = float(np.nanmean(ph["nr"])) / H * 12 if H else float("nan")
        for c in costs:
            cj = np.array([c * ph["nr"][j - 1] / H for j in range(1, F)])
            net = ph["XE"] - cj
            me, te = _nw(net)
            L.append(f"| {H} | {c * 100:.2f}% | {_mt(me, te)} | {to:.2f} |")
            if H == 1 and abs(c - ref_cost) < 1e-12:
                keys.append(f"[{flag}] H=1 순초과({c * 100:.2f}%) {_mt(me, te)}")
    L.append("")
    if spy_m is not None:
        L.append("SPY 대비 D10 초과 (EW, 비용 전)")
        L.append("")
        L.append("| H | 초과 |")
        L.append("|---|---|")
        spy_j = np.array([spy_m.get(yms[j], np.nan) for j in range(1, F)])
        for H in H_LIST:
            me, te = _nw(per_h[H]["JE"][:, 9] - spy_j)
            L.append(f"| {H} | {_mt(me, te)} |")
        L.append("")

    L.append("### 4. 연도별 (H=1, 수익월 기준)")
    L.append("")
    L.append(f"| 연도 | n | D10-D1 (EW) | D10 순초과 ({ref_cost * 100:.2f}%) |")
    L.append("|---|---|---|---|")
    ph1 = per_h[1]
    cj1 = np.array([ref_cost * ph1["nr"][j - 1] for j in range(1, F)])
    net1 = ph1["XE"] - cj1
    sp1 = ph1["JE"][:, 9] - ph1["JE"][:, 0]
    yrs = np.array([y // 100 for y in yms[1:]])
    for yr in sorted(set(yrs.tolist())):
        m = yrs == yr
        L.append(f"| {yr} | {int(m.sum())} | {_p(np.nanmean(sp1[m]))} | "
                 f"{_p(np.nanmean(net1[m]))} |")
    L.append("")

    L.append("### 5. 비교 신호 (H=1, 유니버스 EW 대비 초과)")
    L.append("")
    L.append("| 신호 | 초과 |")
    L.append("|---|---|")
    u1 = np.full(F, np.nan)
    for fi, vv in df.groupby("_fi")["ret_1"].mean().items():
        u1[int(fi)] = float(vv)
    sigs = [("align", df["align"] == 1.0),
            ("above200", df["above200"] == 1.0),
            ("mds", df["mds"] == 1.0),
            ("hi52 상위", df["_h10"] == 10),
            ("mom 상위", df["_m10"] == 10),
            ("mad 상위", df["_d10"] == 10)]
    for name, mask in sigs:
        s = np.full(F, np.nan)
        for fi, vv in df[mask].groupby("_fi")["ret_1"].mean().items():
            s[int(fi)] = float(vv)
        ex = np.array([s[j - 1] - u1[j - 1] for j in range(1, F)])
        me, te = _nw(ex)
        L.append(f"| {name} | {_mt(me, te)} |")
    L.append("")

    L.append("### 6. 조합 이중정렬 (EW, 사이즈중립 초과, 독립 정렬)")
    L.append("")
    dall = df.dropna(subset=["_s5", "_m5"]).copy()
    dall["_s5"] = dall["_s5"].astype(int)
    dall["_m5"] = dall["_m5"].astype(int)
    mall = dall.merge(bench, left_on=["_fi", "_s5"], right_index=True,
                      suffixes=("", "_b"), how="inner")
    for c in RET_COLS:
        mall[c + "_e"] = mall[c] - mall[c + "_b"]
    ecols = [c + "_e" for c in RET_COLS]
    for rv, rname in (("_s3", "size"), ("_h3", "hi52"), ("_m3", "mom"),
                      ("_a01", "align")):
        sub = mall.dropna(subset=[rv]).copy()
        sub[rv] = sub[rv].astype(int)
        cell = sub.groupby(["_fi", rv, "_m5"])[ecols].mean()
        cnt = sub.groupby(["_fi", rv, "_m5"]).size()
        rows = sorted(int(v) for v in sub[rv].unique().tolist())
        for H in (1, 3):
            L.append(f"{rname} × MAD (H={H})")
            L.append("")
            L.append("| 행 | MAD1 | MAD2 | MAD3 | MAD4 | MAD5 | 5-1 |")
            L.append("|---|---|---|---|---|---|---|")
            for r_ in rows:
                jts: dict = {}
                cells = []
                for q in range(1, 6):
                    try:
                        s = cell.xs((r_, q), level=(1, 2))
                    except KeyError:
                        cells.append("nan")
                        continue
                    R = np.full((F, 6), np.nan)
                    for fi, row in s.iterrows():
                        R[int(fi), :] = row.to_numpy(dtype=float)
                    jt = _jt(R, H)
                    jts[q] = jt
                    me, te = _nw(jt)
                    try:
                        nn = float(cnt.xs((r_, q), level=(1, 2)).mean())
                    except KeyError:
                        nn = float("nan")
                    cells.append(f"{_p(me)} ({_t(te)}) [{nn:.1f}]")
                if 1 in jts and 5 in jts:
                    me, te = _nw(jts[5] - jts[1])
                    cells.append(_mt(me, te))
                else:
                    cells.append("nan")
                L.append(f"| {r_} | " + " | ".join(cells) + " |")
            L.append("")

    L.append("### 7. Fama-MacBeth (ret_1 OLS, 월별)")
    L.append("")
    need = ["ret_1", "mad", "hi52", "mom", size_col, "ret_1m", "align",
            "above200"]
    fm = df.dropna(subset=need)
    A, B = [], []
    for _, g in fm.groupby("_fi", sort=False):
        n = len(g)
        if n < 100:
            continue
        y = g["ret_1"].to_numpy(dtype=float)

        def _rn(c):
            r = g[c].rank(method="average").to_numpy(dtype=float)
            return (r - 1) / (n - 1)

        o = np.ones(n)
        Xa = np.column_stack([o, _rn("mad"), _rn(size_col)])
        Xb = np.column_stack([o, _rn("mad"), _rn("hi52"), _rn("mom"),
                              _rn(size_col), _rn("ret_1m"),
                              g["align"].to_numpy(dtype=float),
                              g["above200"].to_numpy(dtype=float)])
        ba, *_ = np.linalg.lstsq(Xa, y, rcond=None)
        bb, *_ = np.linalg.lstsq(Xb, y, rcond=None)
        A.append(ba)
        B.append(bb)
    A = np.array(A, dtype=float)
    B = np.array(B, dtype=float)
    L.append(f"월수: {len(A)}")
    L.append("")
    L.append("| 변수 | (a) 평균 | (a) t | (b) 평균 | (b) t |")
    L.append("|---|---|---|---|---|")
    names_b = ["const", "mad", "hi52", "mom", "size", "ret_1m", "align",
               "above200"]
    names_a = ["const", "mad", "size"]
    for j, name in enumerate(names_b):
        if name in names_a:
            i = names_a.index(name)
            ma, ta = _nw(A[:, i]) if len(A) else (np.nan, np.nan)
            ca = f"| {_p(ma)} | {_t(ta)} "
        else:
            ca = "| - | - "
        mb, tb = _nw(B[:, j]) if len(B) else (np.nan, np.nan)
        L.append(f"| {name} {ca}| {_p(mb)} | {_t(tb)} |")
    L.append("")
    return L, keys, per_h


def _build(panel: pd.DataFrame, mkt: str, *, size_col: str, w_col: str,
           costs: tuple, ref_cost: float, spy_m: dict | None,
           clips) -> tuple[str, list[str]]:
    yms_all = sorted(int(y) for y in panel["ym"].unique().tolist())
    n_u = n_l = float("nan")
    if len(panel):
        n_u = float(panel[panel["univ"]].groupby("ym").size().mean())
        n_l = float(panel[panel["liq"]].groupby("ym").size().mean())
    L = [f"# MAD 리서치 결과 ({mkt.upper()})", ""]
    if yms_all:
        L.append(f"- 형성월: {yms_all[0]} ~ {yms_all[-1]} ({len(yms_all)}개월)")
        if len(yms_all) > 1:
            L.append(f"- 수익월: {yms_all[1]} ~ {yms_all[-1]} "
                     f"({len(yms_all) - 1}개월)")
    L.append(f"- 월평균 종목수: univ {n_u:.1f}, liq {n_l:.1f}")
    L.append(f"- 클립 건수: {clips if clips is not None else '-'}")
    L.append("- 비용: " + ", ".join(f"{c * 100:.2f}%" for c in costs))
    L.append("")
    keys = [f"기간 형성 {yms_all[0] if yms_all else '-'}~"
            f"{yms_all[-1] if yms_all else '-'} / "
            f"univ {n_u:.1f} / liq {n_l:.1f}"]
    for flag in ("univ", "liq"):
        df, yms = _prep(panel, flag, size_col)
        sl, sk, _ = _analyze(df, yms, flag, size_col=size_col, w_col=w_col,
                             costs=costs, ref_cost=ref_cost, spy_m=spy_m)
        L.extend(sl)
        keys.extend(sk)
    return "\n".join(L) + "\n", keys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True, choices=["kr", "us"])
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    mkt = a.market
    cache = not a.rebuild
    if mkt == "kr":
        panel = P.kr_panel(cache=cache)
        size_col, w_col = "me", "me"
        costs, ref, spy_m, clips = tuple(KR_COSTS), 0.0035, None, None
    else:
        panel = P.us_panel(cache=cache)
        size_col, w_col = "tv20", "tv20"
        costs, ref = US_COSTS, 0.002
        spy_m = _spy_monthly()
        clips = panel.attrs.get("us_clips")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        md, keys = _build(panel, mkt, size_col=size_col, w_col=w_col,
                          costs=costs, ref_cost=ref, spy_m=spy_m, clips=clips)
    outp = P.PRIVATE_DIR / f"RESULTS_{mkt.upper()}.md"
    outp.write_text(md, encoding="utf-8")
    print(f"[mad] wrote {outp}", flush=True)
    for k in keys:
        print(f"[mad] {k}", flush=True)


if __name__ == "__main__":
    main()


