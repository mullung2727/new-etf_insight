"""Report close-bet order status from the same query helpers used by the batch."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

try:
    from scripts.run_close_bet import DEFAULT_WATCHLIST_DB, load_close_bet_status, normalize_date_key
except ImportError:
    from run_close_bet import DEFAULT_WATCHLIST_DB, load_close_bet_status, normalize_date_key


def _task_status(task_name: str) -> dict:
    try:
        output = subprocess.check_output(
            ["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return {"error": str(exc)}
    result: dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def build_report(status: dict, task: dict) -> str:
    lines = [
        "종가베팅 주문 배치 결과",
        f"- DB date key: `{status['date_key']}`",
        f"- Windows 작업 마지막 실행: {task.get('Last Run Time', '확인 불가')}",
        f"- Windows 작업 마지막 결과: `{task.get('Last Result', task.get('error', '확인 불가'))}`",
        f"- 로그 파일: `{status['log_path']}` / {'있음' if status['log_exists'] else '없음'}",
        f"- llm_scores: {status['score_count']}건",
        f"- score >= {status['score_threshold']}: {status['threshold_count']}건",
    ]
    for row in status["threshold_rows"]:
        lines.append(f"  - {row['name']}({row['ticker']}) {row['score']}점")

    lines.append(f"- close_bet_orders: {status['order_count']}건")
    for row in status["order_rows"]:
        lines.append(
            "  - "
            f"{row['ticker']} score={row['score']} qty={row['qty']} "
            f"status={row['status']} order_no={row['order_no'] or '-'} "
            f"message={row['message'] or '-'}"
        )

    lines.append(f"- 최종 판단: {status['final_judgment']}")
    if status["threshold_count"] and not status["order_count"]:
        lines.append("- 관측 blocker: 주문 후보는 있었지만 주문 row가 없어 주문 로직 진입 전 실패로 판단")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Report close-bet order status")
    parser.add_argument("--date", help="YYYYMMDD or YYYY-MM-DD; default today KST")
    parser.add_argument("--score-threshold", type=int, default=70)
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--task-name", default=r"\OpenClaw\close-bet-order")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    date_key = normalize_date_key(args.date)
    status = load_close_bet_status(
        args.watchlist_db,
        date_key,
        score_threshold=args.score_threshold,
        log_dir=args.log_dir,
    )
    task = _task_status(args.task_name)
    if args.json:
        print(json.dumps({"status": status, "task": task}, ensure_ascii=False, indent=2))
    else:
        print(build_report(status, task))


if __name__ == "__main__":
    main()
