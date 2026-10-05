"""제3자배정 유상 1단계 분석 (SPEC: research/rights_issue/SPEC.md T1~T8).

실행: cd etl && PYTHONPATH=.. uv run python ../research/rights_issue/analyze_third.py [--scope all]

--scope all: 유상 전체·사유별 (SPEC P1~P7). 출력 paid_*.csv. 기본 동작·third_*.csv 는 그대로.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
import analyze as A
import fetch_kind as FK

from research.backtest_daily import data, guards, stats, universe

CACHE = ROOT / "cache"
OUT = ROOT / "out"
CLS4 = ["장전", "장중", "마감직전", "장후"]
CLS5 = CLS4 + ["시각미상"]
G3 = ["≤0", "0~20%", "20%초과"]
G2 = {"공개전종가가능": ["장전", "장중"], "장후": ["장후", "마감직전"]}
APPLIED = ("적용값: 기간=2021-01~2026-09(T1) · 필터=제목 제3자배정 포함·"
           "[정정]/종속회사/자회사/철회 제외(T2) · 발표일·시각=검색행 date·time, "
           "(회사,날짜)중복→이른시각 1건(T3) · 코드=viewer stock_code(T4) · "
           "진입=ref D0종가→D+1시가/종가(전부), post 장전·장중 D0종가→D+1시가/종가·"
           "장후·마감직전 D+1시가→D+2시가/D+1종가(T5) · "
           "그룹=pre20 ≤0/0~20%/20%초과·연도·유동성 전체/통과(T6) · "
           "가드=halt_ok+SPAC제외·liq플래그·limit_up, 비용 stats.COSTS(기준 0.35%)·일가중(T7) · "
           "2단계=inv_type 규칙분류(T9)·dilution 파싱성공분 3분위+범위·discount 고정구간(-10%/할인<10%/0·할증, 상한 몰림으로 3분위 대체)(T10)·mgmt_flag(T11)")
APPLIED_ALL = ("적용값(scope=all): 범위=유상 전체·사유별(P1) · "
               "사유=자금조달 6항목 금액(P2)·비중 50%·없으면 혼합(P3) · "
               "제외=제목 출자전환·현물출자·채무상계·자금합계 0(P4) · "
               "기간=2021-01~2026-09(P5) · 방식=본문 5.증자방식 제3자/주주/일반공모(P6) · "
               "출력=paid_*.csv(P7) · 틀·가드·비용=T5~T7 동일")
PAID_EX = ("출자전환", "현물출자", "채무상계")


def is_scope_all(argv):
    i = argv.index("--scope") if "--scope" in argv else -1
    return i >= 0 and i + 1 < len(argv) and argv[i + 1] == "all"


def passed(rows, scope_all=False):
    """제목 필터 통과 행만. scope all: 유상증자 + 현금없는 건(출자전환·현물출자·채무상계) 제외."""
    if not scope_all:
        return [r for r in rows if FK.match_search_title(r.get("title") or "", "제3자배정")]
    out = []
    for r in rows:
        t = r.get("title") or ""
        if not FK.match_search_title(t, "유상증자"):
            continue
        if any(w in t for w in PAID_EX):
            continue
        out.append(r)
    return out


def dedup(rows):
    """(회사, 날짜) 중복 → time 가장 이른 1건."""
    best = {}
    for r in rows:
        k = (r.get("company"), r.get("date"))
        t = r.get("time") or "99:99"
        if k not in best or t < (best[k].get("time") or "99:99"):
            best[k] = r
    return [best[k] for k in sorted(best, key=lambda k: (str(k[0]), str(k[1])))]


def load_paid():
    """cache/search_paid_2021~2026.json 행 전부."""
    rows = []
    for y in range(2021, 2027):
        p = CACHE / f"search_paid_{y}.json"
        if not p.exists():
            continue
        obj = json.loads(p.read_text(encoding="utf-8"))
        rows.extend(obj.get("rows", []) if isinstance(obj, dict) else obj)
    return rows


def load_viewer(ac):
    p = CACHE / f"viewer_{ac}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def summ(sdf, col):
    """단일 컬럼 집계. n, n_days, 비용 3단계 mean·t, med·win, daymed·daywin."""
    n = len(sdf)
    row = {"n": n, "n_days": int(sdf["date"].nunique()) if n else 0}
    if n == 0:
        for c in stats.COSTS:
            suf = f"{c * 100:g}"
            row[f"mean_{suf}"] = float("nan")
            row[f"t_{suf}"] = float("nan")
        row["med"] = float("nan")
        row["win"] = float("nan")
        row["daymed"] = float("nan")
        row["daywin"] = float("nan")
        return row
    ct = stats.cost_table(sdf[col].to_numpy(float), stats.day_key(sdf["date"]))
    for c in stats.COSTS:
        suf = f"{c * 100:g}"
        cc = ct["costs"][str(c)]
        row[f"mean_{suf}"] = cc["mean"]
        row[f"t_{suf}"] = cc["t"]
    row["med"] = ct["med"]
    row["win"] = ct["win"]
    net = sdf.assign(_net=sdf[col] - 0.0035)
    row["daymed"] = stats.day_median(net, "_net")
    row["daywin"] = stats.day_win_rate(net, "_net")
    return row


def tertile_labels(s):
    """3분위 라벨 + 분위별 (min, max). 값이 부족하면 빈 매핑."""
    vals = s.dropna()
    if len(vals) < 3 or vals.nunique() < 2:
        return pd.Series(index=s.index, dtype=object), {}
    _, bins = pd.qcut(vals, q=3, retbins=True, duplicates="drop")
    nb = len(bins) - 1
    if nb < 1:
        return pd.Series(index=s.index, dtype=object), {}
    labels = [f"Q{i + 1}" for i in range(nb)]
    lab = pd.cut(s, bins=bins, labels=labels, include_lowest=True)
    rng = {labels[i]: (float(bins[i]), float(bins[i + 1])) for i in range(nb)}
    return lab, rng


DIS_BANDS = ["할인10%", "할인<10%", "0·할증"]
DIS_DEF = {"할인10%": "-10.05≤d≤-9.95", "할인<10%": "-9.95<d<-0.05",
           "0·할증": "d≥-0.05"}


def discount_band(d):
    """discount 고정 구간. d<-10.05·NaN → None(축에서 제외)."""
    if pd.isna(d):
        return None
    if -10.05 <= d <= -9.95:
        return "할인10%"
    if -9.95 < d < -0.05:
        return "할인<10%"
    if d >= -0.05:
        return "0·할증"
    return None


def stage2_all(Tr):
    """유상 전체 2단계: purpose·method·method×purpose 축별 post 수익 + ref 갭."""
    pp = OUT / "paid_parsed.csv"
    if not pp.exists():
        print("[2단계] paid_parsed.csv 없음 — 건너뜀")
        return
    Pz = pd.read_csv(pp, dtype={"acptno": str})
    ok = Pz[Pz["parse_ok"].astype(str).str.lower().isin(["true", "1"])].copy()
    if len(ok) == 0:
        print("[2단계] 파싱 성공 0건 — 건너뜀")
        return
    ft = pd.to_numeric(ok["fund_total"], errors="coerce")
    p4ex = (ft == 0) | (ft.isna())
    n_p4 = int(p4ex.sum())
    ok = ok[~p4ex]
    if len(ok) == 0:
        print(f"[2단계] P4 제외={n_p4}건 후 0건 — 건너뜀")
        return
    ok["acptno"] = ok["acptno"].astype(str)
    keep = ok.set_index("acptno")
    T = Tr.copy()
    T["acptno"] = T["acptno"].astype(str)
    T = T[T["acptno"].isin(keep.index)].copy()
    if len(T) == 0:
        print("[2단계] 조인 0건 — 건너뜀")
        return
    T["purpose"] = T["acptno"].map(keep["purpose"])
    T["method"] = T["acptno"].map(keep["method"])
    T["mp"] = T["method"].astype(str) + "×" + T["purpose"].astype(str)
    R = T[T["kind"] == "ref"]
    P = T[T["kind"] == "post"]
    print(f"[2단계] 파싱성공 이벤트={len(ok)} 조인거래={len(T)} "
          f"(ref={len(R)} post={len(P)}) P4 제외={n_p4}건(fund_total 0/NaN)")

    def pct(v):
        return "-" if pd.isna(v) else f"{float(v) * 100:.1f}%"

    def t2(v):
        return "-" if pd.isna(v) else f"{float(v):.2f}"

    axes = [("purpose", "purpose"), ("method", "method"),
            ("method×purpose", "mp")]
    print("[표6 2단계] 축 × 2그룹: post op + ref 갭 (평균·t = 비용 0.35% 차감)")
    print(f"{'축':<14}{'그룹':<12}{'level':<24}  n_post post_m35  post_t post_med  n_ref  ref_m35")
    rows = []
    for axis, col in axes:
        for g, members in G2.items():
            gR = R[R["cls"].isin(members)]
            gP = P[P["cls"].isin(members)]
            for lv in T[col].value_counts().index.tolist():
                p = gP[gP[col].astype(str) == str(lv)]
                r = gR[gR[col].astype(str) == str(lv)]
                po = summ(p, "op")
                ro = summ(r, "op")
                print(f"{axis:<14}{g:<12}{str(lv):<24}{po['n']:>7} "
                      f"{pct(po['mean_0.35']):>7} {t2(po['t_0.35']):>7} "
                      f"{pct(po['med']):>7} {ro['n']:>7} {pct(ro['mean_0.35']):>7}")
                rows.append({"axis": axis, "level": str(lv), "range": "",
                             "grp2": g, "n_post": po["n"],
                             "post_op_mean": po["mean_0.35"],
                             "post_op_t": po["t_0.35"], "post_op_med": po["med"],
                             "n_ref": ro["n"], "ref_op_mean": ro["mean_0.35"]})
    pd.DataFrame(rows, columns=["axis", "level", "range", "grp2", "n_post",
                                "post_op_mean", "post_op_t", "post_op_med",
                                "n_ref", "ref_op_mean"]).to_csv(
        OUT / "paid_stage2.csv", index=False, encoding="utf-8")


def stage2(Tr, scope_all=False):
    """2단계 집계: inv_type·분위·mgmt 축별 post 수익 + ref 갭."""
    if scope_all:
        stage2_all(Tr)
        return
    pp = OUT / "third_parsed.csv"
    if not pp.exists():
        print("[2단계] third_parsed.csv 없음 — 건너뜀")
        return
    Pz = pd.read_csv(pp, dtype={"acptno": str})
    ok = Pz[Pz["parse_ok"].astype(str).str.lower().isin(["true", "1"])].copy()
    if len(ok) == 0:
        print("[2단계] 파싱 성공 0건 — 건너뜀")
        return
    ok["acptno"] = ok["acptno"].astype(str)
    dil_lab, dil_rng = tertile_labels(ok["dilution"])
    ok["dil_q"] = dil_lab.astype(str)
    ok["dis_q"] = ok["discount_pct"].map(discount_band)
    n_dis_ex = int(ok["dis_q"].isna().sum())
    keep = ok.set_index("acptno")
    T = Tr.copy()
    T["acptno"] = T["acptno"].astype(str)
    T = T[T["acptno"].isin(keep.index)].copy()
    if len(T) == 0:
        print("[2단계] 조인 0건 — 건너뜀")
        return
    T["inv_type"] = T["acptno"].map(keep["inv_type"])
    T["mgmt_flag"] = T["acptno"].map(keep["mgmt_flag"].astype(str))
    T["dil_q"] = T["acptno"].map(keep["dil_q"])
    T["dis_q"] = T["acptno"].map(keep["dis_q"])
    R = T[T["kind"] == "ref"]
    P = T[T["kind"] == "post"]
    print(f"[2단계] 파싱성공 이벤트={len(ok)} 조인거래={len(T)} "
          f"(ref={len(R)} post={len(P)})")
    print("  dilution: " + (", ".join(f"{k}={lo:g}~{hi:g}"
                                      for k, (lo, hi) in sorted(dil_rng.items()))
                            if dil_rng else "분위 불가"))
    dis_n = ok["dis_q"].value_counts()
    print("  discount_pct: " + ", ".join(
        f"{b}({DIS_DEF[b]})={int(dis_n.get(b, 0))}건" for b in DIS_BANDS))
    print(f"  discount 제외={n_dis_ex}건 (d<-10.05·NaN)")

    def pct(v):
        return "-" if pd.isna(v) else f"{float(v) * 100:.1f}%"

    def t2(v):
        return "-" if pd.isna(v) else f"{float(v):.2f}"

    axes = [("inv_type", "inv_type", {}), ("discount", "dis_q", {}),
            ("dilution", "dil_q", dil_rng), ("mgmt_flag", "mgmt_flag", {})]
    print("[표6 2단계] 축 × 2그룹: post op + ref 갭 (평균·t = 비용 0.35% 차감)")
    print(f"{'축':<10}{'그룹':<12}{'level':<28}  n_post post_m35  post_t post_med  n_ref  ref_m35")
    rows = []
    for axis, col, rng in axes:
        if axis == "discount":
            levels = DIS_BANDS
        else:
            levels = sorted(rng) if rng else T[col].value_counts().index.tolist()
        for g, members in G2.items():
            gR = R[R["cls"].isin(members)]
            gP = P[P["cls"].isin(members)]
            for lv in levels:
                p = gP[gP[col].astype(str) == str(lv)]
                r = gR[gR[col].astype(str) == str(lv)]
                po = summ(p, "op")
                ro = summ(r, "op")
                if axis == "discount":
                    disp, rg = f"{lv} ({DIS_DEF[lv]})", DIS_DEF[lv]
                else:
                    lo, hi = rng.get(lv, (None, None)) if rng else (None, None)
                    disp = f"{lv} ({lo:g}~{hi:g})" if lo is not None else str(lv)
                    rg = f"{lo:g}~{hi:g}" if lo is not None else ""
                print(f"{axis:<10}{g:<12}{disp:<28}{po['n']:>7} "
                      f"{pct(po['mean_0.35']):>7} {t2(po['t_0.35']):>7} "
                      f"{pct(po['med']):>7} {ro['n']:>7} {pct(ro['mean_0.35']):>7}")
                rows.append({"axis": axis, "level": str(lv),
                             "range": rg,
                             "grp2": g, "n_post": po["n"],
                             "post_op_mean": po["mean_0.35"],
                             "post_op_t": po["t_0.35"], "post_op_med": po["med"],
                             "n_ref": ro["n"], "ref_op_mean": ro["mean_0.35"]})
    pd.DataFrame(rows, columns=["axis", "level", "range", "grp2", "n_post",
                                "post_op_mean", "post_op_t", "post_op_med",
                                "n_ref", "ref_op_mean"]).to_csv(
        OUT / "third_stage2.csv", index=False, encoding="utf-8")


def main(scope_all=False):
    prefix = "paid_" if scope_all else "third_"
    print(APPLIED_ALL if scope_all else APPLIED)
    cand = load_paid()
    filt = passed(cand, scope_all=scope_all)
    ded = dedup(filt)
    skip = {}

    def drop(reason):
        skip[reason] = skip.get(reason, 0) + 1

    evs = []
    for r in ded:
        ac = r.get("acptno")
        ann = A.norm_date(r.get("date"))
        if not ann:
            drop("발표일 결측")
            continue
        v = load_viewer(ac) if ac else None
        if v is None:
            drop("viewer 없음")
            continue
        code = v.get("stock_code")
        if not code:
            drop("stock_code 없음")
            continue
        evs.append((ac, r.get("company") or "", r.get("title") or "",
                    code, ann, r.get("time")))

    px = data.load_px()
    mdates = data.market_dates()
    names = data.stock_names()
    halt = guards.halt_ok(px, mdates)
    tk = px["ticker"].to_numpy()

    erecs, trecs = [], []
    refskip, postskip = {}, {}
    for ac, company, title, code, ann, tm in sorted(evs):
        d0 = A.locate(mdates, ann, ann)[0]
        if d0 is None:
            drop("D0 없음(범위 밖)")
            continue
        lo = int(np.searchsorted(tk, code, side="left"))
        hi = int(np.searchsorted(tk, code, side="right"))
        if lo == hi:
            drop("D0 행 없음")
            continue
        sub = px.iloc[lo:hi]
        di = np.where(sub["ms"].to_numpy() == d0)[0]
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
        df = A.add_ref(sub, set())
        dpos = int(np.where(df["ms"].to_numpy() == d0)[0][0])
        liq_ok = guards.liquidity(df, dpos) is None
        pre, grp = A.pre20_and_group(df, d0)
        cls = A.time_class(ann, mdates[d0], tm)
        dd = mdates[d0]
        erecs.append({"acptno": ac, "company": company, "code": code, "name": name,
                      "발표일": ann, "공시시각": tm or "", "시각구분": cls,
                      "D0": dd, "pre20": pre, "group": grp,
                      "liq_ok": liq_ok, "title": title})
        x1 = d0 + 10_000
        ref = A.d1_trade(df, d0, x1)
        if isinstance(ref, str):
            refskip[ref] = refskip.get(ref, 0) + 1
        else:
            trecs.append({"acptno": ac, "kind": "ref", "cls": cls, "entry": "D0종가",
                          "date": dd, "year": dd[:4], "group": grp,
                          "liq_ok": liq_ok, **ref})
        post = A.post_trade(df, d0, x1, cls)
        if isinstance(post, str):
            postskip[post] = postskip.get(post, 0) + 1
        else:
            trecs.append({"acptno": ac, "kind": "post", "cls": cls,
                          "entry": "D0종가" if cls in ("장전", "장중") else "D+1시가",
                          "date": dd, "year": dd[:4], "group": grp,
                          "liq_ok": liq_ok, **post})

    OUT.mkdir(parents=True, exist_ok=True)
    E = pd.DataFrame(erecs, columns=["acptno", "company", "code", "name", "발표일",
                                     "공시시각", "시각구분", "D0", "pre20", "group",
                                     "liq_ok", "title"])
    Tr = pd.DataFrame(trecs, columns=["acptno", "kind", "cls", "entry", "date",
                                      "year", "group", "liq_ok", "op", "cl", "hi"])
    E.to_csv(OUT / f"{prefix}events.csv", index=False, encoding="utf-8")
    Tr.to_csv(OUT / f"{prefix}trades.csv", index=False, encoding="utf-8")

    R = Tr[Tr["kind"] == "ref"]
    P = Tr[Tr["kind"] == "post"]
    print(f"후보 행={len(cand)} → 필터 통과={len(filt)} → 중복제거={len(ded)} "
          f"→ 포함={len(E)}")
    print("제외: " + (", ".join(f"{k}={v}" for k, v in skip.items()) if skip else "없음"))
    print("ref 제외: " + (", ".join(f"{k}={v}" for k, v in refskip.items())
                          if refskip else "없음"))
    print("post 제외: " + (", ".join(f"{k}={v}" for k, v in postskip.items())
                           if postskip else "없음"))
    print("[시각구분]")
    print("".join(c.rjust(8) for c in CLS5))
    print("".join(f"{int((E['시각구분'] == c).sum()):>8}" for c in CLS5))

    def pct(v):
        return "-" if pd.isna(v) else f"{float(v) * 100:.1f}%"

    def t2(v):
        return "-" if pd.isna(v) else f"{float(v):.2f}"

    def tline(sdf):
        op, cl = summ(sdf, "op"), summ(sdf, "cl")
        return (f"{op['n']:>5} {pct(op['mean_0.35']):>7} "
                f"{t2(op['t_0.35']):>7} {pct(op['med']):>7} "
                f"{pct(cl['mean_0.35']):>7}")

    head = "    n  op_m35    op_t  op_med  cl_m35"
    print("[표1 ref] 시각구분별 (liq=전체)")
    print("시각구분" + head)
    for c in CLS5:
        print(f"{c:<8}" + tline(R[R["cls"] == c]))
    print("[표2 post] cls별 (liq=전체)")
    print("cls     " + head)
    for c in CLS4:
        print(f"{c:<8}" + tline(P[P["cls"] == c]))
    print("[표3 post] 2그룹 × group (liq=전체)")
    print("그룹        group   " + head)
    for g, members in G2.items():
        gdf = P[P["cls"].isin(members)]
        print(f"{g:<12}{'전체':<8}" + tline(gdf))
        for grp in G3:
            print(f"{g:<12}{grp:<8}" + tline(gdf[gdf["group"] == grp]))
    print("[표4 post] 2그룹 × 연도 (liq=전체)")
    print("year  그룹      " + head)
    yrs = sorted(P["year"].dropna().unique()) if len(P) else []
    if not yrs:
        print("(없음)")
    for yr in yrs:
        ydf = P[P["year"] == yr]
        for g, members in G2.items():
            print(f"{yr:<6}{g:<10}" + tline(ydf[ydf["cls"].isin(members)]))
    print("[표5 post] 2그룹 × liq")
    print("그룹        liq   " + head)
    for g, members in G2.items():
        gdf = P[P["cls"].isin(members)]
        print(f"{g:<12}{'전체':<6}" + tline(gdf))
        print(f"{g:<12}{'통과':<6}" + tline(gdf[gdf["liq_ok"] == True]))
    stage2(Tr, scope_all=scope_all)


def selfcheck():
    rows = [
        {"acptno": "1", "date": "2024-01-02", "time": "09:10",
         "company": "A", "title": "유상증자결정(제3자배정)"},
        {"acptno": "2", "date": "2024-01-02", "time": "08:00",
         "company": "B", "title": "[정정]유상증자결정(제3자배정)"},
        {"acptno": "3", "date": "2024-01-03", "time": "10:00",
         "company": "C", "title": "유상증자결정(제3자배정 철회)"},
        {"acptno": "4", "date": "2024-01-02", "time": "15:00",
         "company": "A", "title": "유상증자결정(제3자배정)"},
    ]
    f = passed(rows)
    assert [r["acptno"] for r in f] == ["1", "4"], f
    d = dedup(f)
    assert len(d) == 1 and d[0]["acptno"] == "1" and d[0]["time"] == "09:10", d
    e = pd.DataFrame([], columns=["date", "op", "cl"])
    s = summ(e, "op")
    assert s["n"] == 0 and s["n_days"] == 0, s
    for k in ("mean_0.35", "t_0.35", "med", "win", "daymed", "daywin"):
        assert pd.isna(s[k]), (k, s[k])
    q = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9], dtype=float)
    lab, rng = tertile_labels(q)
    assert sorted(rng) == ["Q1", "Q2", "Q3"], rng
    assert lab.tolist()[:3] == ["Q1", "Q1", "Q1"], lab.tolist()
    assert discount_band(-10) == "할인10%"
    assert discount_band(-5) == "할인<10%"
    assert discount_band(0) == "0·할증"
    assert discount_band(30) == "0·할증"
    assert discount_band(-20) is None
    assert discount_band(float("nan")) is None
    pa = [
        {"title": "유상증자결정(주주배정후 실권주 일반공모)"},
        {"title": "유상증자결정"},
        {"title": "유상증자결정(제3자배정-출자전환)"},
        {"title": "유상증자결정(제3자배정-현물출자(채무상계))"},
    ]
    fa = passed(pa, scope_all=True)
    assert [r["title"] for r in fa] == [
        "유상증자결정(주주배정후 실권주 일반공모)", "유상증자결정"], fa


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--selfcheck" in argv:
        selfcheck()
        print("selfcheck passed")
    else:
        main(scope_all=is_scope_all(argv))
