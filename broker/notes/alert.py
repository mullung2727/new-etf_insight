"""진입가 도달 감시 — idea 노트의 entry_price에 현재가가 닿으면 Discord 알림.

판정은 순수 함수 ``run_idea_alert_check(today)`` 하나. broker/main.py의 장중
폴링 루프가 5분마다 호출하고, 테스트는 시세·웹훅을 monkeypatch해 직접 호출한다.

Discord 전송은 etl/scripts/notify.py의 축약판 — broker는 별도 venv라 그 모듈을
import할 수 없어 여기 독립 구현한다(하루 1회 발송 정책이라 재시도 없음).
"""
from __future__ import annotations

import logging
import os

import requests

from kiwoom import quotes

from . import store

logger = logging.getLogger(__name__)

_TIMEOUT = 10


def send_discord_alert(message: str) -> bool:
    """DISCORD_WEBHOOK_URL로 message POST. 성공 True, 미설정/실패 False(예외 안 던짐)."""
    url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        logger.info("DISCORD_WEBHOOK_URL not set - skip idea alert")
        return False
    try:
        resp = requests.post(url, json={"content": message[:1900]}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — 알림 실패가 감시 루프를 죽이지 않게
        logger.warning("idea alert send failed: %s", exc)
        return False


def _fmt(name: str | None, symbol: str, cur: int, entry: int) -> str:
    label = f"{name}({symbol})" if name else symbol
    return f"[진입가 도달] {label} 현재가 {cur:,}원 ≤ 진입가 {entry:,}원"


def run_idea_alert_check(today: str) -> list[str]:
    """감시 대상 idea 노트를 훑어 도달분을 알린다. 발송한 uid 목록 반환. today=YYYYMMDD.

    대상 종목을 1콜(get_watchlist_quotes)로 배치 조회하고, 현재가 ≤ entry_price면
    발송 + alerted_on 기록(하루 1회). cur_prc=0(거래정지·조회실패)은 도달로 보지 않는다.
    """
    candidates = store.list_idea_alert_candidates(today)
    if not candidates:
        return []
    price = {
        q["stk_cd"]: q["cur_prc"]
        for q in quotes.get_watchlist_quotes([c.symbol for c in candidates])
    }
    fired: list[str] = []
    for note in candidates:
        cur = price.get(note.symbol, 0)
        if cur <= 0 or note.entry_price is None or cur > note.entry_price:
            continue
        send_discord_alert(_fmt(note.name, note.symbol, cur, note.entry_price))
        store.mark_alerted(note.uid, today)  # 발송 성공 여부와 무관 — 하루 1회로 종결
        fired.append(note.uid)
    return fired
