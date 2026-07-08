"""배치 알림 — best-effort, 채널 교체 가능.

배치 본작업(DB 저장 등)은 알림과 분리돼 있다. 알림은 마지막에 한 번 보내고,
전송 실패해도 예외를 던지지 않아 본작업을 막지 않는다(실패는 False + 로그).

종가베팅 주문/체결 보고는 결정론적 상태 요약이라 LLM(OpenClaw)을 거치지 않고
배치 스크립트가 직접 여기로 알린다.

────────────────────────────────────────────────────────────────────────
보고 채널 바꾸는 법 (Discord ↔ Telegram ↔ …)
────────────────────────────────────────────────────────────────────────
1. `.env`에 `NOTIFY_CHANNEL=telegram` 설정 (기본값 discord).
2. 텔레그램이면 `.env`에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`도 채운다.
   (디스코드면 기존 `DISCORD_WEBHOOK_URL`.)
3. 끝. 리포트 배치 4종은 `send_report_messages.py`→`notify()`를 타므로
   env 하나로 채널이 바뀐다. 코드 수정 불필요.

채널 새로 추가하려면:
- `send_xxx(message) -> bool` 함수를 하나 만들고(성공 True, 실패/미설정 False,
  예외는 안 던진다),
- 아래 `_SENDERS` 딕셔너리에 `"xxx": send_xxx` 한 줄 등록.

참고: 종가베팅 트레이딩 스크립트(run_close_bet.py 등)는 아직 `send_discord`를
직접 부른다. 그쪽 알림까지 채널 스왑에 포함하려면 그 호출들을 `notify()`로
바꾸면 된다(이 파일은 이미 준비됨).
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import os
import time

import requests

_TIMEOUT = 10
_DISCORD_MAX_LEN = 1900   # Discord content 2000자 제한 여유
_TELEGRAM_MAX_LEN = 4000  # Telegram sendMessage 4096자 제한 여유

_SEND_RETRIES = 2  # 일시적 전송 오류 흡수 — 총 3회 (analyze LLM 재시도와 대칭)
_SEND_RETRY_BACKOFF = 2.0  # 초, attempt마다 선형 증가
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_retryable_send(exc: Exception) -> bool:
    """일시적(재시도 가치 있음) 전송 오류인가.

    429(rate-limit)/5xx(서버) 또는 네트워크/타임아웃은 재시도. 4xx(잘못된 웹훅 등)는
    영구 오류라 재시도 무의미 → 바로 False로 스킵.
    """
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = exc.response
        return resp is not None and resp.status_code in _RETRYABLE_STATUS
    return isinstance(exc, requests.exceptions.RequestException)


def send_discord(message: str, webhook_url: str | None = None, *, _sleep=time.sleep) -> bool:
    """Discord로 message를 POST. 성공 True, 미설정/실패 False (예외 안 던짐).

    일시적 오류(429/5xx/타임아웃)는 재시도 — 한 번의 웹훅 blip이 digest 전송 실패로
    텔레그램 세션 전체를 FAILED로 만들던 문제. _sleep 은 테스트 주입용.
    """
    url = webhook_url if webhook_url is not None else os.getenv("DISCORD_WEBHOOK_URL", "")
    if not url:
        # ASCII only: cp949 콘솔에서도 안전 (stdout 재설정 안 한 호출자 대비)
        print("[notify] DISCORD_WEBHOOK_URL not set - skip Discord notify")
        return False
    payload = {"content": message[:_DISCORD_MAX_LEN]}
    for attempt in range(_SEND_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT)
            resp.raise_for_status()
            return True
        except Exception as exc:
            if attempt < _SEND_RETRIES and _is_retryable_send(exc):
                _sleep(_SEND_RETRY_BACKOFF * (attempt + 1))
                continue
            print(f"[notify] Discord send failed: {exc}")
            return False
    return False  # 도달 불가 (루프가 반환/처리) — 타입체커 안심용


def send_telegram(message: str) -> bool:
    """Telegram Bot API sendMessage. 성공 True, 미설정/실패 False (예외 안 던짐).

    TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID(.env) 필요.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("[notify] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set - skip Telegram notify")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message[:_TELEGRAM_MAX_LEN]},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        print(f"[notify] Telegram send failed: {exc}")
        return False


def send_telegram_report(message: str) -> bool:
    """텔레그램 배치 전용 Discord 채널로 보낸다(기존 4종 웹훅과 분리된 별도 웹훅).

    쓰기는 순수 웹훅 — 봇/hermes 무관. 읽기(나중 LLM)만 봇+채널ID가 담당한다.
    env: TELEGRAM_REPORT_TO_DISCORD_WEBHOOK_URL.
    """
    return send_discord(message, webhook_url=os.getenv("TELEGRAM_REPORT_TO_DISCORD_WEBHOOK_URL", ""))


# 채널 → 이 모듈의 sender 함수 이름. 새 채널은 여기 한 줄 추가(위 docstring 참고).
# 함수 객체가 아니라 이름으로 두는 건 호출 시점에 조회하기 위함이다(테스트 monkeypatch
# 반영 + import 시점 캐시로 굳는 것 방지).
_SENDERS = {
    "discord": "send_discord",
    "telegram": "send_telegram",
    "telegram_report": "send_telegram_report",
}


def notify(message: str, channel: str | None = None) -> bool:
    """설정된 채널로 message를 보낸다. 채널은 인자 > NOTIFY_CHANNEL env > discord 순.

    미지원 채널이면 discord로 폴백(오타로 알림이 조용히 사라지는 것 방지).
    """
    name = (channel or os.getenv("NOTIFY_CHANNEL") or "discord").strip().lower()
    func_name = _SENDERS.get(name)
    if func_name is None:
        print(f"[notify] unknown NOTIFY_CHANNEL={name!r} - fallback to discord")
        func_name = "send_discord"
    return globals()[func_name](message)
