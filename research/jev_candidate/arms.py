"""P3 판단 경로 A·B + 비교군 G·H·I·E — PLAN_JEV_CANDIDATE_JUDGE §7.

불러온 데이터 위의 순수 함수만 둔다 (Jev 호출 없음, outcomes 조회 없음).
K = 3 (§2 선택 종목 수).
F 무작위 placebo 는 결정 행으로 저장하지 않는다 — 평가 단계에서 수익률 배열로 샘플링한다.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m research.jev_candidate.arms --dates 20260409,20260410
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.jev_candidate import questions, store
from research.jev_candidate.questions import QUESTION_SET_VER

K = 3  # 경로별 상위 종목 수
ARMS = ("A", "B", "G", "H", "I_A", "I_B", "E")


def _top(picks: list[tuple], k: int = K) -> list[dict]:
    """순위 행 조립 — (ticker, score) 정렬済み 상위 k개."""
    return [
        {"ticker": ticker, "score": score, "rank": i + 1, "decision": "BUY"}
        for i, (ticker, score) in enumerate(picks[:k])
    ]


def pick_A(answers: dict[str, list[dict]]) -> list[dict]:
    """Jev 원점수 상위 K — 동률은 티커 오름차순."""
    ranked = sorted(
        ((t, questions.jev_score(rows)) for t, rows in answers.items()),
        key=lambda kv: (-kv[1], kv[0]),
    )
    return _top(ranked)


def pick_B(answers: dict[str, list[dict]]) -> list[dict]:
    """규칙 BUY 중 원점수 상위 K — 없으면 빈 목록 (NO-TRADE)."""
    ranked = sorted(
        ((t, questions.jev_score(rows)) for t, rows in answers.items() if questions.rule_buy(rows)),
        key=lambda kv: (-kv[1], kv[0]),
    )
    return _top(ranked)


def pick_G(states: list[dict]) -> list[dict]:
    """등락률 상위 K — 동률은 티커 오름차순."""
    ranked = sorted(
        ((s["ticker"], s["price_pct"]) for s in states),
        key=lambda kv: (-kv[1], kv[0]),
    )
    return _top(ranked)


def pick_H(states: list[dict]) -> list[dict]:
    """언급량(n_passed) 상위 K — 동률은 등락률, 그다음 티커 순."""
    by_ticker = {s["ticker"]: s for s in states}
    ranked = sorted(
        ((t, float(s["n_passed"])) for t, s in by_ticker.items()),
        key=lambda kv: (-kv[1], -by_ticker[kv[0]]["price_pct"], kv[0]),
    )
    return _top(ranked)


def pick_I_A(answers_i: dict[str, list[dict]]) -> list[dict]:
    """글 섞기 run(-I) 답에 A와 같은 식."""
    return pick_A(answers_i)


def pick_I_B(answers_i: dict[str, list[dict]]) -> list[dict]:
    """글 섞기 run(-I) 답에 B와 같은 식."""
    return pick_B(answers_i)


def pick_E(states: list[dict]) -> list[dict]:
    """Top30 전체 — 점수·순위 없음."""
    return [
        {"ticker": s["ticker"], "score": None, "rank": None, "decision": "BUY"}
        for s in sorted(states, key=lambda s: s["ticker"])
    ]


def decide_day(con: sqlite3.Connection, day: str, question_set_ver: str) -> list[dict]:
    """하루치 전 arm 결정 행 — I arm 은 "-I" run 답에서, 저장은 주 run_id 로."""
    run_id = f"backtest-open-{day}-{question_set_ver}"
    states = store.load_states(con, run_id)
    answers = store.load_answers(con, run_id)
    answers_i = store.load_answers(con, run_id + "-I")
    picks = {
        "A": pick_A(answers),
        "B": pick_B(answers),
        "G": pick_G(states),
        "H": pick_H(states),
        "I_A": pick_I_A(answers_i),
        "I_B": pick_I_B(answers_i),
        "E": pick_E(states),
    }
    rows = []
    for arm in ARMS:
        for p in picks[arm]:
            rows.append(
                {
                    "run_id": run_id,
                    "date": day,
                    "ticker": p["ticker"],
                    "arm": arm,
                    "score": p["score"],
                    "rank": p["rank"],
                    "decision": p["decision"],
                }
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    """CLI — 날짜별 decide_day → decisions 저장 + arm별 종목·이름 출력."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True, help="YYYYMMDD 콤마 구분")
    ap.add_argument("--db", default=str(store.DEFAULT_DB))
    args = ap.parse_args(argv)
    days = [d.strip() for d in args.dates.split(",") if d.strip()]

    con = store.connect(args.db)
    store.ensure_schema(con)
    for day in days:
        run_id = f"backtest-open-{day}-{QUESTION_SET_VER}"
        rows = decide_day(con, day, QUESTION_SET_VER)
        store.save_decisions(con, rows)
        names = {s["ticker"]: s["name"] for s in store.load_states(con, run_id)}
        for arm in ARMS:
            picks = [r for r in rows if r["arm"] == arm]
            desc = ", ".join(f"{r['ticker']}({names.get(r['ticker'], '?')})" for r in picks)
            print(f"{day} {arm}: {desc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
