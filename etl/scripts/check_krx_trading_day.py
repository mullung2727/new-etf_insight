"""KRX 배치용 거래일 선확인.

주말·대한민국 공휴일·KRX 연말 휴장일(12월 31일)을 실제 시세 API 호출 전에
차단한다. 임시 휴장처럼 달력에 늦게 반영되는 경우를 위해 기존 스냅샷 동일성
가드는 별도로 유지한다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import holidays

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드

NON_TRADING_EXIT_CODE = 3


def trading_day_status(date_key: str) -> tuple[bool, str]:
    """YYYYMMDD의 KRX 거래 여부와 판정 사유를 반환한다."""
    day = datetime.strptime(date_key, "%Y%m%d").date()
    if day.weekday() >= 5:
        return False, "주말"

    holiday_name = holidays.KR(years=[day.year]).get(day)
    if holiday_name:
        return False, str(holiday_name)

    if day.month == 12 and day.day == 31:
        return False, "KRX 연말 휴장"

    return True, "평일·공휴일 아님"


def is_krx_trading_day(date_key: str) -> bool:
    return trading_day_status(date_key)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KRX 거래일 선확인")
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    args = parser.parse_args(argv)

    is_trading, reason = trading_day_status(args.date)
    if is_trading:
        print(f"[trading-day] {args.date}: 거래일 ({reason})")
        return 0

    print(f"[trading-day] {args.date}: 휴장일 ({reason})")
    return NON_TRADING_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
