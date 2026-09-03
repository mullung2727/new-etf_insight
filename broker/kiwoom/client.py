"""Thin httpx wrapper for Kiwoom REST TR calls.

Every TR is a ``POST {host}{endpoint}`` with the bearer token and the TR code
in the ``api-id`` header. Continuation paging uses the ``cont-yn``/``next-key``
header pair, echoed back in the response headers.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .auth import get_token
from .config import Config, load_config

logger = logging.getLogger(__name__)

_cfg: Config | None = None

# ── Rate limit 방어 ────────────────────────────────────────────────────────────
# 모든 TR(quote/order/잔고/청산)이 request()를 통과 → 여기 limiter 하나로 전 소비자
# (배치·청산워커·broker-web)의 Kiwoom 콜을 합산 제어. broker 단일 프로세스라 모듈 전역.
_RATE_LOCK = threading.Lock()
# Kiwoom mock 한도(초당 요청수)가 빡빡 → 간격을 한도 아래로. KIWOOM_MIN_INTERVAL로 조정.
_MIN_INTERVAL = float(os.getenv("KIWOOM_MIN_INTERVAL", "0.3"))  # 0.3s≈3.3/s
_last_call = 0.0        # 직전 호출 monotonic ts
_MAX_RETRIES = int(os.getenv("KIWOOM_MAX_RETRIES", "4"))  # 429 시 추가 재시도
_BACKOFF_BASE = 0.2     # 0.2 → 0.4 → 0.8 → 1.6 지수 백오프


def _throttle() -> None:
    """프로세스 전역 최소 호출간격 보장 — 429 사전 예방."""
    global _last_call
    with _RATE_LOCK:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _retry_after(resp: httpx.Response) -> float | None:
    """Retry-After 헤더(초) → float. 없거나 파싱불가 시 None."""
    v = resp.headers.get("retry-after")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


class KiwoomError(RuntimeError):
    """Raised when Kiwoom returns a non-zero return_code or an HTTP error."""


@dataclass
class TrResult:
    data: dict[str, Any]
    cont_yn: str  # "Y" if more pages remain
    next_key: str  # pass back as next_key to fetch the next page


def request(
    api_id: str,
    endpoint: str,
    body: dict[str, Any] | None = None,
    *,
    cont_yn: str = "N",
    next_key: str = "",
) -> TrResult:
    """Call a Kiwoom TR and return its JSON body plus continuation keys.

    Raises ``KiwoomError`` on HTTP failure or a non-zero ``return_code``.
    """
    cfg = _config()
    headers = {
        "content-type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {get_token()}",
        "api-id": api_id,
        "cont-yn": cont_yn,
        "next-key": next_key,
    }
    url = f"{cfg.rest_host}{endpoint}"
    attempt = 0
    while True:
        _throttle()
        try:
            resp = httpx.post(url, json=body or {}, headers=headers, timeout=15.0)
        except httpx.HTTPError as exc:
            raise KiwoomError(f"{api_id} request failed: {exc}") from exc

        if resp.status_code == 429 and attempt < _MAX_RETRIES:
            delay = _retry_after(resp) or _BACKOFF_BASE * (2 ** attempt)
            logger.warning(
                "%s HTTP 429 rate-limited — retry %d/%d in %.2fs",
                api_id, attempt + 1, _MAX_RETRIES, delay,
            )
            time.sleep(delay)
            attempt += 1
            continue
        break

    if resp.status_code != 200:
        raise KiwoomError(f"{api_id} HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    # Kiwoom signals logical errors in-body via return_code (0 == success).
    code = data.get("return_code")
    if code not in (None, 0, "0"):
        msg = data.get("return_msg", "")
        raise KiwoomError(f"{api_id} return_code={code}: {msg}")

    return TrResult(
        data=data,
        cont_yn=resp.headers.get("cont-yn", "N"),
        next_key=resp.headers.get("next-key", ""),
    )
