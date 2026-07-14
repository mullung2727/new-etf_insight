"""3단계: D+1 시가 상승 종목의 구조화 특징 분석."""
from __future__ import annotations

import argparse
import json
import math
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
from research.watchlist_expected_return.phase2_score_relationship import (
    _pearson,
    _ranks,
    load_analysis_rows,
)


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "results"
HURDLES = (0.0, 0.005, 0.01)
FEATURES = (
    "ratio",
    "trading_value",
    "today_volume",
    "avg5_volume",
    "entry_close",
    "entry_volume",
    "market_cap",
    "intraday_rank",
    "source_count",
)


def _usable_evidence(value: str | None) -> bool:
    if not value:
        return False
    return not any(marker in value for marker in ("없음", "실패", "타임아웃"))


def _source_count(value: str | None) -> int:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def load_feature_rows(watchlist_db: Path, krx_db: Path) -> list[dict[str, Any]]:
    base_rows = load_analysis_rows(watchlist_db, krx_db)
    keys = {(row["date"], row["ticker"]) for row in base_rows}
    with closing(connect_sqlite_ro(watchlist_db)) as sql:
        score_details = {
            (str(row[0]), str(row[1])): {
                "today_volume": row[2],
                "avg5_volume": row[3],
                "source_count": _source_count(row[4]),
                "has_board_evidence": _usable_evidence(row[5]),
                "has_news_evidence": _usable_evidence(row[6]),
                "has_web_evidence": _usable_evidence(row[7]),
            }
            for row in sql.execute("""
                SELECT date, ticker, today_volume, avg5_volume, sources,
                       evidence_board, evidence_news, evidence_web
                FROM llm_scores
            """)
            if (str(row[0]), str(row[1])) in keys
        }
        ranks = {
            (str(row[0]), str(row[1])): row[2]
            for row in sql.execute("SELECT date, ticker, rank FROM intraday_ranking")
            if (str(row[0]), str(row[1])) in keys
        }

    with duckdb.connect(str(krx_db), read_only=True) as krx:
        entry_prices = load_ohlcv(
            krx,
            sorted({row["date"] for row in base_rows}),
            sorted({row["ticker"] for row in base_rows}),
        )

    enriched: list[dict[str, Any]] = []
    for row in base_rows:
        key = (row["date"], row["ticker"])
        entry = entry_prices.get(key, {})
        enriched.append({
            **row,
            **score_details.get(key, {}),
            "entry_volume": entry.get("volume"),
            "market_cap": entry.get("market_cap"),
            "intraday_rank": ranks.get(key),
        })
    return enriched


def _cliffs_delta(positive: list[float], negative: list[float]) -> float | None:
    if not positive or not negative:
        return None
    greater = sum(left > right for left in positive for right in negative)
    lower = sum(left < right for left in positive for right in negative)
    return round((greater - lower) / (len(positive) * len(negative)), 6)


def _feature_effect(rows: list[dict[str, Any]], feature: str, hurdle: float) -> dict[str, Any]:
    usable = [row for row in rows if row.get(feature) is not None]
    positive = [float(row[feature]) for row in usable if row["gap_return_d1_open"] > hurdle]
    negative = [float(row[feature]) for row in usable if row["gap_return_d1_open"] <= hurdle]
    values = [float(row[feature]) for row in usable]
    returns = [float(row["gap_return_d1_open"]) for row in usable]
    delta = _cliffs_delta(positive, negative)
    return {
        "available": len(usable),
        "missing": len(rows) - len(usable),
        "positive_count": len(positive),
        "non_positive_count": len(negative),
        "positive_median": round(statistics.median(positive), 6) if positive else None,
        "non_positive_median": round(statistics.median(negative), 6) if negative else None,
        "cliffs_delta": delta,
        "spearman_to_return": _pearson(_ranks(values), _ranks(returns)) if len(values) >= 2 else None,
    }


def _sign(value: float | None) -> int:
    if value is None or math.isclose(value, 0.0):
        return 0
    return 1 if value > 0 else -1


def analyze_features(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dates = sorted({row["date"] for row in rows})
    split_date = dates[len(dates) // 2] if dates else None
    early = [row for row in rows if split_date and row["date"] < split_date]
    late = [row for row in rows if split_date and row["date"] >= split_date]

    sensitivity: dict[str, dict[str, Any]] = {}
    for hurdle in HURDLES:
        key = f"return_gt_{hurdle:.3f}"
        effects: dict[str, Any] = {}
        for feature in FEATURES:
            overall = _feature_effect(rows, feature, hurdle)
            early_effect = _feature_effect(early, feature, hurdle)
            late_effect = _feature_effect(late, feature, hurdle)
            same_direction = (
                _sign(overall["cliffs_delta"]) != 0
                and _sign(overall["cliffs_delta"]) == _sign(early_effect["cliffs_delta"])
                == _sign(late_effect["cliffs_delta"])
            )
            missing_rate = overall["missing"] / len(rows) if rows else 1.0
            effects[feature] = {
                **overall,
                "early_cliffs_delta": early_effect["cliffs_delta"],
                "late_cliffs_delta": late_effect["cliffs_delta"],
                "same_direction_both_periods": same_direction,
                "candidate": bool(
                    same_direction
                    and overall["cliffs_delta"] is not None
                    and abs(overall["cliffs_delta"]) >= 0.15
                    and missing_rate <= 0.2
                ),
                "winner_direction": "higher" if _sign(overall["cliffs_delta"]) > 0 else "lower",
            }
        sensitivity[key] = {
            "hurdle": hurdle,
            "positive_count": sum(row["gap_return_d1_open"] > hurdle for row in rows),
            "positive_rate": round(sum(row["gap_return_d1_open"] > hurdle for row in rows) / len(rows), 4) if rows else None,
            "features": effects,
        }

    evidence_flags = ("has_board_evidence", "has_news_evidence", "has_web_evidence")
    evidence_summary = {}
    for flag in evidence_flags:
        present = [row["gap_return_d1_open"] for row in rows if row.get(flag)]
        absent = [row["gap_return_d1_open"] for row in rows if not row.get(flag)]
        evidence_summary[flag] = {
            "present_count": len(present),
            "present_positive_rate": round(sum(value > 0 for value in present) / len(present), 4) if present else None,
            "absent_count": len(absent),
            "absent_positive_rate": round(sum(value > 0 for value in absent) / len(absent), 4) if absent else None,
        }

    primary = sensitivity["return_gt_0.000"]
    return {
        "analysis_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_count": len(rows),
        "time_split": {"split_date": split_date, "early_count": len(early), "late_count": len(late)},
        "candidate_rule": "|Cliff's delta| >= 0.15, 결측 <= 20%, 전반부·후반부 방향 일치",
        "primary_candidates": [
            feature for feature, effect in primary["features"].items() if effect["candidate"]
        ],
        "sensitivity": sensitivity,
        "evidence_flags": evidence_summary,
        "guardrails": [
            "점수·근거 생성시각이 없어 텍스트 근거 특징은 탐색용으로만 사용",
            "후보 판정은 인과관계나 운영 투입 승인을 의미하지 않음",
            "0.5%·1.0% 문턱은 거래비용 확정값이 아니라 민감도 조건임",
        ],
    }


def render_markdown(result: dict[str, Any]) -> str:
    primary = result["sensitivity"]["return_gt_0.000"]
    lines = [
        "# Watchlist 기대수익 연구 — 3단계 상승 종목 특징 분석",
        "",
        f"- 분석 표본: {result['sample_count']}건",
        f"- 기간 분할: {result['time_split']}",
        f"- D+1 시가 상승 종목: {primary['positive_count']}건 ({primary['positive_rate']})",
        f"- 반복 특징 후보: {result['primary_candidates']}",
        "",
        "## 구조화 특징 비교",
        "",
        "| 특징 | 상승 중앙값 | 비상승 중앙값 | Cliff's delta | 전반부 | 후반부 | 반복 | 후보 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for feature, effect in primary["features"].items():
        lines.append(
            f"| {feature} | {effect['positive_median']} | {effect['non_positive_median']} | "
            f"{effect['cliffs_delta']} | {effect['early_cliffs_delta']} | {effect['late_cliffs_delta']} | "
            f"{effect['same_direction_both_periods']} | {effect['candidate']} |"
        )
    lines.extend(["", "## 수익 문턱 민감도", "", "| 문턱 | 양수 표본 | 비율 | 후보 |", "| ---: | ---: | ---: | --- |"])
    for data in result["sensitivity"].values():
        candidates = [feature for feature, effect in data["features"].items() if effect["candidate"]]
        lines.append(f"| {data['hurdle']} | {data['positive_count']} | {data['positive_rate']} | {candidates} |")
    lines.extend(["", "## 해석 제한", ""])
    lines.extend(f"- {item}" for item in result["guardrails"])
    return "\n".join(lines) + "\n"


def write_results(result: dict[str, Any], rows: list[dict[str, Any]], output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "phase3_rising_features.json"
    md_path = output_dir / "phase3_rising_features.md"
    rows_path = output_dir / "phase3_feature_rows.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    rows_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path, md_path, rows_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="D+1 시가 상승 watchlist 종목 특징 분석")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    rows = load_feature_rows(args.watchlist_db, args.krx_db)
    result = analyze_features(rows)
    for path in write_results(result, rows, args.output_dir):
        print(f"[phase3] {path}")


if __name__ == "__main__":
    main()
