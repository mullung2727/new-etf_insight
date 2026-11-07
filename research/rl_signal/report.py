"""야간 재학습 결과 알림: results/<version>/<model>/<day>.json → 디스코드 한 통. 주문 없음."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "etl" / "scripts"))
from notify import (  # noqa: E402
    HEADLINE_FAIL,
    HEADLINE_OK,
    HEADLINE_WARN,
    headline,
    send_strategy_alert,
)

from .dataset import day_reasons
from .judge import stratum_summary
from .walkforward import RESULTS

WEBHOOK_ENV = "RL_SIGNAL_DISCORD_WEBHOOK_URL"
LABEL = "RL신호"
STAGE = "야간학습"
MIN_DAYS = 60
NAMES = {"stock": "종목", "etf": "ETF"}


def _pct(x: float) -> str:
    """+0.12% 형식. NaN 이면 -."""
    return "-" if x != x else f"{x:+.2%}"


def _first_part(sname: str, e: dict) -> str:
    """첫 줄 층 조각: 종목 +0.12% (34건, 장중보유 -0.05%)."""
    name = NAMES[sname]
    if e["n"] == 0:
        return f"{name} n=0"
    return f"{name} {_pct(e['model'])} ({e['trades']}건, 장중보유 {_pct(e['bh'])})"


def _summary_line(sname: str, s: dict) -> str:
    """층별 누적 한 줄: 종목 누적 7/60일 평균 +0.08% 기준 1충족 2충족 3미달 4충족."""
    name = NAMES[sname]
    if s["n"] == 0:
        return f"{name} 누적 0/{MIN_DAYS}일 (결과 없음)"
    crit = " ".join(f"{i}{'충족' if ok else '미달'}"
                    for i, ok in enumerate((s["c1"], s["c2"], s["c3"], s["c4"]), start=1))
    return f"{name} 누적 {s['n']}/{MIN_DAYS}일 평균 {_pct(s['mean'])} 기준 {crit}"


def build_message(version: str, model: str, day: str, start: str) -> str:
    """당일 결과 + 층별 누적 메시지. json 없으면 WARN + day_reasons 사유."""
    p = RESULTS / version / model / f"{day}.json"
    if not p.exists():
        reasons = day_reasons(day)
        detail = ", ".join(f"{k} {v}건" for k, v in sorted(reasons.items())) or "없음"
        return headline(HEADLINE_WARN, LABEL, STAGE, f"{day} 결과 없음", f"제외 사유: {detail}")
    rec = json.loads(p.read_text(encoding="utf-8"))
    first = f"{day} " + " · ".join(_first_part(s, rec[s]) for s in ("stock", "etf"))
    body = "\n".join(_summary_line(s, stratum_summary(version, model, s, start))
                     for s in ("stock", "etf"))
    return headline(HEADLINE_OK, LABEL, STAGE, first, body)


def main(argv=None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--day", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--failed", default=None)
    args = ap.parse_args(argv)
    if args.failed is not None:
        msg = headline(HEADLINE_FAIL, LABEL, STAGE, f"실패: {args.failed}")
    else:
        msg = build_message(args.version, args.model, args.day, args.start)
    print(msg)
    try:
        ok = send_strategy_alert(msg, WEBHOOK_ENV)
    except Exception as exc:
        print(f"[report] alert send failed: {exc}")
    else:
        if not ok:
            print("[report] alert send failed")


if __name__ == "__main__":
    main()
