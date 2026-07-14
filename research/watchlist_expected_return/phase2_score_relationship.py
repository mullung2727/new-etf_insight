"""2단계: 기존 원인 명확성 점수와 실제 미래 수익률의 관계 분석."""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from research.watchlist_expected_return.phase1_data_audit import (
    DEFAULT_KRX_DB,
    DEFAULT_WATCHLIST_DB,
    connect_sqlite_ro,
    load_ohlcv,
)


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "results"
SCORE_BANDS = ((0, 39), (40, 59), (60, 79), (80, 100))


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _bootstrap_mean_ci(values: list[float], samples: int = 2000, seed: int = 20260714) -> list[float | None]:
    if not values:
        return [None, None]
    rng = random.Random(seed)
    means = [statistics.fmean(rng.choices(values, k=len(values))) for _ in range(samples)]
    return [round(_quantile(means, 0.025) or 0.0, 6), round(_quantile(means, 0.975) or 0.0, 6)]


def summarize_returns(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0, "mean": None, "mean_ci95": [None, None], "median": None,
            "positive_rate": None, "p10": None, "p90": None, "stddev": None,
        }
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 6),
        "mean_ci95": _bootstrap_mean_ci(values),
        "median": round(statistics.median(values), 6),
        "positive_rate": round(sum(value > 0 for value in values) / len(values), 4),
        "p10": round(_quantile(values, 0.1) or 0.0, 6),
        "p90": round(_quantile(values, 0.9) or 0.0, 6),
        "stddev": round(statistics.stdev(values), 6) if len(values) > 1 else 0.0,
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys)
    )
    return round(numerator / denominator, 6) if denominator else None


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index
        while end + 1 < len(indexed) and indexed[end + 1][1] == indexed[index][1]:
            end += 1
        rank = (index + end) / 2 + 1
        for position in range(index, end + 1):
            ranks[indexed[position][0]] = rank
        index = end + 1
    return ranks


def correlations(rows: list[dict[str, Any]], return_key: str) -> dict[str, float | int | None]:
    pairs = [(float(row["score"]), float(row[return_key])) for row in rows if row.get(return_key) is not None]
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    return {
        "count": len(pairs),
        "pearson": _pearson(xs, ys),
        "spearman": _pearson(_ranks(xs), _ranks(ys)),
    }


def load_analysis_rows(watchlist_db: Path, krx_db: Path) -> list[dict[str, Any]]:
    with closing(connect_sqlite_ro(watchlist_db)) as sql:
        source_rows = [dict(row) for row in sql.execute("""
            SELECT w.date, w.stock_code AS ticker, s.score, s.category,
                   s.ratio, s.trading_value
            FROM watchlist w
            JOIN llm_scores s ON s.date = w.date AND s.ticker = w.stock_code
            WHERE s.score IS NOT NULL
            ORDER BY w.date, w.stock_code
        """)]

    with duckdb.connect(str(krx_db), read_only=True) as krx:
        trading_dates = [str(row[0]) for row in krx.execute(
            "SELECT DISTINCT date FROM ohlcv ORDER BY date"
        ).fetchall()]
        date_index = {date: index for index, date in enumerate(trading_dates)}
        required_dates: set[str] = set()
        for row in source_rows:
            date = row["date"]
            required_dates.add(date)
            if date in date_index:
                start = date_index[date] + 1
                required_dates.update(trading_dates[start:start + 5])
        prices = load_ohlcv(
            krx,
            sorted(required_dates),
            sorted({row["ticker"] for row in source_rows}),
        )

    analysis: list[dict[str, Any]] = []
    for row in source_rows:
        date = row["date"]
        ticker = row["ticker"]
        index = date_index.get(date)
        entry = prices.get((date, ticker))
        if index is None or not entry or not entry.get("close") or index + 1 >= len(trading_dates):
            continue
        next_date = trading_dates[index + 1]
        next_row = prices.get((next_date, ticker))
        if not next_row or next_row.get("open") is None:
            continue
        item = {
            **row,
            "entry_close": entry["close"],
            "d_plus_1_date": next_date,
            "gap_return_d1_open": next_row["open"] / entry["close"] - 1,
        }
        for horizon in range(1, 6):
            future_index = index + horizon
            key = f"return_d{horizon}_close"
            if future_index >= len(trading_dates):
                item[key] = None
                continue
            future = prices.get((trading_dates[future_index], ticker))
            item[key] = (
                future["close"] / entry["close"] - 1
                if future and future.get("close") is not None else None
            )
        analysis.append(item)
    return analysis


def analyze_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return_keys = ["gap_return_d1_open", *[f"return_d{horizon}_close" for horizon in range(1, 6)]]
    score_bands: dict[str, dict[str, Any]] = {}
    for low, high in SCORE_BANDS:
        label = f"{low}-{high}"
        values = [row["gap_return_d1_open"] for row in rows if low <= row["score"] <= high]
        score_bands[label] = summarize_returns(values)

    by_category: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_category[row.get("category") or "미분류"].append(row["gap_return_d1_open"])

    return {
        "analysis_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "primary_target": "D일 종가 대비 D+1 시가 수익률",
        "sample_count": len(rows),
        "overall": {key: summarize_returns([row[key] for row in rows if row.get(key) is not None]) for key in return_keys},
        "correlations": {key: correlations(rows, key) for key in return_keys},
        "score_bands_d1_open": score_bands,
        "categories_d1_open": {
            category: summarize_returns(values)
            for category, values in sorted(by_category.items())
        },
        "interpretation_guardrails": [
            "표본 수가 작아 인과관계나 운영 임계값을 확정하지 않음",
            "점수 생성시각과 근거시각이 없어 텍스트 근거의 미래정보 누출 여부는 검증하지 못함",
            "상관계수와 구간 평균은 탐색 결과이며 다음 단계 특징 후보 선정에만 사용함",
        ],
    }


def render_markdown(result: dict[str, Any]) -> str:
    primary = result["overall"]["gap_return_d1_open"]
    corr = result["correlations"]["gap_return_d1_open"]
    lines = [
        "# Watchlist 기대수익 연구 — 2단계 기존 점수 관계 분석",
        "",
        f"- 분석 표본: {result['sample_count']}건",
        f"- D+1 시가 평균수익률: {primary['mean']}",
        f"- D+1 시가 중앙수익률: {primary['median']}",
        f"- D+1 시가 상승 비율: {primary['positive_rate']}",
        f"- Pearson 상관: {corr['pearson']}",
        f"- Spearman 상관: {corr['spearman']}",
        "",
        "## 점수 구간별 D+1 시가 수익률",
        "",
        "| 점수 | 표본 | 평균 | 95% CI | 중앙값 | 상승 비율 |",
        "| --- | ---: | ---: | --- | ---: | ---: |",
    ]
    for band, values in result["score_bands_d1_open"].items():
        lines.append(
            f"| {band} | {values['count']} | {values['mean']} | {values['mean_ci95']} | "
            f"{values['median']} | {values['positive_rate']} |"
        )
    lines.extend(["", "## 보유기간별 전체 성과", "", "| 기준 | 표본 | 평균 | 중앙값 | 상승 비율 |", "| --- | ---: | ---: | ---: | ---: |"])
    for key, values in result["overall"].items():
        lines.append(f"| {key} | {values['count']} | {values['mean']} | {values['median']} | {values['positive_rate']} |")
    lines.extend(["", "## 해석 제한", ""])
    lines.extend(f"- {item}" for item in result["interpretation_guardrails"])
    return "\n".join(lines) + "\n"


def write_results(result: dict[str, Any], rows: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "phase2_score_relationship.json"
    md_path = output_dir / "phase2_score_relationship.md"
    rows_path = output_dir / "phase2_analysis_rows.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    rows_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path, md_path, rows_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="기존 watchlist 점수와 미래 수익률 관계 분석")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    rows = load_analysis_rows(args.watchlist_db, args.krx_db)
    result = analyze_rows(rows)
    for path in write_results(result, rows, args.output_dir):
        print(f"[phase2] {path}")


if __name__ == "__main__":
    main()
