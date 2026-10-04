"""200일선 아래 TQQQ 피신 비교 — 1999~ 합성 이음 (D20).

cd etl && PYTHONPATH=.. uv run python ../research/us_oas/run_exit_compare.py
stdout 마크다운 표 + RESULTS_EXIT_COMPARE.md 저장.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.backtest_daily import perf, portfolio
from research.backtest_daily.data_us import (
    load_etf_tr, load_fred, load_index, synth_cash, synth_leveraged,
)
from research.us_oas import macro_signals as ms
from research.us_oas import rules
from research.us_oas import run_hold as _hold
from research.us_oas import run_tqqq_oas as _base
from research.us_oas import run_trend_grid as _trend

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

COST = 0.001
MA_WINDOW = _hold.MA_WINDOW
DXY_N = 63
DXY_TH = 0.05  # 사전 고정 (run_macro_overlay 와 동일)
RESULTS = Path(__file__).resolve().parent / "RESULTS_EXIT_COMPARE.md"
MS = (0.4, 0.2, 0.0)
NAMES = (["TQQQ", "QQQ", "QQQ_MA"]
         + [f"M{int(round(m * 100)):02d}_{d}" for m in MS for d in ("QQQ", "HALF", "BIL")]
         + ["M00_MACRO"])
YNAMES = ["TQQQ", "QQQ", "QQQ_MA", "M40_QQQ", "M00_QQQ", "M00_BIL", "M00_MACRO"]
DRAWDOWNS = [("닷컴", "20000327", "20021009"),
             ("금융위기", "20071031", "20090309"),
             ("2020", "20200219", "20200320"),
             ("2022", "20211119", "20221228"),
             ("2025", "20241216", "20250408")]


def _build_weights(below: pd.Series, dxy_flag: pd.Series,
                   curve_flag: pd.Series) -> dict[str, pd.DataFrame]:
    """13개 전략 목표비중. 위면 TQQQ 1.0, 아래면 m + 나머지는 QQQ/BIL."""
    idx = below.index
    z = pd.Series(0.0, index=idx)
    o = pd.Series(1.0, index=idx)
    fall = pd.Series(False, index=idx)
    tall = pd.Series(True, index=idx)
    d = dxy_flag.fillna(False).astype(bool)
    c = curve_flag.fillna(False).astype(bool)
    w: dict[str, pd.DataFrame] = {
        "TQQQ": pd.DataFrame({"TQQQ": o, "QQQ": z, "BIL": z}, index=idx),
        "QQQ": pd.DataFrame({"TQQQ": z, "QQQ": o, "BIL": z}, index=idx),
    }
    bm = below.fillna(False).astype(bool).to_numpy(dtype=bool)
    w["QQQ_MA"] = pd.DataFrame(
        {"TQQQ": np.zeros(len(idx)),
         "QQQ": np.where(bm, 0.0, 1.0),
         "BIL": np.where(bm, 1.0, 0.0)}, index=idx)
    for m in MS:
        tag = f"M{int(round(m * 100)):02d}"
        w[f"{tag}_QQQ"] = rules.bil_overlay(below, fall, m, 0.0)
        w[f"{tag}_HALF"] = rules.bil_overlay(below, tall, m, 0.5)
        w[f"{tag}_BIL"] = rules.bil_overlay(below, tall, m, 1.0)
    w["M00_MACRO"] = rules.bil_overlay(below, (d | c), 0.0, 1.0)
    return w


def _whipsaw(below: pd.Series, dates: list[str]) -> tuple[int, float, int, int, str, str]:
    """진입 횟수·연평균·헛신호(20거래일 내 복귀)·닷컴 복귀후 재진입 횟수."""
    b = below.fillna(False).astype(bool).to_numpy(dtype=bool)
    entries = [i for i in range(1, len(b)) if b[i] and not b[i - 1]]
    years = len(dates) / 252
    per_year = len(entries) / years if years else float("nan")
    whip = sum(1 for i in entries
               if any(not b[j] for j in range(i + 1, min(i + 21, len(b)))))
    sub = [i for i, d in enumerate(dates) if "2000" <= d[:4] <= "2002"]
    exits = [i for i in sub if i > 0 and not b[i] and b[i - 1]]
    entries_sub = {i for i in sub if i > 0 and b[i] and not b[i - 1]}
    reenter = sum(1 for e in exits if any(n > e for n in entries_sub))
    span = (dates[sub[0]], dates[sub[-1]]) if sub else ("-", "-")
    return len(entries), per_year, whip, reenter, span[0], span[1]


def main() -> None:
    px = load_etf_tr(["QQQ", "TQQQ", "BIL"])
    dtb3 = load_fred("DTB3")
    dgs10 = load_fred("DGS10")
    dxy = load_index("DX-Y.NYB")

    qqq_full = px["QQQ"].dropna()
    tqqq_actual = px["TQQQ"]
    bil_actual = px["BIL"]
    tqqq_first = str(tqqq_actual.dropna().index[0])
    bil_first = str(bil_actual.dropna().index[0])

    # ---- 합성 이음 (run_macro_overlay 와 동일: 실제 첫 수익일 전만 합성) ----
    syn_t = synth_leveraged(px["QQQ"], dtb3)
    syn_b = synth_cash(dtb3, px.index.tolist())
    tqqq = tqqq_actual.copy()
    tqqq.loc[px.index < tqqq_first] = syn_t.loc[syn_t.index < tqqq_first]
    bil = bil_actual.copy()
    bil.loc[px.index < bil_first] = syn_b.loc[syn_b.index < bil_first]

    # ---- 백테스트 기간·신호 (run_macro_overlay 와 동일) ----
    qdates = qqq_full.index.tolist()
    start = qdates[200]  # QQQ 수익 첫날 + 200거래일 (200일선 유효)
    dates = [d for d in qdates if d >= start]
    qqq_level_full = (1 + qqq_full).cumprod()
    below = _hold._below_on(dates, rules.below_ma(qqq_level_full, MA_WINDOW))
    dxy_flag = (ms.pct_change_n(dxy, dates, DXY_N) >= DXY_TH).astype(bool)
    curve_flag = ms.curve(dgs10, dtb3, dates)["disinvert"].astype(bool)

    rets = pd.DataFrame({"TQQQ": tqqq.reindex(dates),
                         "QQQ": qqq_full.reindex(dates),
                         "BIL": bil.reindex(dates)})
    if bool(rets.isna().any().any()):
        raise ValueError("NaN return in period")
    weights = _build_weights(below, dxy_flag, curve_flag)
    runs = {n: portfolio.run(weights[n], rets, COST) for n in NAMES}

    sub1 = [d for d in dates if "199912" <= d[:6] <= "200912"]
    sub2 = [d for d in dates if "201002" <= d[:6] <= "202610"]
    if not sub1 or not sub2:
        raise ValueError("empty subperiod")
    runs_s1 = {n: portfolio.run(weights[n].loc[sub1], rets.loc[sub1], COST) for n in NAMES}
    runs_s2 = {n: portfolio.run(weights[n].loc[sub2], rets.loc[sub2], COST) for n in NAMES}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L: list[str] = [
        "# 200일선 아래 TQQQ 피신 비교",
        "",
        f"- 생성: {now}",
        f"- 기간: {dates[0]}~{dates[-1]} (거래일 {len(dates)}일,"
        f" QQQ 수익 첫날 {qdates[0]} + 200거래일부터)",
        "- 합성: 3배 일간 리밸런싱 근사, 보수 연 0.95%, 차입비용 DTB3(연율 %),"
        f" 실제 TQQQ 상장 전만 합성(실제 첫 수익일 {tqqq_first}부터 실제);"
        f" BIL도 첫 수익일 {bil_first} 전 synth_cash(DTB3 일간 rate/100/252)",
        "- 신호 시점: below=당일 QQQ 종가 200일선(이전 실험들과 동일);"
        " dxy·curve는 관측일<T 값만(macro_signals, ≤T−1)",
        "- 200일선 길이 고정, 비용 0.1%, 세전, 매크로 임계 사전 고정"
        "(DXY 63일 +5%·금리차 역전해소)",
        f"- 데이터 마지막 날짜: ETF {px.index[-1]}, DTB3 {dtb3.index[-1]},"
        f" DGS10 {dgs10.index[-1]}, DXY {dxy.index[-1]}",
        "- 전략: 200일선 위면 TQQQ 100%, 아래면 TQQQ m + 나머지;"
        " M*_QQQ=나머지 QQQ, M*_HALF=나머지 절반 BIL, M*_BIL=나머지 전부 BIL;"
        " M00_MACRO=dxy·curve 발동일에만 나머지 전부 BIL;"
        " QQQ_MA=아래에서 QQQ→BIL 100% (TQQQ 없음)",
        "",
        "## 1. 전체 기간 요약",
        "| 전략 | CAGR | MDD | Sharpe | Calmar | 최종자산 | 평균 TQQQ | 평균 BIL | 연 매매 |",
        "|" + "---|" * 9,
    ]
    for n in NAMES:
        s = perf.summary(runs[n]["ret"])
        avg_t = float(weights[n]["TQQQ"].mean())
        avg_b = float(weights[n]["BIL"].mean())
        tpy = perf.trades_per_year(runs[n]["turnover"])
        L.append(f"| {n} | {_base._pct(s['cagr'])} | {_base._pct(s['mdd'])}"
                 f" | {_base._num(s['sharpe'])} | {_base._num(s['calmar'])}"
                 f" | {_base._num(s['final'])} | {_base._pct(avg_t)}"
                 f" | {_base._pct(avg_b)} | {_base._num(tpy, 1)} |")
    L.append("")

    L += ["## 2. 구간별 요약 (CAGR·MDD)",
          f"합성구간: {sub1[0]}~{sub1[-1]} ({len(sub1)}일, TQQQ 합성),"
          f" 실제구간: {sub2[0]}~{sub2[-1]} ({len(sub2)}일, 실제 TQQQ)."
          " 각 구간 첫날 목표비중 그대로 portfolio.run 재실행.",
          "| 전략 | 합성 CAGR | 합성 MDD | 실제 CAGR | 실제 MDD |",
          "|" + "---|" * 5]
    for n in NAMES:
        a, b = perf.summary(runs_s1[n]["ret"]), perf.summary(runs_s2[n]["ret"])
        L.append(f"| {n} | {_base._pct(a['cagr'])} | {_base._pct(a['mdd'])}"
                 f" | {_base._pct(b['cagr'])} | {_base._pct(b['mdd'])} |")
    L.append("")

    L += ["## 3. 큰 낙폭 구간 수익",
          ", ".join(f"{label}={s}~{e}" for label, s, e in DRAWDOWNS) + ".",
          "| 전략 | " + " | ".join(label for label, _, _ in DRAWDOWNS) + " |",
          "|" + "---|" * (len(DRAWDOWNS) + 1)]
    for n in NAMES:
        cells = [_base._pct(_trend._window_ret(runs[n]["ret"], dates, s, e))
                 for _, s, e in DRAWDOWNS]
        L.append(f"| {n} | {' | '.join(cells)} |")
    L.append("")

    dot = {n: _trend._window_ret(runs[n]["ret"], dates, *DRAWDOWNS[0][1:]) for n in NAMES}
    gfc = {n: _trend._window_ret(runs[n]["ret"], dates, *DRAWDOWNS[1][1:]) for n in NAMES}
    crisis = {n: ((1 + dot[n]) * (1 + gfc[n]) - 1
                     if pd.notna(dot[n]) and pd.notna(gfc[n]) else float("nan"))
              for n in NAMES}
    real_cagr = {n: perf.summary(runs_s2[n]["ret"])["cagr"] for n in NAMES}
    L += ["## 4. 교환표 (위기 방어 vs 상승장 수익)",
          "위기 방어=(1+닷컴수익)×(1+금융위기수익)−1, 상승장=실제 구간 CAGR."
          " 차이 열은 TQQQ 대비 pp.",
          "| 전략 | 위기 방어 | 실제 CAGR | 위기−TQQQ | 실제−TQQQ |",
          "|" + "---|" * 5]
    for n in NAMES:
        L.append(f"| {n} | {_base._pct(crisis[n])} | {_base._pct(real_cagr[n])}"
                 f" | {_base._pct(crisis[n] - crisis['TQQQ'])}"
                 f" | {_base._pct(real_cagr[n] - real_cagr['TQQQ'])} |")
    L.append("")

    n_entry, per_year, n_whip, n_re, d0, d1 = _whipsaw(below, dates)
    L += ["## 5. 200일선 들락날락",
          "진입=False→True 전환 수(첫날 아래 시작은 제외)."
          " 헛신호=진입 후 20거래일 안에 다시 위로 올라간 횟수."
          f" 닷컴 복귀후 재진입={d0}~{d1} 구간에서 위로 복귀했다가 다시 내려간 횟수"
          "(구간 내 복귀 중 이후 재진입이 있는 것).",
          "| 항목 | 값 |",
          "|---|---|",
          f"| 아래 진입 횟수 | {n_entry} |",
          f"| 연평균 진입 | {_base._num(per_year, 1)} |",
          f"| 헛신호 (20거래일 내 복귀) | {n_whip} |",
          f"| 닷컴 복귀후 재진입 | {n_re} |",
          ""]

    L += ["## 6. 연도별 수익",
          "| 연도 | " + " | ".join(YNAMES) + " |",
          "|" + "---|" * (len(YNAMES) + 1)]
    y = pd.concat({n: perf.yearly(runs[n]["ret"]) for n in YNAMES}, axis=1).sort_index()
    for yr in y.index:
        L.append("| " + str(yr) + " | "
                 + " | ".join(_base._pct(y.loc[yr, n]) for n in YNAMES) + " |")
    L.append("")

    text = "\n".join(L) + "\n"
    RESULTS.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
