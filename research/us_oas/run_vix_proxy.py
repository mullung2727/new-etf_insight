"""VIX 보정 ETF 신호 비교 (D10) — stdout 마크다운 표 + RESULTS_VIX_PROXY.md 저장.

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_vix_proxy.py
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
RESULTS = Path(__file__).resolve().parent / "RESULTS_VIX_PROXY.md"

VARIANTS = {
    "BASE": ("HYG", "IEI"),
    "VIX": ("HYG", "IEI", "VIX", "VIX_l1"),
    "HYG_L1": ("HYG", "IEI", "HYG_l1"),
    "ALL": ("HYG", "IEI", "VIX", "VIX_l1", "HYG_l1"),
}
VAR_COLS = ["HYG", "IEI", "VIX", "VIX_l1", "HYG_l1"]


def _r2(y, pred) -> float:
    y = np.asarray(y, dtype=float)
    pred = np.asarray(pred, dtype=float)
    denom = float(((y - y.mean()) ** 2).sum())
    if denom <= 0:
        return float("nan")
    return 1 - float(((y - pred) ** 2).sum()) / denom


def main() -> None:
    oas = load_fred(OAS_SERIES)
    px = load_etf_tr(["TQQQ", "QQQ", "HYG", "IEI"])
    vix = load_index("^VIX")
    lr = np.log1p(px[["HYG", "IEI"]])
    features = proxy.make_features(lr, vix)

    start, end = oas.index[0], oas.index[-1]
    coefs = {n: proxy.fit_fixed(oas, features, start, end, cols)
             for n, cols in VARIANTS.items()}

    valid = px[["TQQQ", "QQQ", "HYG", "IEI"]].notna().all(axis=1)
    first = valid[valid].index[0]
    dates = [d for d in px.index if d >= first]
    rets = px.loc[dates][["TQQQ", "QQQ"]]
    if bool(rets.isna().any().any()):
        raise ValueError("NaN return in period")

    obs_set = set(oas.index)
    if start in dates and start in obs_set:
        anchor_date = start
        anchor_value = float(oas.loc[start])
    else:
        cands = [d for d in dates if d >= start and d in obs_set]
        if not cands:
            raise ValueError("no anchor trading day with OAS obs")
        anchor_date = cands[0]
        anchor_value = float(oas.loc[anchor_date])

    aligned = {}
    states = {}
    for n, cols in VARIANTS.items():
        a = proxy.etf_signal(features, coefs[n], anchor_date, anchor_value,
                             dates, window=10, cols=cols)
        # oas=4.0 고정: ETF_DELTA 와 같은 방식(수준 조건 끔, 10일 변화 단계 A~D만)
        a["oas"] = 4.0
        st = rules.spec_states(a)
        st[a["d10_bp"].isna()] = "A"
        aligned[n], states[n] = a, st

    weights = {
        "QQQ": rules.to_weights(pd.Series(0.0, index=rets.index)),
        "TQQQ": rules.to_weights(pd.Series(1.0, index=rets.index)),
    }
    for n in VARIANTS:
        weights[n] = rules.to_weights(states[n].map(rules.SPEC_WEIGHTS))
    names = ["QQQ", "TQQQ", *VARIANTS]
    runs = {n: portfolio.run(weights[n], rets, COST) for n in names}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last = dates[-1]
    vix_miss = len(dates) - len(set(vix.index) & set(dates))
    L: list[str] = [
        "# VIX 보정 ETF 신호 결과",
        "",
        f"- 생성: {now}",
        f"- 기간: {dates[0]}~{last} (거래일 {len(dates)}일)",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, OAS {oas.index[-1]}, VIX {vix.index[-1]}",
        f"- 앵커: {anchor_date} OAS {anchor_value:.2f} (수준 조건 끔, Δ10만 사용)",
        f"- VIX 거래일 결측: {vix_miss}일 (예측 NaN → 상태 A)",
        "- 계수 2023-10~2026-10 고정, 2010~2023 표본외, 비용 0.1%, 세전",
        "",
    ]

    obs_pos = {d: i for i, d in enumerate(oas.index)}
    ev = [d for d in dates if d in obs_set]
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
            ed = aligned[n].loc[d, "d10_bp"]
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

    dates3 = [d for d in px.index if d > start]
    L += ["## 2. 하루 단위 표본외 비교 (nowcast, 확장창)",
          "| 변형 | 평가일 | 평균절대 (bp) | 최대 (bp) | 90분위 (bp) | 하루변화 표본외 R² |",
          "|---|---|---|---|---|---|"]
    for n, cols in VARIANTS.items():
        nc = proxy.nowcast(oas, features, dates3, cols)
        evq = [d for d in dates3 if bool(nc.loc[d, "fitted"]) and d in obs_set]
        if evq:
            est = nc.loc[evq, "oas"].to_numpy(dtype=float)
            act = oas.loc[evq].to_numpy(dtype=float)
            base_v = nc.loc[evq, "base"].to_numpy(dtype=float)
            err = np.abs(est - act) * 100
            mae, mx, p90 = float(err.mean()), float(err.max()), float(np.percentile(err, 90))
            r2o = _r2(act - base_v, est - base_v)
        else:
            mae = mx = p90 = r2o = float("nan")
        L.append(f"| {n} | {len(evq)} | {_base._num(mae, 1)} | {_base._num(mx, 1)}"
                 f" | {_base._num(p90, 1)} | {_base._num(r2o, 3)} |")
    L.append("")

    L += ["## 3. 전체 기간 요약",
          *_base._summary_table(names, runs, weights),
          ""]

    L += ["## 4. 연도별 수익",
          "| 연도 | " + " | ".join(names) + " |",
          "|" + "---|" * (len(names) + 1)]
    y = pd.concat({n: perf.yearly(runs[n]["ret"]) for n in names}, axis=1).sort_index()
    for yr in y.index:
        L.append("| " + str(yr) + " | " + " | ".join(_base._pct(y.loc[yr, n]) for n in names) + " |")
    L.append("")

    L.append("## 5. 스트레스 구간")
    for label, s, e in _etf.STRESS:
        e2 = e or last
        win = [d for d in dates if s <= d <= e2]
        L += [f"### {label} ({s}~{e2})",
              "| 전략 | 첫 감축일 | 최저 TQQQ | 복귀일 | 구간 수익 | 구간 MDD |",
              "|---|---|---|---|---|---|"]
        if not win:
            L.append("| 구간 내 거래일 없음 | - | - | - | - | - |")
            continue
        for n in VARIANTS:
            w = weights[n]["TQQQ"].loc[win]
            cuts = [d for d in win if w.loc[d] < 1 - 1e-9]
            first_cut = cuts[0] if cuts else "-"
            rec = "-"
            if cuts:
                for d in win:
                    if d > cuts[0] and w.loc[d] > 1 - 1e-9:
                        rec = d
                        break
            rslice = runs[n]["ret"].loc[win]
            wr = float((1 + rslice).prod() - 1)
            L.append(f"| {n} | {first_cut} | {_base._pct(float(w.min()))} | {rec}"
                     f" | {_base._pct(wr)} | {_base._pct(perf.max_drawdown(rslice))} |")
        for n in ["TQQQ", "QQQ"]:
            rslice = runs[n]["ret"].loc[win]
            wr = float((1 + rslice).prod() - 1)
            L.append(f"| {n} | - | - | - | {_base._pct(wr)} | {_base._pct(perf.max_drawdown(rslice))} |")
    L.append("")

    L.append("## 6. 상태 일수")
    for n in VARIANTS:
        L += [f"### {n}",
              "| 상태 | 일수 |",
              "|---|---|"]
        sc = states[n].value_counts()
        for g in ["A", "B", "C", "D"]:
            L.append(f"| {g} | {int(sc.get(g, 0))} |")
    L.append("")

    cut_dates = [d for d in dates if d > start]
    rets_cut = rets.loc[cut_dates]
    orc = proxy.oracle(oas, cut_dates)
    weights7 = {n: weights[n].loc[cut_dates] for n in VARIANTS}
    weights7["ORACLE"] = rules.to_weights(rules.spec_states(orc).map(rules.SPEC_WEIGHTS))
    names7 = [*VARIANTS, "ORACLE"]
    runs7 = {n: portfolio.run(weights7[n], rets_cut, COST) for n in names7}
    L += ["## 7. 실 OAS 겹치는 3년 비교",
          f"구간: {cut_dates[0]}~{cut_dates[-1]}. 구간 첫날 목표 비중 그대로 portfolio.run 재실행."
          " ORACLE은 실매매 불가 상한.",
          *_base._summary_table(names7, runs7, weights7),
          ""]

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
