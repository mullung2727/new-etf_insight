"""6단계: D일 15:00 이전 정보만으로 극단적 D+1 갭 원인의 구분 가능성을 평가한다."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import duckdb

from research.watchlist_expected_return.watchlist_probability_langgraph import (
    DEFAULT_KRX_DB,
    DEFAULT_TELEGRAM_DB,
    DEFAULT_WATCHLIST_DB,
    fetch_historical_news,
)


ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "etl" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from new_etf_insight.llm import generate_json  # noqa: E402


SEOUL = ZoneInfo("Asia/Seoul")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "results"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "extreme_gap_cause.md"
SCHEMA_PATH = Path(__file__).resolve().with_name("extreme_gap_cause_schema.json")


def as_of(date: str) -> dt.datetime:
    return dt.datetime.strptime(date + "150000", "%Y%m%d%H%M%S").replace(tzinfo=SEOUL)


def load_extreme_cases(
    watchlist_db: Path,
    krx_db: Path,
    count: int = 15,
) -> list[dict]:
    with closing(sqlite3.connect(f"file:{watchlist_db}?mode=ro", uri=True)) as sql:
        sql.row_factory = sqlite3.Row
        watchlist = [dict(row) for row in sql.execute(
            "SELECT date, stock_code AS ticker FROM watchlist ORDER BY date, stock_code"
        )]

    cases = []
    with duckdb.connect(str(krx_db), read_only=True) as krx:
        names = dict(krx.execute("SELECT code, name FROM stock_names").fetchall())
        for row in watchlist:
            outcome = krx.execute("""
                SELECT next_day.date, current.close, next_day.open
                FROM ohlcv current
                JOIN ohlcv next_day
                  ON next_day.ticker=current.ticker
                 AND next_day.date=(
                     SELECT MIN(date) FROM ohlcv
                     WHERE ticker=current.ticker AND date>current.date
                 )
                WHERE current.date=? AND current.ticker=?
            """, [row["date"], row["ticker"]]).fetchone()
            if not outcome or not outcome[1] or outcome[2] is None:
                continue
            next_date, close, next_open = outcome
            cases.append({
                "case_id": f"{row['date']}_{row['ticker']}",
                "date": row["date"],
                "ticker": row["ticker"],
                "name": names.get(row["ticker"], row["ticker"]),
                "next_date": next_date,
                "d_close": close,
                "d1_open": next_open,
                "d1_open_return_pct": round((next_open / close - 1) * 100, 4),
            })
    ordered = sorted(cases, key=lambda row: row["d1_open_return_pct"], reverse=True)
    selected = ordered[:count] + list(reversed(ordered[-count:]))
    for index, case in enumerate(selected):
        case["outcome_group"] = "up" if index < count else "down"
    return selected


def load_telegram_context(db_path: Path, ticker: str, date: str) -> list[dict]:
    target = dt.datetime.strptime(date, "%Y%m%d").date()
    target_text = target.isoformat()
    lower = (target - dt.timedelta(days=7)).isoformat()
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT date_kst, session, mention_channels, discovery_reason, analysis
            FROM telegram_stock_insights
            WHERE ticker=? AND date_kst BETWEEN ? AND ?
              AND (date_kst<? OR session='morning')
            ORDER BY date_kst, CASE session WHEN 'morning' THEN 0 WHEN 'close' THEN 1 ELSE 2 END
        """, [ticker, lower, target_text, target_text]).fetchall()
    return [{
        "date": row["date_kst"],
        "session": row["session"],
        "channels": json.loads(row["mention_channels"] or "[]"),
        "discovery_reason": row["discovery_reason"],
        "analysis": json.loads(row["analysis"]) if row["analysis"] else None,
    } for row in rows]


def build_blind_inputs(cases: list[dict], telegram_db: Path) -> list[dict]:
    inputs = []
    for case in cases:
        cutoff = as_of(case["date"])
        try:
            news = fetch_historical_news(case["name"], case["ticker"], cutoff)
        except Exception as exc:
            news = []
            news_warning = type(exc).__name__
        else:
            news_warning = None
        inputs.append({
            "case_id": case["case_id"],
            "as_of": cutoff.isoformat(),
            "ticker": case["ticker"],
            "name": case["name"],
            "news": news,
            "telegram": load_telegram_context(telegram_db, case["ticker"], case["date"]),
            "market_snapshot": None,
            "limitations": [
                "15:00 과거 시세 스냅샷 없음",
                "시각이 없는 기존 llm_scores 근거 제외",
                *( [f"news_fetch_failed:{news_warning}"] if news_warning else [] ),
            ],
        })
    return inputs


Classifier = Callable[[str], str]


def classify_blind_inputs(inputs: list[dict], classifier: Classifier | None = None) -> list[dict]:
    prompt = PROMPT_PATH.read_text(encoding="utf-8").format(
        input_json=json.dumps(inputs, ensure_ascii=False, indent=2)
    )
    generator = classifier or (
        lambda value: generate_json(value, output_schema_path=SCHEMA_PATH, search=False)
    )
    output = json.loads(generator(prompt))["cases"]
    expected = {case["case_id"] for case in inputs}
    actual = {case["case_id"] for case in output}
    if actual != expected or len(output) != len(inputs):
        raise ValueError("blind classification case mismatch")
    return output


def evaluate(cases: list[dict], classifications: list[dict]) -> dict:
    by_id = {row["case_id"]: row for row in classifications}
    rows = []
    for case in cases:
        classification = by_id[case["case_id"]]
        signal_matches = (
            classification["direction_bias"] == "positive"
            if case["outcome_group"] == "up"
            else classification["direction_bias"] == "negative"
            or classification["downside_risk"] == "high"
            or classification["priced_in_risk"] == "high"
        )
        rows.append({**case, **classification, "pre_cutoff_signal_matches": signal_matches})

    def group_summary(group: str) -> dict:
        group_rows = [row for row in rows if row["outcome_group"] == group]
        count = len(group_rows)
        rate = lambda matched: round(sum(matched) / count, 4) if count else None
        return {
            "count": count,
            "clear_or_partial_cause_count": sum(
                row["identifiable"] != "none" for row in group_rows
            ),
            "positive_bias_rate": rate(row["direction_bias"] == "positive" for row in group_rows),
            "negative_bias_rate": rate(row["direction_bias"] == "negative" for row in group_rows),
            "high_priced_in_risk_rate": rate(row["priced_in_risk"] == "high" for row in group_rows),
            "high_downside_risk_rate": rate(row["downside_risk"] == "high" for row in group_rows),
            "signal_match_count": sum(row["pre_cutoff_signal_matches"] for row in group_rows),
            "signal_match_rate": rate(row["pre_cutoff_signal_matches"] for row in group_rows),
            "cause_types": dict(Counter(row["cause_type"] for row in group_rows)),
        }

    up = group_summary("up")
    down = group_summary("down")
    positive_gap = (
        round(up["positive_bias_rate"] - down["positive_bias_rate"], 4)
        if up["positive_bias_rate"] is not None and down["positive_bias_rate"] is not None
        else None
    )
    downside_gap = (
        round(down["high_downside_risk_rate"] - up["high_downside_risk_rate"], 4)
        if down["high_downside_risk_rate"] is not None and up["high_downside_risk_rate"] is not None
        else None
    )
    return {
        "cutoff": "D일 15:00 KST",
        "blindness": "분류 후 D+1 결과 결합",
        "up": up,
        "down": down,
        "separation": {
            "positive_bias_rate_gap_up_minus_down": positive_gap,
            "high_downside_risk_rate_gap_down_minus_up": downside_gap,
            "assessment": "weak",
        },
        "rows": rows,
    }


def render_markdown(result: dict) -> str:
    lines = [
        "# Watchlist 극단적 D+1 시가 갭 원인 분석",
        "",
        "- 기준 시각: D일 15:00 KST",
        "- 분류 입력에는 D+1 가격·수익률·상승/하락 그룹을 포함하지 않음",
        "- 15:00 과거 시세 스냅샷과 시각이 없는 기존 근거는 제외",
        f"- 상승군 사전 신호 일치: {result['up']['signal_match_count']}/{result['up']['count']}",
        f"- 하락군 사전 신호 일치: {result['down']['signal_match_count']}/{result['down']['count']}",
        f"- 긍정 방향 비율: 상승군 {result['up']['positive_bias_rate']}, 하락군 {result['down']['positive_bias_rate']}",
        f"- 고위험 비율: 상승군 {result['up']['high_downside_risk_rate']}, 하락군 {result['down']['high_downside_risk_rate']}",
        f"- 상승·하락 구분력 판정: {result['separation']['assessment']}",
        "",
        "| 그룹 | D일 | 종목 | D+1 시가 | 구분 | 원인 유형 | 방향 | 선반영 | 하락위험 |",
        "| --- | --- | --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['outcome_group']} | {row['date']} | {row['name']}({row['ticker']}) | "
            f"{row['d1_open_return_pct']:+.2f}% | {row['identifiable']} | {row['cause_type']} | "
            f"{row['direction_bias']} | {row['priced_in_risk']} | {row['downside_risk']} |"
        )
    lines.extend(["", "## 판정 근거", ""])
    for row in result["rows"]:
        lines.append(f"- **{row['date']} {row['name']}**: {row['evidence_summary']} — {row['reasoning']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="15:00 blind 극단 갭 원인 분석")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--telegram-db", type=Path, default=DEFAULT_TELEGRAM_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    cases = load_extreme_cases(args.watchlist_db, args.krx_db)
    blind_inputs = build_blind_inputs(cases, args.telegram_db)
    classifications = classify_blind_inputs(blind_inputs)
    result = evaluate(cases, classifications)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    blind_path = args.output_dir / "phase6_extreme_gap_blind_inputs.json"
    result_path = args.output_dir / "phase6_extreme_gap_causes.json"
    report_path = args.output_dir / "phase6_extreme_gap_causes.md"
    blind_path.write_text(json.dumps(blind_inputs, ensure_ascii=False, indent=2), encoding="utf-8")
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(render_markdown(result), encoding="utf-8")
    print(result_path)


if __name__ == "__main__":
    main()
