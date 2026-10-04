"""TQQQ·OAS 5전략 비교 — stdout 마크다운 표 + RESULTS.md 저장.

etl/ 에서: PYTHONPATH=.. uv run python ../research/us_oas/run_tqqq_oas.py
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from research.backtest_daily import perf, portfolio
from research.backtest_daily.data_us import load_etf_tr, load_fred
from research.us_oas import rules

COST = 0.001
OAS_SERIES = "BAMLH0A0HYM2"
RESULTS = Path(__file__).resolve().parent / "RESULTS.md"
STRESS = [("2024-08", "20240715", "20240930"),
          ("2025-03~04", "20250215", "20250531"),
          ("2026-10", "20260901", None)]


def _pct(x) -> str:
    return "-" if pd.isna(x) else f"{x * 100:.1f}%"


def _num(x, nd: int = 2) -> str:
    return "-" if pd.isna(x) else f"{x:.{nd}f}"


def _summary_table(names, runs, weights) -> list[str]:
    lines = ["| 전략 | CAGR | 변동성 | Sharpe | MDD | Calmar | 최종자산 | 평균 TQQQ | 연 매매 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for n in names:
        s = perf.summary(runs[n]["ret"])
        avg = float(weights[n]["TQQQ"].mean())
        tpy = perf.trades_per_year(runs[n]["turnover"])
        lines.append(
            f"| {n} | {_pct(s['cagr'])} | {_pct(s['vol'])} | {_num(s['sharpe'])}"
            f" | {_pct(s['mdd'])} | {_num(s['calmar'])} | {_num(s['final'])}"
            f" | {_pct(avg)} | {_num(tpy, 1)} |"
        )
    return lines


def main() -> None:
    oas = load_fred(OAS_SERIES)
    px = load_etf_tr(["TQQQ", "QQQ"])
    dates = [d for d in px.index if d > oas.index[0]]
    rets = px.loc[dates]
    if bool(rets.isna().any().any()):
        raise ValueError("NaN return in period")
    aligned = rules.align_oas(oas, dates)
    weights = {
        "QQQ": rules.to_weights(pd.Series(0.0, index=rets.index)),
        "TQQQ": rules.to_weights(pd.Series(1.0, index=rets.index)),
        "SPEC": rules.spec_weights(aligned),
        "BOLL_A": rules.boll_weights(aligned, "A"),
        "BOLL_B": rules.boll_weights(aligned, "B"),
    }
    names = ["QQQ", "TQQQ", "SPEC", "BOLL_A", "BOLL_B"]
    runs = {n: portfolio.run(weights[n], rets, COST) for n in names}
    states = rules.spec_states(aligned)
    regimes = rules.boll_regime(aligned)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last = dates[-1]
    L: list[str] = [
        "# TQQQ·OAS 결과",
        "",
        f"- 생성: {now}",
        f"- 기간: {dates[0]}~{last} (거래일 {len(dates)}일)",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, OAS {oas.index[-1]}",
        "- 비용 한 방향 0.1%, 세전, 통과 판정 없음",
        "",
        "## 1. 전체 기간 요약",
        *_summary_table(names, runs, weights),
        "",
    ]

    cut = aligned[aligned["mid"].notna()].index[0]
    rets2 = rets.loc[cut:]
    w2 = {n: weights[n].loc[cut:] for n in names}
    runs2 = {n: portfolio.run(w2[n], rets2, COST) for n in names}
    L += ["## 2. 같은 구간 비교 (볼린저 밴드 이후)",
          f"구간: {cut}~{last}. 구간 첫날 목표 비중 그대로 portfolio.run 재실행.",
          *_summary_table(names, runs2, w2),
          ""]

    L += ["## 3. 연도별 수익",
          "| 연도 | " + " | ".join(names) + " |",
          "|---|---|---|---|---|---|"]
    y = pd.concat({n: perf.yearly(runs[n]["ret"]) for n in names}, axis=1).sort_index()
    for yr in y.index:
        L.append("| " + str(yr) + " | " + " | ".join(_pct(y.loc[yr, n]) for n in names) + " |")
    L.append("")

    L.append("## 4. 최악 낙폭 5개")
    for n in names:
        L += [f"### {n}",
              "| 고점 | 저점 | 회복 | 낙폭 | 일수 |",
              "|---|---|---|---|---|"]
        for _, r in perf.drawdowns(runs[n]["ret"], 5).iterrows():
            pk = "-" if pd.isna(r["peak"]) else r["peak"]
            rc = "-" if pd.isna(r["recovery"]) else r["recovery"]
            L.append(f"| {pk} | {r['trough']} | {rc} | {_pct(r['depth'])} | {int(r['days'])} |")
    L.append("")

    L += ["## 5. 상태 일수",
          "### SPEC",
          "| 상태 | 일수 |",
          "|---|---|"]
    sc = states.value_counts()
    for s in ["A", "B", "C", "D", "E"]:
        L.append(f"| {s} | {int(sc.get(s, 0))} |")
    L += ["### BOLL",
          "| 국면 | 일수 |",
          "|---|---|"]
    rc = regimes.value_counts()
    for g in ["risk_off", "normal", "risk_on"]:
        L.append(f"| {g} | {int(rc.get(g, 0))} |")
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
        for n in ["SPEC", "BOLL_A", "BOLL_B"]:
            w = weights[n]["TQQQ"].loc[win]
            cuts = [d for d in win if w.loc[d] < 1 - 1e-9]
            first = cuts[0] if cuts else "-"
            rec = "-"
            if cuts:
                for d in win:
                    if d > cuts[0] and w.loc[d] > 1 - 1e-9:
                        rec = d
                        break
            rslice = runs[n]["ret"].loc[win]
            wr = float((1 + rslice).prod() - 1)
            L.append(f"| {n} | {first} | {_pct(float(w.min()))} | {rec}"
                     f" | {_pct(wr)} | {_pct(perf.max_drawdown(rslice))} |")
        for n in ["TQQQ", "QQQ"]:
            rslice = runs[n]["ret"].loc[win]
            wr = float((1 + rslice).prod() - 1)
            L.append(f"| {n} | - | - | - | {_pct(wr)} | {_pct(perf.max_drawdown(rslice))} |")
    L.append("")

    L += ["## 7. 신호 이력",
          "### SPEC 상태 변경"]
    prev = states.shift(1)
    prev.iloc[0] = "A"
    chg = states[states != prev]
    if len(chg) == 0:
        L.append("없음")
    else:
        L += ["| 날짜 | oas_date | oas | d10_bp | 변경 |",
              "|---|---|---|---|---|"]
        for d in chg.index:
            r = aligned.loc[d]
            od = "-" if pd.isna(r["oas_date"]) else r["oas_date"]
            o = "-" if pd.isna(r["oas"]) else f"{r['oas']:.2f}"
            b = "-" if pd.isna(r["d10_bp"]) else f"{r['d10_bp']:+.0f}"
            L.append(f"| {d} | {od} | {o} | {b} | {prev.loc[d]}→{states.loc[d]} |")
    L.append("### BOLL 국면 변경")
    pr = regimes.shift(1)
    pr.iloc[0] = regimes.iloc[0]
    chg2 = regimes[regimes != pr]
    if len(chg2) == 0:
        L.append("없음")
    else:
        L += ["| 날짜 | oas | mid | upper | lower | 변경 |",
              "|---|---|---|---|---|---|"]
        for d in chg2.index:
            r = aligned.loc[d]
            cells = [("-" if pd.isna(r[c]) else f"{r[c]:.2f}") for c in ["oas", "mid", "upper", "lower"]]
            L.append(f"| {d} | {' | '.join(cells)} | {pr.loc[d]}→{regimes.loc[d]} |")

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
