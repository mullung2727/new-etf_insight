from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from kiwoom import us
from kiwoom.client import KiwoomError

router = APIRouter(prefix="/us", tags=["us"])

_CODE_RE = re.compile(r"^[A-Z0-9.-]{1,10}$")

# 뉴욕 정규장 16:00 마감 + 종가 확정 여유. 이 시각 이후면 오늘 봉도 끝난 날로 본다.
SESSION_DONE = time(16, 30)


def _cutoff(now_ny: datetime) -> str:
    day = now_ny.date()
    if now_ny.time() >= SESSION_DONE:
        day += timedelta(days=1)
    return day.strftime("%Y%m%d")


@router.get("/daily", operation_id="get_us_daily", summary="미국 종목 일봉 조회 (키움 usa06012)")
def get_us_daily(
    code: str, from_: str = Query(..., alias="from"), today_ny: str | None = None
) -> Any:
    """미국 종목 일봉을 날짜 오름차순으로 반환한다. 예: ?code=QQQ&from=20261013.

    끝난 날만 반환(뉴욕 기준 당일 진행 봉 제외), 수정주가 적용, USD.
    today_ny(YYYYMMDD): 이 날짜 이상 봉은 버림. 생략 시 기본값: 뉴욕 16:30 이후면 내일 → 오늘 봉 포함.
    """
    if len(from_) != 8 or not from_.isdigit():
        raise HTTPException(status_code=422, detail="from은 YYYYMMDD 8자리 숫자여야 함")
    upper = code.upper()
    if not _CODE_RE.match(upper):
        raise HTTPException(status_code=422, detail="code는 영문·숫자·'.'·'-' 1~10자여야 함")
    today = today_ny or _cutoff(datetime.now(ZoneInfo("America/New_York")))
    try:
        return us.get_us_daily(upper, from_, today)
    except KiwoomError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
