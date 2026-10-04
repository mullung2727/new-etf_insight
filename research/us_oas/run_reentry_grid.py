"""재진입 강화 × 이동평균 길이 격자 — 1999~ 합성 이음 (D21).

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_reentry_grid.py
stdout 마크다운 표 + RESULTS_REENTRY_GRID.md 저장.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest_daily import perf, portfolio
from research.backtest_daily.data_us import (
    load_etf_tr, load_fred, synth_cash, synth_leveraged,
)
from research.us_oas import rules
from research.us_oas import run_hold as _hold
from research.us_oas import run_macro_overlay as _macro
from research.us_oas import run_tqqq_oas as _base
from research.us_oas import run_trend_grid as _trend

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = 0.001
WINDOWS = (100, 150, 180, 200, 250)
REENTRIES = ("instant", "confirm", "band", "staged")
DEFENSES = ("BIL", "QQQ")
CONFIRM_DAYS = 20
BAND = 0.03
DRAWDOWNS = _macro.DRAWDOWNS
DOTCOM = DRAWDOWNS[0][1:]
GFC = DRAWDOWNS[1][1:]
REENTRY_WINDOW = ("20000103", "20021231")
RESULTS = Path(__file__).resolve().parent / "RESULTS_REENTRY_GRID.md"
BENCH = ["TQQQ", "QQQ", "QQQ_MA200", "M00_BIL"]


def _grid_name(window: int, reentry: str, defense: str) -> str:
    return f"W{window}_{reentry}_{defense}"


def _grid_names() -> list[str]:
    return [_grid_name(w, r, d) for w in WINDOWS for r in REENTRIES for d in DEFENSES]


def _weights_3(tqqq: pd.Series, defense: str) -> pd.DataFrame:
    """TQQQ 비중 + 나머지 전부 방어자산. columns ["TQQQ", "QQQ", "BIL"], 행 합 1."""
    t = tqqq.astype(float)
    z = pd.Series(0.0, index=t.index)
    rest = 1.0 - t
    if defense == "BIL":
        return pd.DataFrame({"TQQQ": t, "QQQ": z, "BIL": rest}, index=t.index)
    return pd.DataFrame({"TQQQ": t, "QQQ": rest, "BIL": z}, index=t.index)


def _reentries(w: pd.Series, dates: list[str]) -> int:
    """REENTRY_WINDOW 안 백테스트 날짜에서 TQQQ 비중이 0→양수로 바뀐 횟수."""
    s, e = REENTRY_WINDOW
    n = 0
    prev: float | None = None
    for d in dates:
        v = float(w.loc[d])
        if prev is not None and s <= d <= e and prev <= 1e-9 and v > 1e-9:
            n += 1
        prev = v
    return n


def _pivot_lines(defense: str, value) -> list[str]:
    """value(이름)->문자열을 window(행) × reentry(열) 피벗으로."""
    lines = ["| window | " + " | ".join(REENTRIES) + " |",
             "|" + "---|" * (len(REENTRIES) + 1)]
    for w in WINDOWS:
        cells = [value(_grid_name(w, r, defense)) for r in REENTRIES]
        lines.append(f"| {w} | " + " | ".join(cells) + " |")
    return lines


def main() -> None:
    px = load_etf_tr(["QQQ", "TQQQ", "BIL"])
    dtb3 = load_fred("DTB3")

    qqq_full = px["QQQ"].dropna()
    tqqq_actual = px["TQQQ"]
    bil_actual = px["BIL"]
    tqqq_first = str(tqqq_actual.dropna().index[0])
    bil_first = str(bil_actual.dropna().index[0])

    # ---- 합성 이음 (run_macro_overlay·run_exit_compare 와 동일) ----
    syn_t = synth_leveraged(px["QQQ"], dtb3)
    syn_b = synth_cash(dtb3, px.index.tolist())
    tqqq = tqqq_actual.copy()
    tqqq.loc[px.index < tqqq_first] = syn_t.loc[syn_t.index < tqqq_first]
    bil = bil_actual.copy()
    bil.loc[px.index < bil_first] = syn_b.loc[syn_b.index < bil_first]

    # ---- 신호: QQQ 총수익 수준 1999~ 전체로 MA 계산 후 백테스트 날짜로 자름 ----
    qqq_level_full = (1 + qqq_full).cumprod()
    qdates = qqq_full.index.tolist()
    start = qdates[max(WINDOWS)]  # 가장 긴 창 유효 이후, 전 전략 같은 시작일
    dates = [d for d in qdates if d >= start]
    states = {(w, r): rules.trend_state(
        qqq_level_full, w, r, confirm_days=CONFIRM_DAYS, band=BAND).reindex(dates)
        for w in WINDOWS for r in REENTRIES}
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
    s200 = states[(200, "instant")]
    weights["QQQ_MA200"] = pd.DataFrame({"TQQQ": z, "QQQ": s200, "BIL": 1.0 - s200},
                                        index=rets.index)
    for w in WINDOWS:
        for r in REENTRIES:
            for d in DEFENSES:
                weights[_grid_name(w, r, d)] = _weights_3(states[(w, r)], d)
    # D20 교차검증: M00_BIL(below+bil_overlay) == W200_instant_BIL(trend_state)
    below200 = _hold._below_on(dates, rules.below_ma(qqq_level_full, 200))
    m00 = rules.bil_overlay(below200, pd.Series(True, index=below200.index), 0.0, 1.0)
    ref = weights["W200_instant_BIL"]
    if not all(np.allclose(m00[c].to_numpy(dtype=float), ref[c].to_numpy(dtype=float))
               for c in ("TQQQ", "QQQ", "BIL")):
        raise ValueError("M00_BIL != W200_instant_BIL")
    weights["M00_BIL"] = m00

    names = BENCH + _grid_names()
    runs = {n: portfolio.run(weights[n], rets, COST) for n in names}

    sub2 = [d for d in dates if d[:6] >= "201002"]
    if not sub2:
        raise ValueError("empty actual subperiod")
    runs_s2 = {n: portfolio.run(weights[n].loc[sub2], rets.loc[sub2], COST) for n in names}

    full = {n: perf.summary(runs[n]["ret"]) for n in names}
    dot = {n: _trend._window_ret(runs[n]["ret"], dates, *DOTCOM) for n in names}
    gfc = {n: _trend._window_ret(runs[n]["ret"], dates, *GFC) for n in names}
    real = {n: perf.summary(runs_s2[n]["ret"])["cagr"] for n in names}
    tpy = {n: perf.trades_per_year(runs[n]["turnover"]) for n in names}
    rent = {n: _reentries(weights[n]["TQQQ"], dates) for n in names}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L: list[str] = [
        "# 재진입 강화 × 이동평균 길이 격자",
        "",
        f"- 생성: {now}",
        f"- 기간: {dates[0]}~{dates[-1]} (거래일 {len(dates)}일,"
        f" QQQ 수익 첫날 {qdates[0]} + {max(WINDOWS)}거래일부터, 전 전략 동일)",
        "- 합성: 3배 일간 리밸런싱 근사, 보수 연 0.95%, 차입비용 DTB3(연율 %),"
        f" 실제 TQQQ 상장 전만 합성(실제 첫 수익일 {tqqq_first}부터 실제);"
        f" BIL도 첫 수익일 {bil_first} 전 synth_cash(DTB3 일간 rate/100/252)",
        "- 신호: QQQ 총수익 수준 1999~ 전체로 이동평균 계산 후 백테스트 날짜로 절단,"
        " 당일 종가 판단·당일 종가 체결",
        "- confirm 20일·band 3%는 사전 고정 통상값, 전 조합 공개, 1등 선택 아님,"
        " 비용 0.1%, 세전",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, DTB3 {dtb3.index[-1]}",
        "- 전략: TQQQ 비중 s=trend_state 값, 나머지 1−s 전부 방어자산;"
        " 이탈은 level<ma 즉시 0, 복귀는 instant(당일)/confirm(연속 20일째)/"
        "band(ma×1.03 이상)/staged(첫날 0.5 뒤 연속 20일째 1.0);"
        " QQQ_MA200=200일 instant로 QQQ↔BIL(TQQQ 없음);"
        " M00_BIL=D20 방식(below+bil_overlay)으로 별도 계산, W200_instant_BIL과 일치 확인",
        "",
        "## 1. 전체 표 (기준 4개 + 40개)",
        f"닷컴={DOTCOM[0]}~{DOTCOM[1]}, 금융위기={GFC[0]}~{GFC[1]},"
        f" 실제구간={sub2[0]}~{sub2[-1]} ({len(sub2)}일, 구간 첫날 목표비중 그대로 재실행)."
        f" 닷컴재진입={REENTRY_WINDOW[0]}~{REENTRY_WINDOW[1]} 중"
        " TQQQ 비중 0→양수 전환 수(백테스트 시작 전 제외,"
        " QQQ·QQQ_MA200은 TQQQ 미보유라 0).",
        "| 전략 | CAGR | MDD | Sharpe | 닷컴 | 금융위기 | 실제CAGR | 연 매매 | 닷컴재진입 |",
        "|" + "---|" * 9,
    ]
    for n in names:
        s = full[n]
        L.append(f"| {n} | {_base._pct(s['cagr'])} | {_base._pct(s['mdd'])}"
                 f" | {_base._num(s['sharpe'])} | {_base._pct(dot[n])}"
                 f" | {_base._pct(gfc[n])} | {_base._pct(real[n])}"
                 f" | {_base._num(tpy[n], 1)} | {rent[n]} |")
    L.append("")

    L += ["## 2. 피벗 (행 window × 열 reentry)"]
    for d in DEFENSES:
        L += [f"### 방어 {d} — ① 전체 CAGR",
              *_pivot_lines(d, lambda n: _base._pct(full[n]["cagr"])),
              "",
              f"### 방어 {d} — ② 닷컴 수익",
              *_pivot_lines(d, lambda n: _base._pct(dot[n])),
              "",
              f"### 방어 {d} — ③ 실제 구간 CAGR",
              *_pivot_lines(d, lambda n: _base._pct(real[n])),
              "",
              f"### 방어 {d} — ④ 전체 MDD",
              *_pivot_lines(d, lambda n: _base._pct(full[n]["mdd"])),
              ""]

    L += ["## 3. 큰 낙폭 구간 수익 (방어 BIL 20개 + 기준)",
          ", ".join(f"{label}={s}~{e}" for label, s, e in DRAWDOWNS) + ".",
          "| 전략 | " + " | ".join(label for label, _, _ in DRAWDOWNS) + " |",
          "|" + "---|" * (len(DRAWDOWNS) + 1)]
    sec3 = BENCH + [_grid_name(w, r, "BIL") for w in WINDOWS for r in REENTRIES]
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
