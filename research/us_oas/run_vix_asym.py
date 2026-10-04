"""VIX 비대칭 보정 비교 (D11) — stdout 마크다운 표 + RESULTS_VIX_ASYM.md 저장.

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_vix_asym.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest_daily import perf, portfolio
from research.backtest_daily.data_us import load_etf_tr, load_fred, load_index
from research.us_oas import proxy, rules
from research.us_oas import run_etf_proxy as _etf
from research.us_oas import run_tqqq_oas as _base

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = 0.001
OAS_SERIES = "BAMLH0A0HYM2"
RESULTS = Path(__file__).resolve().parent / "RESULTS_VIX_ASYM.md"

VARIANTS = {
    "BASE2": ("HYG", "IEI", "HYG_l1"),
    "SYM": ("HYG", "IEI", "HYG_l1", "VIX", "VIX_l1"),
    "ASYM_A": ("HYG", "IEI", "HYG_l1", "VIX_up", "VIX_dn", "VIX_up_l1", "VIX_dn_l1"),
    "ASYM_B": ("HYG", "IEI", "HYG_l1", "VIX_up", "VIX_up_l1"),
}
VAR_COLS = ["HYG", "IEI", "HYG_l1", "VIX", "VIX_l1",
            "VIX_up", "VIX_dn", "VIX_up_l1", "VIX_dn_l1"]


def _r2(y, pred) -> float:
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    denom = float(((y - y.mean()) ** 2).sum())
    if denom <= 0:
        return float("nan")
    return 1 - float(((y - pred) ** 2).sum()) / denom


def _count_episodes(w: pd.Series) -> int:
    """감축 에피소드 수 = TQQQ 비중 1 미만 연속 구간 수 (첫날부터 1 미만이면 1개)."""
    n = 0
    prev = False
    for x in w.to_numpy(dtype=float) < 1 - 1e-9:
        if bool(x) and not prev:
            n += 1
        prev = bool(x)
    return n


def _count_early_recoveries(w: pd.Series, window: int = 10) -> int:
    """이른 복귀 수 = 100% 복귀 뒤 window 거래일 안에 다시 1 미만으로 내려간 횟수."""
    below = w.to_numpy(dtype=float) < 1 - 1e-9
    n = 0
    for i in range(1, len(below)):
        if below[i - 1] and not below[i]:
            if bool(below[i + 1:i + 1 + window].any()):
                n += 1
    return n


def main() -> None:
    oas = load_fred(OAS_SERIES)
    px = load_etf_tr(["TQQQ", "QQQ", "HYG", "IEI"])
    vix = load_index("^VIX")
    lr = np.log1p(px[["HYG", "IEI"]])
    features = proxy.make_features(lr, vix)

    start, end = oas.index[0], oas.index[-1]
    coefs = {n: proxy.fit_fixed(oas, features, start, end, cols)
             for n, cols in VARIANTS.items()}

    # ---- 평가 2 기간·신호 (run_etf_proxy 와 동일) ----
    valid = px[["TQQQ", "QQQ", "HYG", "IEI"]].notna().all(axis=1)
    first = valid[valid].index[0]
    dates2 = [d for d in px.index if d >= first]
    rets2 = px.loc[dates2][["TQQQ", "QQQ"]]
    if bool(rets2.isna().any().any()):
        raise ValueError("NaN return in period")
    obs_set = set(oas.index)
    if start in dates2 and start in obs_set:
        anchor_date = start
        anchor_value = float(oas.loc[start])
    else:
        cands = [d for d in dates2 if d >= start and d in obs_set]
        if not cands:
            raise ValueError("no anchor trading day with OAS obs")
        anchor_date = cands[0]
        anchor_value = float(oas.loc[anchor_date])

    aligned2 = {}
    states2 = {}
    for n, cols in VARIANTS.items():
        a = proxy.etf_signal(features, coefs[n], anchor_date, anchor_value,
                             dates2, window=10, cols=cols)
        # oas=4.0 고정: ETF_DELTA 와 같은 방식(수준 조건 끔, 10일 변화 단계 A~D만)
        a["oas"] = 4.0
        st = rules.spec_states(a)
        st[a["d10_bp"].isna()] = "A"
        aligned2[n], states2[n] = a, st
    weights2 = {
        "QQQ": rules.to_weights(pd.Series(0.0, index=rets2.index)),
        "TQQQ": rules.to_weights(pd.Series(1.0, index=rets2.index)),
    }
    for n in VARIANTS:
        weights2[n] = rules.to_weights(states2[n].map(rules.SPEC_WEIGHTS))
    names2 = ["QQQ", "TQQQ", *VARIANTS]
    runs2 = {n: portfolio.run(weights2[n], rets2, COST) for n in names2}

    # ---- 평가 1 기간·신호 (run_nowcast 와 동일, 명세 규칙 전체) ----
    dates1 = [d for d in px.index if d > start]
    rets1 = px.loc[dates1][["TQQQ", "QQQ"]]
    if bool(rets1.isna().any().any()):
        raise ValueError("NaN return in period")
    nc1 = {n: proxy.nowcast(oas, features, dates1, cols) for n, cols in VARIANTS.items()}
    states1 = {n: rules.spec_states(nc1[n]) for n in VARIANTS}
    lag1 = rules.align_oas(oas, dates1)
    orc1 = proxy.oracle(oas, dates1)
    weights1 = {
        "QQQ": rules.to_weights(pd.Series(0.0, index=rets1.index)),
        "TQQQ": rules.to_weights(pd.Series(1.0, index=rets1.index)),
        "LAG1": rules.to_weights(rules.spec_states(lag1).map(rules.SPEC_WEIGHTS)),
    }
    for n in VARIANTS:
        weights1[n] = rules.to_weights(states1[n].map(rules.SPEC_WEIGHTS))
    weights1["ORACLE"] = rules.to_weights(rules.spec_states(orc1).map(rules.SPEC_WEIGHTS))
    names1 = ["QQQ", "TQQQ", "LAG1", *VARIANTS, "ORACLE"]
    runs1 = {n: portfolio.run(weights1[n], rets1, COST) for n in names1}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last1, last2 = dates1[-1], dates2[-1]
    vix_miss = len(dates2) - len(set(vix.index) & set(dates2))
    L: list[str] = [
        "# VIX 비대칭 보정 결과",
        "",
        f"- 생성: {now}",
        f"- 평가 1 기간: {dates1[0]}~{last1} (거래일 {len(dates1)}일)",
        f"- 평가 2 기간: {dates2[0]}~{last2} (거래일 {len(dates2)}일)",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, OAS {oas.index[-1]}, VIX {vix.index[-1]}",
        f"- 앵커: {anchor_date} OAS {anchor_value:.2f} (평가 2, 수준 조건 끔, Δ10만 사용)",
        f"- VIX 거래일 결측: {vix_miss}일 (NaN 포함 거래일은 nowcast 누적에서 제외·etf_signal NaN → 상태 A)",
        "- 계수 2023-10~2026-10 고정, 비용 0.1%, 세전, ORACLE 실매매 불가",
        "",
    ]

    obs_pos = {d: i for i, d in enumerate(oas.index)}
    ev = [d for d in dates2 if d in obs_set]
    L += ["## 1. 변형별 계수·표본내 적합",
          "| 변형 | b0 | " + " | ".join(VAR_COLS) + " | 학습 쌍 | 표본내 R² | d10 상관 |",
          "|" + "---|" * (len(VAR_COLS) + 5)]
    for n, cols in VARIANTS.items():
        pairs = proxy.daily_pairs(oas, features, cols)
        sel = pairs[(pairs.index >= start) & (pairs.index <= end)]
        pred = proxy.pred_change_bp(sel, coefs[n], cols).to_numpy(dtype=float)
        r2in = _r2(sel["d_bp"].to_numpy(dtype=float), pred)
        ed_list: list[float] = []
        ad_list: list[float] = []
        for d in ev:
            j = obs_pos[d]
            if j < 10:
                continue
            ed = aligned2[n].loc[d, "d10_bp"]
            if pd.isna(ed):
                continue
            ad_list.append((float(oas.iloc[j]) - float(oas.iloc[j - 10])) * 100)
            ed_list.append(float(ed))
        corr = float(np.corrcoef(ed_list, ad_list)[0, 1]) if len(ed_list) >= 2 else float("nan")
        cells = [_base._num(coefs[n][0], 4)]
        for c in VAR_COLS:
            cells.append(_base._num(coefs[n][cols.index(c) + 1], 4) if c in cols else "-")
        L.append(f"| {n} | {' | '.join(cells)} | {len(sel)}"
                 f" | {_base._num(r2in, 3)} | {_base._num(corr, 3)} |")
    L.append("")

    L += ["## 2. 하루 단위 표본외 추정 품질 (nowcast, 확장창)",
          "상승일 = 실제 OAS − base > 0, 하락일 = < 0 (차이 0인 날 제외).",
          "| 변형 | 평가일 | 평균절대 (bp) | 최대 (bp) | 90분위 (bp)"
          " | 하루변화 표본외 R² | 상승일 MAE (bp) | 하락일 MAE (bp) |",
          "|---|---|---|---|---|---|---|---|"]
    updn = {}
    for n in VARIANTS:
        nc = nc1[n]
        evq = [d for d in dates1 if bool(nc.loc[d, "fitted"]) and d in obs_set]
        if evq:
            est = nc.loc[evq, "oas"].to_numpy(dtype=float)
            act = oas.loc[evq].to_numpy(dtype=float)
            base_v = nc.loc[evq, "base"].to_numpy(dtype=float)
            err = np.abs(est - act) * 100
            mae, mx, p90 = float(err.mean()), float(err.max()), float(np.percentile(err, 90))
            r2o = _r2(act - base_v, est - base_v)
            chg = act - base_v
            up, dn = err[chg > 0], err[chg < 0]
            mae_up = float(up.mean()) if len(up) else float("nan")
            mae_dn = float(dn.mean()) if len(dn) else float("nan")
            updn[n] = (len(up), len(dn))
        else:
            mae = mx = p90 = r2o = mae_up = mae_dn = float("nan")
            updn[n] = (0, 0)
        L.append(f"| {n} | {len(evq)} | {_base._num(mae, 1)} | {_base._num(mx, 1)}"
                 f" | {_base._num(p90, 1)} | {_base._num(r2o, 3)}"
                 f" | {_base._num(mae_up, 1)} | {_base._num(mae_dn, 1)} |")
    L.append("상승/하락일 수: " + ", ".join(f"{n} {u}/{d}" for n, (u, d) in updn.items()))
    L.append("")

    L += ["## 3. 평가 1 요약 (3년 실매매 방식)",
          *_base._summary_table(names1, runs1, weights1),
          ""]

    L.append("## 4. 평가 1 스트레스 구간")
    for label, s, e in _base.STRESS:
        e2 = e or last1
        win = [d for d in dates1 if s <= d <= e2]
        L += [f"### {label} ({s}~{e2})",
              "| 전략 | 첫 감축일 | 최저 TQQQ | 복귀일 | 구간 수익 | 구간 MDD |",
              "|---|---|---|---|---|---|"]
        if not win:
            L.append("| 구간 내 거래일 없음 | - | - | - | - | - |")
            continue
        for n in VARIANTS:
            w = weights1[n]["TQQQ"].loc[win]
            cuts = [d for d in win if w.loc[d] < 1 - 1e-9]
            first_cut = cuts[0] if cuts else "-"
            rec = "-"
            if cuts:
                for d in win:
                    if d > cuts[0] and w.loc[d] > 1 - 1e-9:
                        rec = d
                        break
            rslice = runs1[n]["ret"].loc[win]
            wr = float((1 + rslice).prod() - 1)
            L.append(f"| {n} | {first_cut} | {_base._pct(float(w.min()))} | {rec}"
                     f" | {_base._pct(wr)} | {_base._pct(perf.max_drawdown(rslice))} |")
        for n in ["TQQQ", "QQQ"]:
            rslice = runs1[n]["ret"].loc[win]
            wr = float((1 + rslice).prod() - 1)
            L.append(f"| {n} | - | - | - | {_base._pct(wr)} | {_base._pct(perf.max_drawdown(rslice))} |")
    L.append("")

    L += ["## 5. 평가 2 요약 (16년 ETF 근사)",
          *_base._summary_table(names2, runs2, weights2),
          ""]

    L.append("## 6. 평가 2 스트레스 구간")
    for label, s, e in _etf.STRESS:
        e2 = e or last2
        win = [d for d in dates2 if s <= d <= e2]
        L += [f"### {label} ({s}~{e2})",
              "| 전략 | 첫 감축일 | 최저 TQQQ | 복귀일 | 구간 수익 | 구간 MDD |",
              "|---|---|---|---|---|---|"]
        if not win:
            L.append("| 구간 내 거래일 없음 | - | - | - | - | - |")
            continue
        for n in VARIANTS:
            w = weights2[n]["TQQQ"].loc[win]
            cuts = [d for d in win if w.loc[d] < 1 - 1e-9]
            first_cut = cuts[0] if cuts else "-"
            rec = "-"
            if cuts:
                for d in win:
                    if d > cuts[0] and w.loc[d] > 1 - 1e-9:
                        rec = d
                        break
            rslice = runs2[n]["ret"].loc[win]
            wr = float((1 + rslice).prod() - 1)
            L.append(f"| {n} | {first_cut} | {_base._pct(float(w.min()))} | {rec}"
                     f" | {_base._pct(wr)} | {_base._pct(perf.max_drawdown(rslice))} |")
        for n in ["TQQQ", "QQQ"]:
            rslice = runs2[n]["ret"].loc[win]
            wr = float((1 + rslice).prod() - 1)
            L.append(f"| {n} | - | - | - | {_base._pct(wr)} | {_base._pct(perf.max_drawdown(rslice))} |")
    L.append("")

    pos2 = {d: i for i, d in enumerate(dates2)}
    first_cuts = {}
    for n in VARIANTS:
        w = weights2[n]["TQQQ"]
        for si, (_label, s, e) in enumerate(_etf.STRESS):
            e2 = e or last2
            win = [d for d in dates2 if s <= d <= e2]
            cuts = [d for d in win if w.loc[d] < 1 - 1e-9]
            first_cuts[(n, si)] = cuts[0] if cuts else None
    L += ["## 7. 이른 복귀(오판) 지표",
          "감축 에피소드 = TQQQ 비중 1 미만 연속 구간 수."
          " 이른 복귀 = 100% 복귀 뒤 10거래일 안에 다시 1 미만으로 내려간 횟수.",
          "",
          "### 평가 1",
          "| 변형 | 감축 에피소드 수 | 이른 복귀 수 |",
          "|---|---|---|"]
    for n in VARIANTS:
        w = weights1[n]["TQQQ"]
        L.append(f"| {n} | {_count_episodes(w)} | {_count_early_recoveries(w)} |")
    L += ["",
          "### 평가 2",
          "| 변형 | 감축 에피소드 수 | 이른 복귀 수 | 첫 감축일 평균 앞당김 (거래일, BASE2 대비) |",
          "|---|---|---|---|"]
    for n in VARIANTS:
        w = weights2[n]["TQQQ"]
        diffs = []
        for si in range(len(_etf.STRESS)):
            b, v = first_cuts[("BASE2", si)], first_cuts[(n, si)]
            if b is not None and v is not None:
                diffs.append(pos2[v] - pos2[b])
        avg = float(sum(diffs) / len(diffs)) if diffs else float("nan")
        L.append(f"| {n} | {_count_episodes(w)} | {_count_early_recoveries(w)}"
                 f" | {_base._num(avg, 1)} (n={len(diffs)}/{len(_etf.STRESS)}) |")
    L.append("음수 = BASE2보다 빠름. 양쪽 모두 첫 감축일이 있는 구간만 평균.")
    L.append("")

    L += ["## 8. 평가 1 상태 일수",
          "| 상태 | " + " | ".join(VARIANTS) + " |",
          "|" + "---|" * (len(VARIANTS) + 1)]
    for g in ["A", "B", "C", "D", "E"]:
        L.append("| " + g + " | "
                 + " | ".join(str(int((states1[n] == g).sum())) for n in VARIANTS) + " |")
    L.append("")

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
