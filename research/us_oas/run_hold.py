"""OAS 감축 + 200일선 유지(HOLD) 조합 (D12) — stdout 마크다운 표 + RESULTS_HOLD.md 저장.

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_hold.py
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
from research.us_oas import run_vix_asym as _asym

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = 0.001
OAS_SERIES = "BAMLH0A0HYM2"
RESULTS = Path(__file__).resolve().parent / "RESULTS_HOLD.md"
COLS = ("HYG", "IEI", "HYG_l1")
MA_WINDOW = 200
MA_DOWN_WEIGHT = 0.4
MA_NOTE = "참고: 0.4 는 임의 선택(명세 C 단계와 같은 비중)"


def _below_on(dates: list[str], below_full: pd.Series) -> pd.Series:
    """전체 기간 200일선 이탈 여부를 백테스트 날짜로 자른다."""
    b = below_full.reindex(dates)
    if bool(b.isna().any()):
        raise ValueError("QQQ MA200 missing for some backtest dates")
    return b.astype(bool)


def _ma_weights(below: pd.Series) -> pd.DataFrame:
    """MA200 참고 비중: 200일선 아래면 0.4, 위면 1.0."""
    w = pd.Series(np.where(below.to_numpy(dtype=bool), MA_DOWN_WEIGHT, 1.0),
                  index=below.index, dtype=float)
    return rules.to_weights(w)


def _stress_lines(stress, dates, runs, weights, strat_names, last) -> list[str]:
    L: list[str] = []
    for label, s, e in stress:
        e2 = e or last
        win = [d for d in dates if s <= d <= e2]
        L += [f"### {label} ({s}~{e2})",
              "| 전략 | 첫 감축일 | 최저 TQQQ | 복귀일 | 구간 수익 | 구간 MDD |",
              "|---|---|---|---|---|---|"]
        if not win:
            L.append("| 구간 내 거래일 없음 | - | - | - | - | - |")
            continue
        for n in strat_names:
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
    return L


def main() -> None:
    oas = load_fred(OAS_SERIES)
    px = load_etf_tr(["TQQQ", "QQQ", "HYG", "IEI"])
    vix = load_index("^VIX")
    lr = np.log1p(px[["HYG", "IEI"]])
    features = proxy.make_features(lr, vix)

    start, end = oas.index[0], oas.index[-1]
    coef = proxy.fit_fixed(oas, features, start, end, COLS)
    pairs = proxy.daily_pairs(oas, features, COLS)
    n_pairs = int(((pairs.index >= start) & (pairs.index <= end)).sum())

    # 200일선: QQQ 수익 전체 기간(1999~) 누적수준으로 계산 뒤 백테스트 날짜로 절단
    qqq_ret_full = load_etf_tr(["QQQ"])["QQQ"].dropna()
    qqq_level_full = (1 + qqq_ret_full).cumprod()
    below_full = rules.below_ma(qqq_level_full, MA_WINDOW)

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

    a2 = proxy.etf_signal(features, coef, anchor_date, anchor_value,
                          dates2, window=10, cols=COLS)
    a2["oas"] = 4.0  # 수준 조건 끔, 10일 변화 단계 A~D만 (= 이전 BASE2)
    st2 = rules.spec_states(a2)
    st2[a2["d10_bp"].isna()] = "A"
    w_oas2 = st2.map(rules.SPEC_WEIGHTS).astype(float)
    below2 = _below_on(dates2, below_full)
    w_hold2 = rules.hold_while_below(w_oas2, below2)
    weights2 = {
        "QQQ": rules.to_weights(pd.Series(0.0, index=rets2.index)),
        "TQQQ": rules.to_weights(pd.Series(1.0, index=rets2.index)),
        "OAS": rules.to_weights(w_oas2),
        "OAS_HOLD": rules.to_weights(w_hold2),
        "MA200": _ma_weights(below2),
    }
    names2 = ["QQQ", "TQQQ", "OAS", "OAS_HOLD", "MA200"]
    runs2 = {n: portfolio.run(weights2[n], rets2, COST) for n in names2}

    # ---- 평가 1 기간·신호 (run_nowcast 와 동일, 명세 규칙 전체) ----
    dates1 = [d for d in px.index if d > start]
    rets1 = px.loc[dates1][["TQQQ", "QQQ"]]
    if bool(rets1.isna().any().any()):
        raise ValueError("NaN return in period")
    nc1 = proxy.nowcast(oas, features, dates1, COLS)
    st1 = rules.spec_states(nc1)
    w_oas1 = st1.map(rules.SPEC_WEIGHTS).astype(float)
    below1 = _below_on(dates1, below_full)
    w_hold1 = rules.hold_while_below(w_oas1, below1)
    lag1 = rules.align_oas(oas, dates1)
    weights1 = {
        "QQQ": rules.to_weights(pd.Series(0.0, index=rets1.index)),
        "TQQQ": rules.to_weights(pd.Series(1.0, index=rets1.index)),
        "LAG1": rules.to_weights(rules.spec_states(lag1).map(rules.SPEC_WEIGHTS)),
        "OAS": rules.to_weights(w_oas1),
        "OAS_HOLD": rules.to_weights(w_hold1),
        "MA200": _ma_weights(below1),
    }
    names1 = ["QQQ", "TQQQ", "LAG1", "OAS", "OAS_HOLD", "MA200"]
    runs1 = {n: portfolio.run(weights1[n], rets1, COST) for n in names1}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last1, last2 = dates1[-1], dates2[-1]
    coef_s = ", ".join(f"b_{c}={coef[i + 1]:.4f}" for i, c in enumerate(COLS))
    L: list[str] = [
        "# OAS 감축 + 200일선 유지 결과",
        "",
        f"- 생성: {now}",
        f"- 평가 1 기간: {dates1[0]}~{last1} (거래일 {len(dates1)}일)",
        f"- 평가 2 기간: {dates2[0]}~{last2} (거래일 {len(dates2)}일)",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, OAS {oas.index[-1]}, VIX {vix.index[-1]}",
        f"- 추정식 cols: {', '.join(COLS)}; 계수: b0={coef[0]:.4f}, {coef_s}"
        f" (학습 쌍 {n_pairs}개, {start}~{end})",
        f"- 앵커: {anchor_date} OAS {anchor_value:.2f} (평가 2, 수준 조건 끔, Δ10만 사용)",
        f"- QQQ 200일선: {qqq_ret_full.index[0]}~ 누적수준으로 계산 후 백테스트 날짜로 절단",
        "- 비용 0.1%, 세전, 계수 2023-10~2026-10 고정",
        "",
    ]

    L += ["## 1. 평가 2 요약 (16년 ETF 근사)",
          *_base._summary_table(names2, runs2, weights2),
          "",
          MA_NOTE,
          ""]

    L += ["## 2. 평가 2 연도별 수익",
          "| 연도 | " + " | ".join(names2) + " |",
          "|" + "---|" * (len(names2) + 1)]
    y = pd.concat({n: perf.yearly(runs2[n]["ret"]) for n in names2}, axis=1).sort_index()
    for yr in y.index:
        L.append("| " + str(yr) + " | " + " | ".join(_base._pct(y.loc[yr, n]) for n in names2) + " |")
    L.append("")

    L.append("## 3. 평가 2 큰 낙폭 구간 (TQQQ −30% 이하, 저점 날짜순)")
    dd = perf.drawdowns(rets2["TQQQ"], 12)
    dd = dd[dd["depth"] <= -0.30].sort_values("trough").reset_index(drop=True)
    if len(dd) == 0:
        L += ["TQQQ −30% 이하 낙폭 구간 없음.", ""]
    else:
        L += ["| 구간(고점~저점) | TQQQ 낙폭 | OAS | OAS_HOLD | MA200 | QQQ |",
              "|---|---|---|---|---|---|"]
        for _, r in dd.iterrows():
            pk = r["peak"]
            win_start = dates2[0] if pd.isna(pk) else pk
            seg = f"{'-' if pd.isna(pk) else pk}~{r['trough']}"
            win = [d for d in dates2 if win_start <= d <= r["trough"]]
            cells = []
            for n in ["OAS", "OAS_HOLD", "MA200", "QQQ"]:
                rslice = runs2[n]["ret"].loc[win]
                cells.append(_base._pct(float((1 + rslice).prod() - 1)))
            L.append(f"| {seg} | {_base._pct(r['depth'])} | {' | '.join(cells)} |")
        L.append("")

    L += ["## 4. 평가 2 스트레스 구간",
          *_stress_lines(_etf.STRESS, dates2, runs2, weights2,
                         ["OAS", "OAS_HOLD", "MA200"], last2),
          ""]

    below_days = int(below2.sum())
    L += ["## 5. 평가 2 이른 복귀 지표",
          "감축 에피소드 = TQQQ 비중 1 미만 연속 구간 수."
          " 이른 복귀 = 100% 복귀 뒤 10거래일 안에 다시 1 미만으로 내려간 횟수.",
          "",
          "| 전략 | 감축 에피소드 수 | 이른 복귀 수 | 연 매매 | 200일선 아래 일수 |",
          "|---|---|---|---|---|"]
    for n in ["OAS", "OAS_HOLD", "MA200"]:
        w = weights2[n]["TQQQ"]
        tpy = perf.trades_per_year(runs2[n]["turnover"])
        bd = str(below_days) if n in ("OAS_HOLD", "MA200") else "-"
        L.append(f"| {n} | {_asym._count_episodes(w)} | {_asym._count_early_recoveries(w)}"
                 f" | {_base._num(tpy, 1)} | {bd} |")
    L += [f"평가 2 기간 중 200일선 아래: {below_days}일 / {len(dates2)}일.", ""]

    L += ["## 6. 평가 1 요약 (3년 실매매 방식)",
          *_base._summary_table(names1, runs1, weights1),
          "",
          MA_NOTE,
          ""]

    L += ["## 7. 평가 1 스트레스 구간",
          *_stress_lines(_base.STRESS, dates1, runs1, weights1,
                         ["LAG1", "OAS", "OAS_HOLD", "MA200"], last1),
          ""]

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
