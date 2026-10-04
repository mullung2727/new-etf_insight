"""LAG1·NOWCAST·ORACLE 비교 — stdout 마크다운 표 + RESULTS_NOWCAST.md 저장.

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_nowcast.py
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
RESULTS = Path(__file__).resolve().parent / "RESULTS_NOWCAST.md"


def main() -> None:
    oas = load_fred(OAS_SERIES)
    px = load_etf_tr(["TQQQ", "QQQ", "HYG", "IEI"])
    dates = [d for d in px.index if d > oas.index[0]]
    rets = px.loc[dates][["TQQQ", "QQQ"]]
    if bool(rets.isna().any().any()):
        raise ValueError("NaN return in period")
    logret = np.log1p(px[["HYG", "IEI"]])
    lag1 = rules.align_oas(oas, dates)
    nc = proxy.nowcast(oas, logret, dates)
    orc = proxy.oracle(oas, dates)
    weights = {
        "QQQ": rules.to_weights(pd.Series(0.0, index=rets.index)),
        "TQQQ": rules.to_weights(pd.Series(1.0, index=rets.index)),
        "LAG1": rules.to_weights(rules.spec_states(lag1).map(rules.SPEC_WEIGHTS)),
        "NOWCAST": rules.to_weights(rules.spec_states(nc).map(rules.SPEC_WEIGHTS)),
        "ORACLE": rules.to_weights(rules.spec_states(orc).map(rules.SPEC_WEIGHTS)),
    }
    names = ["QQQ", "TQQQ", "LAG1", "NOWCAST", "ORACLE"]
    runs = {n: portfolio.run(weights[n], rets, COST) for n in names}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last = dates[-1]
    L: list[str] = [
        "# LAG1·NOWCAST·ORACLE 결과",
        "",
        f"- 생성: {now}",
        f"- 기간: {dates[0]}~{last} (거래일 {len(dates)}일)",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, OAS {oas.index[-1]}",
        "- 비용 한 방향 0.1%, 세전, ORACLE은 실매매 불가 상한",
        "",
        "## 1. 전체 기간 요약",
        *_base._summary_table(names, runs, weights),
        "",
    ]

    obs_set = set(oas.index)
    ev = [d for d in dates if bool(nc.loc[d, "fitted"]) and d in obs_set]
    if ev:
        est = nc.loc[ev, "oas"].to_numpy(dtype=float)
        act = oas.loc[ev].to_numpy(dtype=float)
        base_v = nc.loc[ev, "base"].to_numpy(dtype=float)
        err = np.abs(est - act) * 100
        mae, mx, p90 = float(err.mean()), float(err.max()), float(np.percentile(err, 90))
        ed, ad = est - base_v, act - base_v
        denom = float(((ad - ad.mean()) ** 2).sum())
        r2 = 1 - float(((ad - ed) ** 2).sum()) / denom if denom > 0 else float("nan")
        mae_base = float((np.abs(act - base_v) * 100).mean())
    else:
        mae = mx = p90 = r2 = mae_base = float("nan")
    L += ["## 2. 추정 품질",
          f"평가일: fitted=True 이고 T에 실제 관측이 있는 날 {len(ev)}일"
          f" (전체 fitted {int(nc['fitted'].sum())}일).",
          "| 지표 | 추정 | T−1 그대로 |",
          "|---|---|---|",
          f"| 평균절대 오차 (bp) | {_base._num(mae, 1)} | {_base._num(mae_base, 1)} |",
          f"| 최대 오차 (bp) | {_base._num(mx, 1)} | - |",
          f"| 90분위 오차 (bp) | {_base._num(p90, 1)} | - |",
          f"| 하루 변화 표본외 R² | {_base._num(r2, 3)} | - |",
          ""]

    st_lag1 = rules.spec_states(lag1)
    st_nc = rules.spec_states(nc)
    st_orc = rules.spec_states(orc)
    over_b = lambda s: int(s.isin(["B", "C", "D", "E"]).sum())
    L += ["## 3. 상태 일치 (ORACLE 기준)",
          "| 전략 | ORACLE과 다른 날 | B 이상 일수 |",
          "|---|---|---|",
          f"| LAG1 | {int((st_lag1 != st_orc).sum())} | {over_b(st_lag1)} |",
          f"| NOWCAST | {int((st_nc != st_orc).sum())} | {over_b(st_nc)} |",
          f"| ORACLE | 0 | {over_b(st_orc)} |",
          ""]

    L.append("## 4. 스트레스 구간")
    for label, start, end in _base.STRESS:
        e = end or last
        win = [d for d in dates if start <= d <= e]
        L += [f"### {label} ({start}~{e})",
              "| 전략 | 첫 감축일 | 최저 TQQQ | 복귀일 | 구간 수익 | 구간 MDD |",
              "|---|---|---|---|---|---|"]
        if not win:
            L.append("| 구간 내 거래일 없음 | - | - | - | - | - |")
            continue
        for n in ["LAG1", "NOWCAST", "ORACLE"]:
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
            L.append(f"| {n} | {first} | {_base._pct(float(w.min()))} | {rec}"
                     f" | {_base._pct(wr)} | {_base._pct(perf.max_drawdown(rslice))} |")
        for n in ["TQQQ", "QQQ"]:
            rslice = runs[n]["ret"].loc[win]
            wr = float((1 + rslice).prod() - 1)
            L.append(f"| {n} | - | - | - | {_base._pct(wr)}"
                     f" | {_base._pct(perf.max_drawdown(rslice))} |")
    L.append("")

    L += ["## 5. NOWCAST 상태 변경",
          "| 날짜 | base_date | base | est | d10_bp | 변경 |",
          "|---|---|---|---|---|---|"]
    prev = st_nc.shift(1)
    prev.iloc[0] = "A"
    chg = st_nc[st_nc != prev]
    if len(chg) == 0:
        L.append("없음")
    else:
        for d in chg.index:
            r = nc.loc[d]
            bd = "-" if pd.isna(r["base_date"]) else r["base_date"]
            b = "-" if pd.isna(r["base"]) else f"{r['base']:.2f}"
            e_ = "-" if pd.isna(r["oas"]) else f"{r['oas']:.2f}"
            bp = "-" if pd.isna(r["d10_bp"]) else f"{r['d10_bp']:+.0f}"
            L.append(f"| {d} | {bd} | {b} | {e_} | {bp} | {prev.loc[d]}→{st_nc.loc[d]} |")

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
