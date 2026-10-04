"""ETF 단독 OAS 신호 장기 백테스트 (D9) — stdout 마크다운 표 + RESULTS_ETF_PROXY.md 저장.

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_etf_proxy.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest_daily import perf, portfolio
from research.backtest_daily.data_us import load_etf_tr, load_fred
from research.us_oas import proxy, rules
from research.us_oas import run_tqqq_oas as _base

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = 0.001
OAS_SERIES = "BAMLH0A0HYM2"
RESULTS = Path(__file__).resolve().parent / "RESULTS_ETF_PROXY.md"
STRESS = [("2011 유럽", "20110701", "20111231"),
          ("2015-16 조정", "20150801", "20160331"),
          ("2018 Q4", "20181001", "20181231"),
          ("2020 코로나", "20200215", "20200630"),
          ("2022 금리", "20220101", "20221231"),
          ("2025 관세", "20250215", "20250531")]


def main() -> None:
    oas = load_fred(OAS_SERIES)
    px = load_etf_tr(["TQQQ", "QQQ", "HYG", "IEI"])
    lr = np.log1p(px[["HYG", "IEI"]])
    coef = proxy.fit_fixed(oas, lr, oas.index[0], oas.index[-1])
    pairs_all = proxy.daily_pairs(oas, lr)
    n_pairs = int(((pairs_all.index >= oas.index[0]) & (pairs_all.index <= oas.index[-1])).sum())
    valid = px[["TQQQ", "QQQ", "HYG", "IEI"]].notna().all(axis=1)
    first = valid[valid].index[0]
    dates = [d for d in px.index if d >= first]
    rets = px.loc[dates][["TQQQ", "QQQ"]]
    if bool(rets.isna().any().any()):
        raise ValueError("NaN return in period")
    first_oas = oas.index[0]
    obs_set = set(oas.index)
    if first_oas in dates and first_oas in obs_set:
        anchor_date = first_oas
        anchor_value = float(oas.loc[first_oas])
    else:
        cands = [d for d in dates if d >= first_oas and d in obs_set]
        if not cands:
            raise ValueError("no anchor trading day with OAS obs")
        anchor_date = cands[0]
        anchor_value = float(oas.loc[anchor_date])
    aligned = proxy.etf_signal(lr, coef, anchor_date, anchor_value, dates, window=10)
    states_level = rules.spec_states(aligned)
    states_level[aligned["d10_bp"].isna()] = "A"
    aligned_delta = aligned.copy()
    # oas=4.0 고정: "OAS<5" 항상 참, 6·8% 조건·E 미발동 → 10일 변화 단계 A~D만
    aligned_delta["oas"] = 4.0
    states_delta = rules.spec_states(aligned_delta)
    states_delta[aligned_delta["d10_bp"].isna()] = "A"
    weights = {
        "QQQ": rules.to_weights(pd.Series(0.0, index=rets.index)),
        "TQQQ": rules.to_weights(pd.Series(1.0, index=rets.index)),
        "ETF_LEVEL": rules.to_weights(states_level.map(rules.SPEC_WEIGHTS)),
        "ETF_DELTA": rules.to_weights(states_delta.map(rules.SPEC_WEIGHTS)),
    }
    names = ["QQQ", "TQQQ", "ETF_LEVEL", "ETF_DELTA"]
    runs = {n: portfolio.run(weights[n], rets, COST) for n in names}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last = dates[-1]
    L: list[str] = [
        "# ETF 단독 OAS 신호 결과",
        "",
        f"- 생성: {now}",
        f"- 기간: {dates[0]}~{last} (거래일 {len(dates)}일)",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, OAS {oas.index[-1]}",
        f"- 계수: b0={coef[0]:.4f}, b1={coef[1]:.4f}, b2={coef[2]:.4f} (학습 쌍 {n_pairs}개)",
        f"- 앵커: {anchor_date} OAS {anchor_value:.2f}",
        "- 계수는 2023-10~2026-10 고정 → 2010~2023 표본외, 비용 0.1%, 세전",
        "",
    ]

    ev = [d for d in dates if d in obs_set]
    est = aligned.loc[ev, "oas"].to_numpy(dtype=float)
    act = oas.loc[ev].to_numpy(dtype=float)
    diff = np.abs(est - act) * 100
    mae, mx = float(diff.mean()), float(diff.max())
    last_diff = float((est[-1] - act[-1]) * 100)
    obs_pos = {d: i for i, d in enumerate(oas.index)}
    ed_list: list[float] = []
    ad_list: list[float] = []
    for d in ev:
        j = obs_pos[d]
        if j < 10:
            continue
        ed = aligned.loc[d, "d10_bp"]
        if pd.isna(ed):
            continue
        ad = (float(oas.iloc[j]) - float(oas.iloc[j - 10])) * 100
        ed_list.append(float(ed))
        ad_list.append(ad)
    corr = float(np.corrcoef(ed_list, ad_list)[0, 1]) if len(ed_list) >= 2 else float("nan")
    last_s = "-" if pd.isna(last_diff) else f"{last_diff:+.1f}"
    L += ["## 1. 수준 표류 점검 (OAS 관측 기간만)",
          f"평가일: {len(ev)}일 ({ev[0]}~{ev[-1]}), d10 비교 {len(ed_list)}일.",
          "| 지표 | 값 |",
          "|---|---|",
          f"| 평균절대 차이 (bp) | {_base._num(mae, 1)} |",
          f"| 최대 차이 (bp) | {_base._num(mx, 1)} |",
          f"| 마지막 날 차이 (추정-실제, bp) | {last_s} |",
          f"| d10 상관계수 (추정 vs 실제 10관측 차) | {_base._num(corr, 3)} |",
          ""]

    L += ["## 2. 전체 기간 요약",
          *_base._summary_table(names, runs, weights),
          ""]

    L += ["## 3. 연도별 수익",
          "| 연도 | " + " | ".join(names) + " |",
          "|---|---|---|---|---|"]
    y = pd.concat({n: perf.yearly(runs[n]["ret"]) for n in names}, axis=1).sort_index()
    for yr in y.index:
        L.append("| " + str(yr) + " | " + " | ".join(_base._pct(y.loc[yr, n]) for n in names) + " |")
    L.append("")

    L.append("## 4. 최악 낙폭 5개")
    for n in names:
        L += [f"### {n}",
              "| 고점 | 저점 | 회복 | 낙폭 | 일수 |",
              "|---|---|---|---|---|"]
        for _, r in perf.drawdowns(runs[n]["ret"], 5).iterrows():
            pk = "-" if pd.isna(r["peak"]) else r["peak"]
            rc = "-" if pd.isna(r["recovery"]) else r["recovery"]
            L.append(f"| {pk} | {r['trough']} | {rc} | {_base._pct(r['depth'])} | {int(r['days'])} |")
    L.append("")

    L += ["## 5. 상태 일수",
          "### ETF_LEVEL",
          "| 상태 | 일수 |",
          "|---|---|"]
    sc = states_level.value_counts()
    for s in ["A", "B", "C", "D", "E"]:
        L.append(f"| {s} | {int(sc.get(s, 0))} |")
    L += ["### ETF_DELTA",
          "| 상태 | 일수 |",
          "|---|---|"]
    sd = states_delta.value_counts()
    for s in ["A", "B", "C", "D", "E"]:
        L.append(f"| {s} | {int(sd.get(s, 0))} |")
    L.append("")

    L.append("## 6. 스트레스 구간")
    for label, start, end in STRESS:
        e = end or last
        win = [d for d in dates if start <= d <= e]
        L += [f"### {label} ({start}~{e})",
              "| 전략 | 첫 감축일 | 최저 TQQQ | 복귀일 | 구간 수익 | 구간 MDD |",
              "|---|---|---|---|---|---|"]
        if not win:
            L.append("| 구간 내 거래일 없음 | - | - | - | - | - |")
            continue
        for n in ["ETF_LEVEL", "ETF_DELTA"]:
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

    cut_dates = [d for d in dates if d > oas.index[0]]
    rets_cut = rets.loc[cut_dates]
    lag1 = rules.align_oas(oas, cut_dates)
    nc = proxy.nowcast(oas, lr, cut_dates)
    weights7 = {
        "ETF_LEVEL": weights["ETF_LEVEL"].loc[cut_dates],
        "ETF_DELTA": weights["ETF_DELTA"].loc[cut_dates],
        "LAG1": rules.to_weights(rules.spec_states(lag1).map(rules.SPEC_WEIGHTS)),
        "NOWCAST": rules.to_weights(rules.spec_states(nc).map(rules.SPEC_WEIGHTS)),
    }
    names7 = ["ETF_LEVEL", "ETF_DELTA", "LAG1", "NOWCAST"]
    runs7 = {n: portfolio.run(weights7[n], rets_cut, COST) for n in names7}
    L += ["## 7. 실 OAS 겹치는 3년 비교",
          f"구간: {cut_dates[0]}~{cut_dates[-1]}. 구간 첫날 목표 비중 그대로 portfolio.run 재실행.",
          *_base._summary_table(names7, runs7, weights7),
          ""]

    L += ["## 8. ETF_DELTA 상태 변경 (2020-02~2020-06)",
          "| 날짜 | d10_bp | 변경 |",
          "|---|---|---|"]
    prev = states_delta.shift(1)
    prev.iloc[0] = "A"
    chg = states_delta[states_delta != prev]
    chg_win = [d for d in chg.index if "20200201" <= d <= "20200630"]
    if not chg_win:
        L.append("없음")
    else:
        for d in chg_win:
            bp = aligned.loc[d, "d10_bp"]
            b = "-" if pd.isna(bp) else f"{bp:+.0f}"
            L.append(f"| {d} | {b} | {prev.loc[d]}→{states_delta.loc[d]} |")

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
