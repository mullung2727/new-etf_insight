"""Discord webhook 알림 — best-effort.

`DISCORD_WEBHOOK_URL`(.env)이 설정돼 있으면 메시지를 Discord에 POST한다.
미설정이거나 전송 실패해도 배치 본 작업을 막지 않는다(예외 삼킴 + 로그).

종가베팅 주문/체결 보고는 결정론적 상태 요약이라 LLM(OpenClaw)을 거치지 않고
배치 스크립트가 직접 이 함수로 알린다.
"""
from __future__ import annotations

import os

import requests

_TIMEOUT = 10
_MAX_LEN = 1900  # Discord content 2000자 제한 여유


def send_discord(message: str, webhook_url: str | None = None) -> bool:
    """Discord로 message를 POST. 성공 True, 미설정/실패 False (예외 안 던짐)."""
    url = webhook_url if webhook_url is not None else os.getenv("DISCORD_WEBHOOK_URL", "")
    if not url:
        # ASCII only: cp949 콘솔에서도 안전 (stdout 재설정 안 한 호출자 대비)
        print("[notify] DISCORD_WEBHOOK_URL not set - skip Discord notify")
        return False
    try:
        resp = requests.post(url, json={"content": message[:_MAX_LEN]}, timeout=_TIMEOUT)
        resp.raise_for_status()
        return True
    except Exception as exc:
        print(f"[notify] Discord send failed: {exc}")
        return False
