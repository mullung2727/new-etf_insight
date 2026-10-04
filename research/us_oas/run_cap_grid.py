"""TQQQ 상한 고정 x 200일선 피신 격자 — 1999~ 합성 이음 (D23).

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_cap_grid.py
stdout 마크다운 표 + RESULTS_CAP_GRID.md 저장.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from research.backtest_daily import perf, portfolio
from research.backtest_daily.data_us import (
    load_etf_tr, load_fred, synth_cash, synth_leveraged,
)
from research.us_oas import rules
from research.us_oas import run_macro_overlay as _macro
from research.us_oas import run_tqqq_oas as _base
from research.us_oas import run_trend_grid as _trend

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = 0.001
REBALANCE_EVERY = 21
MA_WINDOW = 200
CONFIRM_DAYS = 20
CAPS = (0.5, 0.6, 0.7, 0.8, 1.0)
RESTS = ("QQQ", "BIL")
TRENDS = ("none", "instant", "confirm")
DRAWDOWNS = _macro.DRAWDOWNS
DOTCOM = DRAWDOWNS[0][1:]
GFC = DRAWDOWNS[1][1:]
RESULTS = Path(__file__).resolve().parent / "RESULTS_CAP_GRID.md"
BENCH = ["TQQQ", "QQQ", "QQQ_MA200"]


def _grid_name(cap: float, rest: str, trend: str) -> str:
    return f"C{int(round(cap * 100))}_{rest}_{trend}"


def _grid_names() -> list[str]:
    return [_grid_name(c, r, t) for c in CAPS for r in RESTS for t in TRENDS]


def _cap_weights(cap: float, rest: str, s: pd.Series) -> pd.DataFrame:
    """TQQQ=c*s, R=(1-c)*s, BIL=1-s. columns ["TQQQ", "QQQ", "BIL"], 행 합 1."""
    tqqq = cap * s.astype(float)
    r = (1.0 - cap) * s.astype(float)
    if rest == "QQQ":
        return pd.DataFrame({"TQQQ": tqqq, "QQQ": r, "BIL": 1.0 - s}, index=s.index)
    z = pd.Series(0.0, index=s.index)
    return pd.DataFrame({"TQQQ": tqqq, "QQQ": z, "BIL": 1.0 - tqqq}, index=s.index)


def _pivot_lines(rest: str, value) -> list[str]:
    """value(이름)->문자열을 cap(행) x trend(열) 피벗으로."""
    lines = ["| cap | " + " | ".join(TRENDS) + " |",
             "|" + "---|" * (len(TRENDS) + 1)]
    for c in CAPS:
        cells = [value(_grid_name(c, rest, t)) for t in TRENDS]
        lines.append(f"| {c:g} | " + " | ".join(cells) + " |")
    return lines


def main() -> None:
    px = load_etf_tr(["QQQ", "TQQQ", "BIL"])
    dtb3 = load_fred("DTB3")

    qqq_full = px["QQQ"].dropna()
    tqqq_actual = px["TQQQ"]
    bil_actual = px["BIL"]
    tqqq_first = str(tqqq_actual.dropna().index[0])
    bil_first = str(bil_actual.dropna().index[0])

    # ---- 합성 이음 (run_macro_overlay·run_exit_compare·run_reentry_grid 와 동일) ----
    syn_t = synth_leveraged(px["QQQ"], dtb3)
    syn_b = synth_cash(dtb3, px.index.tolist())
    tqqq = tqqq_actual.copy()
    tqqq.loc[px.index < tqqq_first] = syn_t.loc[syn_t.index < tqqq_first]
    bil = bil_actual.copy()
    bil.loc[px.index < bil_first] = syn_b.loc[syn_b.index < bil_first]

    # ---- 신호: QQQ 총수익 수준 1999~ 전체로 MA 계산 후 백테스트 날짜로 자름 ----
    qqq_level_full = (1 + qqq_full).cumprod()
    qdates = qqq_full.index.tolist()
    start = qdates[MA_WINDOW]  # 200일선 유효 이후, 전 전략 같은 시작일
    dates = [d for d in qdates if d >= start]
    states = {
        "none": pd.Series(1.0, index=dates),
        "instant": rules.trend_state(
            qqq_level_full, MA_WINDOW, "instant").reindex(dates),
        "confirm": rules.trend_state(
            qqq_level_full, MA_WINDOW, "confirm",
            confirm_days=CONFIRM_DAYS).reindex(dates),
    }
    if any(bool(s.isna().any()) for s in states.values()):
        raise ValueError("trend_state NaN in period")

    rets = pd.DataFrame({"TQQQ": tqqq.reindex(dates),
                         "QQQ": qqq_full.reindex(dates),
                         "BIL": bil.reindex(dates)})
    if bool(rets.isna().any().any()):
        raise ValueError("NaN return in period")

    weights: dict[str, pd.DataFrame] = {}
    z = pd.Series(0.0, index=rets.index)
    o = pd.Series(1.0, index=rets.index)
    weights["TQQQ"] = pd.DataFrame({"TQQQ": o, "QQQ": z, "BIL": z}, index=rets.index)
    weights["QQQ"] = pd.DataFrame({"TQQQ": z, "QQQ": o, "BIL": z}, index=rets.index)
    s200 = states["instant"]
    weights["QQQ_MA200"] = pd.DataFrame({"TQQQ": z, "QQQ": s200, "BIL": 1.0 - s200},
                                        index=rets.index)
    for c in CAPS:
        for r in RESTS:
            for t in TRENDS:
                weights[_grid_name(c, r, t)] = _cap_weights(c, r, states[t])

    names = BENCH + _grid_names()
    runs = {n: portfolio.run(weights[n], rets, COST, REBALANCE_EVERY) for n in names}

    sub2 = [d for d in dates if d[:6] >= "201002"]
    if not sub2:
        raise ValueError("empty actual subperiod")
    runs_s2 = {n: portfolio.run(weights[n].loc[sub2], rets.loc[sub2],
                                COST, REBALANCE_EVERY) for n in names}

    full = {n: perf.summary(runs[n]["ret"]) for n in names}
    dot = {n: _trend._window_ret(runs[n]["ret"], dates, *DOTCOM) for n in names}
    gfc = {n: _trend._window_ret(runs[n]["ret"], dates, *GFC) for n in names}
    real = {n: perf.summary(runs_s2[n]["ret"]) for n in names}
    avg_t = {n: float(weights[n]["TQQQ"].mean()) for n in names}
    tpy = {n: perf.trades_per_year(runs[n]["turnover"]) for n in names}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L: list[str] = [
        "# TQQQ 상한 고정 x 200일선 피신 격자",
        "",
        f"- 생성: {now}",
        f"- 기간: {dates[0]}~{dates[-1]} (거래일 {len(dates)}일,"
        f" QQQ 수익 첫날 {qdates[0]} + {MA_WINDOW}거래일부터, 전 전략 동일)",
        "- 합성: 3배 일간 리밸런싱 근사, 보수 연 0.95%, 차입비용 DTB3(연율 %),"
        f" 실제 TQQQ 상장 전만 합성(실제 첫 수익일 {tqqq_first}부터 실제);"
        f" BIL도 첫 수익일 {bil_first} 전 synth_cash(DTB3 일간 rate/100/252)",
        "- 신호: QQQ 총수익 수준 1999~ 전체로 200일 이동평균 계산 후 백테스트 날짜로 절단,"
        " 당일 종가 판단·당일 종가 체결",
        "- 상한·추세는 사전 고정 값, 월 1회(21거래일) 재조정, 전 조합 공개, 비용 0.1%, 세전",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, DTB3 {dtb3.index[-1]}",
        "- 전략: TQQQ=c*s, R=(1-c)*s, BIL=1-s; s=추세 상태(instant 즉시 복귀/"
        "confirm 연속 20일째 복귀, 이탈은 즉시 0); none은 s=1 항상;"
        " 200일선 아래 피신분은 BIL 고정;"
        " QQQ_MA200=200일 instant로 QQQ↔BIL(TQQQ 없음);"
        " C100은 R 무관(동일 값)",
        "",
        "## 1. 전체 표 (기준 3개 + 30개)",
        f"닷컴={DOTCOM[0]}~{DOTCOM[1]}, 금융위기={GFC[0]}~{GFC[1]},"
        f" 실제구간={sub2[0]}~{sub2[-1]} ({len(sub2)}일, 구간 첫날 목표비중 그대로 재실행).",
        "| 전략 | CAGR | MDD | Sharpe | Calmar | 닷컴 | 금융위기"
        " | 실제CAGR | 실제MDD | 평균TQQQ | 연매매 |",
        "|" + "---|" * 11,
    ]
    for n in names:
        s = full[n]
        L.append(f"| {n} | {_base._pct(s['cagr'])} | {_base._pct(s['mdd'])}"
                 f" | {_base._num(s['sharpe'])} | {_base._num(s['calmar'])}"
                 f" | {_base._pct(dot[n])} | {_base._pct(gfc[n])}"
                 f" | {_base._pct(real[n]['cagr'])} | {_base._pct(real[n]['mdd'])}"
                 f" | {_base._pct(avg_t[n])} | {_base._num(tpy[n], 1)} |")
    L.append("")

    L += ["## 2. 피벗 (행 cap × 열 trend)"]
    for r in RESTS:
        L += [f"### R {r} — ① 전체 CAGR",
              *_pivot_lines(r, lambda n: _base._pct(full[n]["cagr"])),
              "",
              f"### R {r} — ② 닷컴 수익",
              *_pivot_lines(r, lambda n: _base._pct(dot[n])),
              "",
              f"### R {r} — ③ 2010~ CAGR",
              *_pivot_lines(r, lambda n: _base._pct(real[n]["cagr"])),
              "",
              f"### R {r} — ④ 전체 MDD",
              *_pivot_lines(r, lambda n: _base._pct(full[n]["mdd"])),
              "",
              f"### R {r} — ⑤ 전체 Sharpe",
              *_pivot_lines(r, lambda n: _base._num(full[n]["sharpe"])),
              ""]

    L += ["## 3. 큰 낙폭 구간 수익 (R=QQQ 15개 + 기준)",
          ", ".join(f"{label}={s}~{e}" for label, s, e in DRAWDOWNS) + ".",
          "| 전략 | " + " | ".join(label for label, _, _ in DRAWDOWNS) + " |",
          "|" + "---|" * (len(DRAWDOWNS) + 1)]
    sec3 = BENCH + [_grid_name(c, "QQQ", t) for c in CAPS for t in TRENDS]
    for n in sec3:
        cells = [_base._pct(_trend._window_ret(runs[n]["ret"], dates, s, e))
                 for _, s, e in DRAWDOWNS]
        L.append(f"| {n} | {' | '.join(cells)} |")
    L.append("")

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
