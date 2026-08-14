"""상한가 체결을 제외한 1분봉 전략 대규모 탐색과 LLM overlay 점검.

전략 선택은 전반/후반 모두 양수인 조합만 검증 후보로 인정한다. 전체 조합은
JSON에 보존하고 문서에는 서로 다른 진입 규칙의 최종 후보 3개만 정리한다.
"""
from __future__ import annotations

import argparse
import itertools
import json
import statistics
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from research.watchlist_expected_return.five_minute_high_breakout import find_entry as five_bar_high_break
from research.watchlist_expected_return.phase4_holding_strategy import summarize_outcomes
from research.watchlist_expected_return.phase7_pullback_strategy import (
    DEFAULT_KRX_DB,
    DEFAULT_WATCHLIST_DB,
    load_research_rows,
)
from research.watchlist_expected_return.phase8_minute_pullback_strategy import (
    DEFAULT_OUTPUT_DIR,
    find_minute_entry,
    load_minute_samples,
    simulate_minute_exit,
)
from research.watchlist_expected_return.prior_low_reclaim import find_entry as prior_low_reclaim
from research.watchlist_expected_return.vwap_reclaim import find_entry as vwap_reclaim


ENTRY_RULES = (
    "prior_low_reclaim",
    "five_bar_high_break",
    "vwap_reclaim",
    "close_1519",
    "session_open_reclaim",
    "low_rebound_1pct",
    "low_rebound_2pct",
)
TP_VALUES = (0.03, 0.04, 0.05, 0.06, 0.08)
SL_VALUES = (0.03, 0.04, 0.05)
HOLD_DAYS = (1, 3)
COST_RATES = (0.005, 0.01)
MIN_PERIOD_COUNT = 10
PROVISIONAL_MIN_PERIOD_COUNT = 8


def tick_size(price: int) -> int:
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def infer_upper_limit(previous_close: int) -> int:
    """전일 종가의 130%를 최종 가격대 호가단위로 내림한다."""
    if previous_close <= 0:
        return 0
    raw = previous_close * 130 // 100
    tick = tick_size(raw)
    return raw // tick * tick


def is_upper_limit_entry(entry_price: float, previous_close: int) -> bool:
    upper = infer_upper_limit(previous_close)
    return bool(upper and entry_price >= upper)


def summarize_excess_returns(rows: list[dict[str, float]]) -> dict[str, Any]:
    values = [row["stock_return"] - row["index_return"] for row in rows]
    if not values:
        return {"count": 0, "mean_excess": None, "median_excess": None, "outperform_rate": None}
    return {
        "count": len(values),
        "mean_excess": round(statistics.mean(values), 6),
        "median_excess": round(statistics.median(values), 6),
        "outperform_rate": round(sum(value > 0 for value in values) / len(values), 4),
    }


def fetch_kospi_daily(count: int = 160) -> dict[str, float]:
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol=KOSPI&timeframe=day&count={count}&requestType=0"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(request, timeout=20).read().decode("euc-kr", errors="replace")
    root = ET.fromstring(raw)
    output = {}
    for item in root.findall(".//item"):
        values = item.attrib.get("data", "").split("|")
        if len(values) >= 5:
            output[values[0]] = float(values[4])
    return output


def candidate_strategies() -> list[dict[str, Any]]:
    output = []
    for entry_rule, tp, sl, days in itertools.product(
        ENTRY_RULES, TP_VALUES, SL_VALUES, HOLD_DAYS
    ):
        output.append({
            "id": f"{entry_rule}__tp{tp:.0%}_sl{sl:.0%}_d{days}",
            "entry_rule": entry_rule,
            "exit_rule": {"kind": "tp_sl", "tp": tp, "sl": sl, "days": days},
        })
    return output


def select_final_candidates(
    candidates: list[dict[str, Any]], limit: int = 3
) -> list[dict[str, Any]]:
    ordered = sorted(
        (item for item in candidates if item.get("validated")),
        key=lambda item: item.get("selection_score", float("-inf")),
        reverse=True,
    )
    return ordered[:limit]


def _find_entry(sample: dict[str, Any], rule: str) -> dict[str, Any] | None:
    day = [bar for bar in sample["bars"] if bar["date"] == sample["entry_date"]]
    prior_low = sample["prior_low"]
    if not day:
        return None
    if rule == "prior_low_reclaim":
        return prior_low_reclaim(day, prior_low)
    if rule == "five_bar_high_break":
        return five_bar_high_break(day, prior_low)
    if rule == "vwap_reclaim":
        return vwap_reclaim(day, prior_low)
    if rule == "low_rebound_1pct":
        return find_minute_entry(sample["bars"], sample["entry_date"], prior_low, "low_rebound_1pct")

    lower_low_seen = False
    previous_close = None
    session_open = day[0]["open"]
    running_low = float("inf")
    for bar in day:
        if bar["time"] > "151900" and rule == "close_1519":
            break
        lower_low_seen = lower_low_seen or bar["low"] < prior_low
        running_low = min(running_low, bar["low"])
        if rule == "close_1519" and bar["time"] == "151900":
            if lower_low_seen and bar["close"] > session_open:
                return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}
            return None
        if rule == "session_open_reclaim":
            crossed = previous_close is not None and previous_close <= session_open < bar["close"]
            if lower_low_seen and crossed:
                return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}
        if rule == "low_rebound_2pct":
            if lower_low_seen and bar["close"] > session_open and bar["close"] >= running_low * 1.02:
                return {"entry_price": bar["close"], "entry_timestamp": bar["timestamp"]}
        previous_close = bar["close"]
    return None


def compute_kospi_relative(
    samples: list[dict[str, Any]], index_close: dict[str, float], entry_rule: str
) -> dict[str, Any]:
    horizons: dict[str, Any] = {}
    for horizon in (1, 3):
        rows = []
        for sample in samples:
            entry = _find_entry(sample, entry_rule)
            if not entry or is_upper_limit_entry(entry["entry_price"], sample["previous_close"]):
                continue
            if len(sample["trading_dates"]) <= horizon:
                continue
            exit_date = sample["trading_dates"][horizon]
            exit_bars = [bar for bar in sample["bars"] if bar["date"] == exit_date]
            entry_date = sample["entry_date"]
            if not exit_bars or entry_date not in index_close or exit_date not in index_close:
                continue
            rows.append({
                "stock_return": exit_bars[-1]["close"] / entry["entry_price"] - 1,
                "index_return": index_close[exit_date] / index_close[entry_date] - 1,
            })
        horizons[f"d{horizon}_close"] = summarize_excess_returns(rows)
    return {
        "source": "Naver KOSPI daily close",
        "definition": "stock entry-to-D+n close return minus KOSPI entry-date-close-to-D+n-close return",
        **horizons,
    }


def attach_previous_close(
    samples: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    row_map = {(row["date"], row["ticker"]): row for row in rows}
    output = []
    for sample in samples:
        row = row_map.get((sample["watchlist_date"], sample["ticker"]))
        if not row:
            continue
        days = [*row["history"], *row["future"]]
        dates = [day["date"] for day in days]
        try:
            index = dates.index(sample["entry_date"])
        except ValueError:
            continue
        if index == 0 or not days[index - 1].get("close"):
            continue
        output.append({**sample, "previous_close": int(days[index - 1]["close"])})
    return output


def load_probability_scores(reports_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    output = {}
    for path in sorted(reports_dir.glob("watchlist_research_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        definition = str((doc.get("source_data") or {}).get("definition") or "")
        if "probability of D+1 open above D close" not in definition:
            continue
        for item in doc.get("items", []):
            date = str(item.get("date") or "").replace("-", "")
            ticker = str(item.get("ticker") or "")
            if date and ticker:
                output[(date, ticker)] = item
    return output


def _score_overlay(
    outcomes: list[dict[str, Any]], score_map: dict[tuple[str, str], dict[str, Any]], cost: float
) -> dict[str, Any]:
    matched = []
    for outcome in outcomes:
        score = score_map.get((outcome["watchlist_date"], outcome["ticker"]))
        if score:
            components = score.get("score_components") or {}
            matched.append({**outcome, "llm_score": score.get("score"),
                            "exhaustion_penalty": components.get("exhaustion_penalty")})
    variants = {
        "matched_baseline": matched,
        "score_ge_30": [row for row in matched if (row.get("llm_score") or 0) >= 30],
        "score_ge_40": [row for row in matched if (row.get("llm_score") or 0) >= 40],
        "exhaustion_le_10": [row for row in matched if row.get("exhaustion_penalty") is not None and row["exhaustion_penalty"] <= 10],
    }
    return {name: summarize_outcomes(rows, cost) for name, rows in variants.items()}


def analyze(
    samples: list[dict[str, Any]],
    score_map: dict[tuple[str, str], dict[str, Any]],
    cost_rate: float = 0.01,
) -> dict[str, Any]:
    dates = sorted({sample["entry_date"] for sample in samples})
    if len(dates) < 2:
        raise ValueError("시간 분할에 필요한 표본이 부족함")
    split_date = dates[len(dates) // 2]
    results = []
    all_upper_limit_exclusions: dict[tuple[str, str], dict[str, Any]] = {}

    for strategy in candidate_strategies():
        outcomes = []
        entry_count = upper_excluded = 0
        for sample in samples:
            entry = _find_entry(sample, strategy["entry_rule"])
            if not entry:
                continue
            entry_count += 1
            if is_upper_limit_entry(entry["entry_price"], sample["previous_close"]):
                upper_excluded += 1
                key = (sample["entry_date"], sample["ticker"])
                all_upper_limit_exclusions[key] = {
                    "entry_date": sample["entry_date"], "ticker": sample["ticker"],
                    "entry_rule": strategy["entry_rule"], "entry_price": entry["entry_price"],
                    "previous_close": sample["previous_close"],
                    "inferred_upper_limit": infer_upper_limit(sample["previous_close"]),
                }
                continue
            outcome = simulate_minute_exit(
                sample["bars"], entry, sample["trading_dates"], strategy["exit_rule"]
            )
            if outcome:
                outcomes.append({
                    **outcome,
                    "entry_date": sample["entry_date"],
                    "watchlist_date": sample["watchlist_date"],
                    "ticker": sample["ticker"],
                    "holding_days": strategy["exit_rule"]["days"],
                })
        early = [row for row in outcomes if row["entry_date"] < split_date]
        late = [row for row in outcomes if row["entry_date"] >= split_date]
        early_metrics = summarize_outcomes(early, cost_rate)
        late_metrics = summarize_outcomes(late, cost_rate)
        total_metrics = summarize_outcomes(outcomes, cost_rate)
        half_cost_early = summarize_outcomes(early, 0.005)
        half_cost_late = summarize_outcomes(late, 0.005)
        validated = bool(
            early_metrics["count"] >= MIN_PERIOD_COUNT
            and late_metrics["count"] >= MIN_PERIOD_COUNT
            and early_metrics["mean"] is not None and early_metrics["mean"] > 0
            and late_metrics["mean"] is not None and late_metrics["mean"] > 0
            and half_cost_early["mean"] is not None and half_cost_early["mean"] > 0
            and half_cost_late["mean"] is not None and half_cost_late["mean"] > 0
        )
        worst_period_mean = min(
            value for value in (early_metrics["mean"], late_metrics["mean"])
            if value is not None
        )
        results.append({
            **strategy,
            "entry_count_before_upper_limit_guard": entry_count,
            "upper_limit_excluded": upper_excluded,
            "outcomes": outcomes,
            "early": early_metrics,
            "late": late_metrics,
            "total": total_metrics,
            "validated": validated,
            "selection_score": worst_period_mean,
        })

    final = select_final_candidates(results, 3)
    if len(final) < 3:
        provisional = sorted(
            (item for item in results if item not in final and item["early"]["count"] >= PROVISIONAL_MIN_PERIOD_COUNT and item["late"]["count"] >= PROVISIONAL_MIN_PERIOD_COUNT),
            key=lambda item: item["selection_score"], reverse=True,
        )
        for item in provisional:
            final.append({**item, "provisional": True})
            if len(final) == 3:
                break

    final_summary = []
    for item in final:
        clean = {key: value for key, value in item.items() if key != "outcomes"}
        clean["llm_overlay"] = _score_overlay(item["outcomes"], score_map, cost_rate)
        final_summary.append(clean)

    return {
        "analysis_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_count": len(samples),
        "split_date": split_date,
        "strategy_attempt_count": len(results),
        "entry_rule_count": len(ENTRY_RULES),
        "exit_rule_count_per_entry": len(TP_VALUES) * len(SL_VALUES) * len(HOLD_DAYS),
        "cost_rates": list(COST_RATES),
        "upper_limit_policy": "entry_price >= inferred historical upper limit => unfillable/excluded",
        "upper_limit_excluded_unique": len(all_upper_limit_exclusions),
        "upper_limit_exclusions": sorted(all_upper_limit_exclusions.values(), key=lambda row: (row["entry_date"], row["ticker"])),
        "validated_count": sum(item["validated"] for item in results),
        "final_candidates": final_summary,
        "all_results": [{key: value for key, value in item.items() if key != "outcomes"} for item in results],
        "guardrails": [
            "D+1~D+5 중 최초 lower-low 발생일 전체에서 진입 규칙을 비교하며 특정 운영 신호 발생 종목으로 시작하지 않음",
            "상한가 추정 가격과 같거나 높은 진입은 체결 불가로 제외",
            "과거 DB에 권위 상한가가 없어 전일종가 130%를 호가단위로 내린 proxy를 사용하며 신규상장·권리락·기준가격 변경일은 한계",
            "같은 1분봉 TP·SL 동시 도달은 SL 우선",
            "전반부·후반부 모두 비용 1% 차감 후 평균 양수인 조합만 검증 후보",
            "LLM overlay는 현재 확률점수 정의가 저장된 날짜만 사용하며 과거 원인명확성 점수는 혼합하지 않음",
            "현재 LLM 점수는 가격요인을 포함하므로 순수 정성이 아닌 hybrid overlay로 해석",
        ],
    }


def _pct(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.2%}"


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# 상한가 체결 제외 전략 탐색 — 최종 후보 3개",
        "",
        f"- 실험 전략: {result['strategy_attempt_count']}개",
        f"- 공통 표본: {result['sample_count']}건",
        f"- 시간 분할: {result['split_date']}",
        f"- 상한가 체결 제외: {result['upper_limit_excluded_unique']}개 종목·일자",
        f"- 전후반 검증 통과 조합: {result['validated_count']}개",
        "",
        "## 최종 후보 3개",
        "",
    ]
    for index, item in enumerate(result["final_candidates"], start=1):
        lines.extend([
            f"### {index}. `{item['id']}`" + (" (잠정)" if item.get("provisional") else ""),
            f"- 진입: `{item['entry_rule']}`",
            f"- 청산: TP {_pct(item['exit_rule']['tp'])} / SL {_pct(item['exit_rule']['sl'])} / 최대 {item['exit_rule']['days']}거래일",
            f"- 전반부: {item['early']['count']}건, 평균 {_pct(item['early']['mean'])}, 승률 {_pct(item['early']['positive_rate'])}, MDD {_pct(item['early']['max_drawdown'])}",
            f"- 후반부: {item['late']['count']}건, 평균 {_pct(item['late']['mean'])}, 승률 {_pct(item['late']['positive_rate'])}, MDD {_pct(item['late']['max_drawdown'])}",
            f"- 전체: {item['total']['count']}건, 평균 {_pct(item['total']['mean'])}, 승률 {_pct(item['total']['positive_rate'])}",
            f"- 해당 규칙 상한가 제외: {item['upper_limit_excluded']}건",
            "- LLM overlay 표본: " + str(item["llm_overlay"]["matched_baseline"]["count"]),
            "",
        ])
    lines.extend(["## 코스피 대비 신호 성과", ""])
    rendered_rules = set()
    for item in result["final_candidates"]:
        rule = item["entry_rule"]
        if rule in rendered_rules:
            continue
        rendered_rules.add(rule)
        benchmark = result.get("kospi_relative_by_entry", {}).get(rule, {})
        d1, d3 = benchmark.get("d1_close", {}), benchmark.get("d3_close", {})
        lines.extend([
            f"- `{rule}` D+1: {d1.get('count', 0)}건, 평균 초과수익 {_pct(d1.get('mean_excess'))}, 코스피 상회율 {_pct(d1.get('outperform_rate'))}",
            f"- `{rule}` D+3: {d3.get('count', 0)}건, 평균 초과수익 {_pct(d3.get('mean_excess'))}, 코스피 상회율 {_pct(d3.get('outperform_rate'))}",
        ])
    lines.extend(["", "## LLM 정성판단 영향", ""])
    for item in result["final_candidates"]:
        lines.append(f"- `{item['entry_rule']}`")
        for name, metrics in item["llm_overlay"].items():
            lines.append(
                f"  - {name}: {metrics['count']}건, 평균 {_pct(metrics['mean'])}, 승률 {_pct(metrics['positive_rate'])}"
            )
    lines.extend(["", "## 체결·검증 원칙", ""])
    lines.extend(f"- {text}" for text in result["guardrails"])
    lines.extend([
        "",
        "## 결론",
        "",
        "- 사전 검증 기준 통과 조합이 없으면 전후반 최저 평균이 높은 3개를 잠정 후보로만 표시한다.",
        "- 이번 실행은 검증 통과 0개이며 코스피 초과수익도 음수이므로 운영 적용 후보는 없다.",
        "- 표시된 3개는 다음 독립 기간에서 재검증할 우선순위일 뿐 매수 규칙 확정안이 아니다.",
        "- LLM overlay는 안전한 현재 점수 구간 표본이 적으면 전략 확정 근거가 아니라 방향성 점검으로만 해석한다.",
        "- 전체 210개 조합의 상세 결과는 동명 JSON의 `all_results`에 보존한다.",
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="상한가 제외 전략 20개 이상 탐색")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_OUTPUT_DIR / "minute_cache")
    parser.add_argument("--reports-dir", type=Path, default=Path(r"C:\Users\mullu\.openclaw\workspace\reports"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    rows = load_research_rows(args.watchlist_db, args.krx_db)
    raw_samples, stats = load_minute_samples(rows, args.cache_dir)
    samples = attach_previous_close(raw_samples, rows)
    result = analyze(samples, load_probability_scores(args.reports_dir))
    result["data_stats"] = stats
    index_close = fetch_kospi_daily()
    result["kospi_relative_by_entry"] = {
        rule: compute_kospi_relative(samples, index_close, rule) for rule in ENTRY_RULES
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "phase11_upper_limit_strategy_search.json"
    md_path = args.output_dir / "phase11_upper_limit_strategy_search.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({
        "strategies": result["strategy_attempt_count"],
        "samples": result["sample_count"],
        "upper_limit_excluded": result["upper_limit_excluded_unique"],
        "validated": result["validated_count"],
        "final": [item["id"] for item in result["final_candidates"]],
        "json": str(json_path), "markdown": str(md_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
