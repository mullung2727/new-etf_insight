"""장 시간표 — 개장/마감 시각이 바뀌는 날(수능·연초) 공용 시계.

평소 시각("HH:MM[:SS]")을 그날 실제 시각으로 밀어 준다. 정오 전 시각은
개장 기준(open − 09:00), 정오 후 시각은 마감 기준(close − 15:30)으로 민다.
특별한 날은 옆 `market_sessions.json`에 적는다. 연초 첫 거래일(10:00 개장)은
파일에 없어도 `check_krx_trading_day`로 자동 판정한다.
"""
from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  (cp949 가드 + path)

import argparse
import functools
import json
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from check_krx_trading_day import is_krx_trading_day

__all__ = ["load", "session", "shift", "at", "wait_until", "csat_missing", "main"]

KST = ZoneInfo("Asia/Seoul")

NORMAL_OPEN = "09:00"
NORMAL_CLOSE = "15:30"
NEW_YEAR_OPEN = "10:00"

SESSIONS_FILE = Path(__file__).with_name("market_sessions.json")

_HM_RE = re.compile(r"^(\d{2}):(\d{2})$")


def _parse_hm(text: object, what: str) -> tuple[int, int]:
    """'HH:MM' 검증 → (시, 분). 틀리면 ValueError."""
    m = _HM_RE.match(text) if isinstance(text, str) else None
    if m is None:
        raise ValueError(f"{what}: 'HH:MM' 형식이어야 함: {text!r}")
    h, minute = int(m.group(1)), int(m.group(2))
    if h > 23 or minute > 59:
        raise ValueError(f"{what}: 시각 범위 초과: {text!r}")
    return h, minute


def _parse_hms(hms: str) -> tuple[int, int, int]:
    """'HH:MM' 또는 'HH:MM:SS' 검증 → (시, 분, 초)."""
    parts = hms.split(":") if isinstance(hms, str) else []
    ok = len(parts) in (2, 3) and all(
        p.isascii() and p.isdigit() and len(p) <= 2 for p in parts)
    if not ok:
        raise ValueError(f"시각은 'HH:MM' 또는 'HH:MM:SS': {hms!r}")
    h, minute = int(parts[0]), int(parts[1])
    sec = int(parts[2]) if len(parts) == 3 else 0
    if h > 23 or minute > 59 or sec > 59:
        raise ValueError(f"시각 범위 초과: {hms!r}")
    return h, minute, sec


def _to_seconds(hm: str) -> int:
    h, minute = _parse_hm(hm, "세션 시각")
    return h * 3600 + minute * 60


def load(path: str | Path = SESSIONS_FILE) -> dict[str, tuple[str, str]]:
    """시간표 파일 읽기·검증 → {YYYYMMDD: (open, close)}. 없으면 {}.

    키는 8자리 숫자, open·close는 'HH:MM', open < close여야 한다.
    하나라도 틀리면 ValueError.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: JSON 파싱 실패: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path}: 최상위는 날짜→시간표 객체여야 함")
    out: dict[str, tuple[str, str]] = {}
    for day, entry in data.items():
        where = f"{path} [{day}]"
        if not isinstance(day, str) or len(day) != 8 or not day.isdigit():
            raise ValueError(f"{where}: 키는 YYYYMMDD 8자리 숫자여야 함")
        if not isinstance(entry, dict):
            raise ValueError(f"{where}: {{'open', 'close', 'why'}} 객체여야 함")
        o = _parse_hm(entry.get("open"), f"{where}.open")
        c = _parse_hm(entry.get("close"), f"{where}.close")
        if o >= c:
            raise ValueError(f"{where}: open이 close보다 빨라야 함")
        out[day] = (entry["open"], entry["close"])
    return out


@functools.lru_cache(maxsize=None)
def _first_trading_day(year: int) -> str:
    """그해 1월 1일부터 처음 거래일인 날(YYYYMMDD)."""
    day = date(year, 1, 1)
    for _ in range(31):  # 1월 안에는 반드시 거래일이 있다
        key = day.strftime("%Y%m%d")
        if is_krx_trading_day(key):
            return key
        day += timedelta(days=1)
    raise ValueError(f"{year}년 1월에 거래일이 없음")


def session(day: str,
            sessions: dict[str, tuple[str, str]] | None = None) -> tuple[str, str]:
    """그날 (open, close). 우선순위: 파일 > 연초 첫 거래일 > 평소."""
    if sessions is None:
        sessions = load()
    if day in sessions:
        return sessions[day]
    if day == _first_trading_day(int(day[:4])):
        return (NEW_YEAR_OPEN, NORMAL_CLOSE)
    return (NORMAL_OPEN, NORMAL_CLOSE)


def shift(day: str, hms: str, anchor: str | None = None,
          sessions: dict[str, tuple[str, str]] | None = None) -> int:
    """평소 시각을 그날 시각으로 미는 초. anchor 미지정 시 정오 기준 자동."""
    if anchor is None:
        h, minute, sec = _parse_hms(hms)
        anchor = "open" if (h, minute, sec) < (12, 0, 0) else "close"
    if anchor == "open":
        base, sess = NORMAL_OPEN, session(day, sessions)[0]
    elif anchor == "close":
        base, sess = NORMAL_CLOSE, session(day, sessions)[1]
    else:
        raise ValueError(f"anchor는 'open'·'close'·None: {anchor!r}")
    return _to_seconds(sess) - _to_seconds(base)


def at(day: str, hms: str, anchor: str | None = None,
       sessions: dict[str, tuple[str, str]] | None = None) -> datetime:
    """평소 시각 문자열 → 그날 KST datetime (microsecond 0)."""
    if sessions is None:
        sessions = load()
    h, minute, sec = _parse_hms(hms)
    base = datetime.strptime(day, "%Y%m%d").replace(
        tzinfo=KST, hour=h, minute=minute, second=sec, microsecond=0)
    return base + timedelta(seconds=shift(day, hms, anchor, sessions))


def csat_missing(day: str, sessions: dict[str, tuple[str, str]] | None = None) -> bool:
    """day(YYYYMMDD)가 10월 이후인데 그해 수능일(마감이 평소와 다른 날) 줄이 없으면 True."""
    if int(day[4:6]) < 10:
        return False
    if sessions is None:
        sessions = load()
    year = day[:4]
    return not any(key.startswith(year) and close != NORMAL_CLOSE
                   for key, (_open, close) in sessions.items())


def wait_until(day: str, hms: str, anchor: str | None = None,
               sessions: dict[str, tuple[str, str]] | None = None,
               now_fn: Callable[[], datetime] | None = None,
               sleep_fn: Callable[[float], None] = time.sleep) -> None:
    """at(...)까지 남았으면 sleep_fn(남은 초) 1회 호출."""
    target = at(day, hms, anchor, sessions)
    now = now_fn() if now_fn is not None else datetime.now(KST)
    if (left := (target - now).total_seconds()) > 0:
        sleep_fn(left)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="장 시간표 — 그날 shift(초) 출력")
    parser.add_argument("--shift", required=True, help="평소 시각 HH:MM")
    parser.add_argument("--date", default=None, help="YYYYMMDD (기본: 오늘 KST)")
    args = parser.parse_args(argv)
    day = args.date or datetime.now(KST).strftime("%Y%m%d")
    try:
        sessions = load(SESSIONS_FILE)  # 호출 시점 참조 — 테스트에서 패치 가능
    except ValueError as e:
        print(f"[market-session] 시간표 오류: {e}", file=sys.stderr)
        return 2
    print(shift(day, args.shift, sessions=sessions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
