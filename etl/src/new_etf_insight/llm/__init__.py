from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

from new_etf_insight.llm.base import LlmProvider
from new_etf_insight.llm.codex_provider import CodexProvider


DEFAULT_PROVIDER = "codex"
PROVIDER_ENV = "ETF_LLM_PROVIDER"

LLM_RETRIES = 2  # 일시적 오류 흡수 — 총 3회 시도 (collect fetch 재시도와 대칭)
LLM_RETRY_BACKOFF = 3.0  # 초, attempt마다 선형 증가
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_STATUS_RE = re.compile(r"\((\d{3})\)")


def _is_transient(exc: Exception) -> bool:
    """재시도 가치 있는 일시적 오류인가.

    - OpenAI HTTP: 네트워크/타임아웃/SSE 스트림끊김(requests) + 429/5xx.
      provider가 non-ok를 RuntimeError("... (NNN): ...")로 던져 상태코드 파싱.
    - codex CLI: subprocess 비정상 종료(일시 blip)는 재시도.
    영구 오류(401 인증, 스키마/파싱)는 즉시 전파 — 재시도 낭비 안 함.
    """
    if isinstance(exc, (requests.exceptions.RequestException, subprocess.CalledProcessError)):
        return True
    if isinstance(exc, RuntimeError):
        m = _STATUS_RE.search(str(exc))
        if m and int(m.group(1)) in _RETRYABLE_STATUS:
            return True
    return False


def get_provider(provider_name: str | None = None) -> LlmProvider:
    name = (provider_name or os.getenv(PROVIDER_ENV) or DEFAULT_PROVIDER).strip().lower()

    if name in {"codex", "codex_cli"}:
        return CodexProvider()

    raise ValueError(f"Unsupported LLM provider: {name}")


def generate_json(
    prompt: str,
    *,
    output_schema_path: Path,
    search: bool = False,
    model: str | None = None,
    provider_name: str | None = None,
    _sleep=time.sleep,
) -> str:
    """LLM JSON 호출. 일시적 오류(429/5xx/타임아웃/스트림끊김)는 재시도.

    한 번의 API blip이 analyze 단계 전체를 exit 1 → 텔레그램 세션 FAILED 로 만들던
    문제(collect fetch 재시도와 같은 취약성). _sleep 은 테스트 주입용.
    """
    provider = get_provider(provider_name)
    for attempt in range(LLM_RETRIES + 1):
        try:
            return provider.generate_json(
                prompt,
                output_schema_path=output_schema_path,
                search=search,
                model=model,
            )
        except Exception as exc:  # noqa: BLE001 — transient 판별 후 나머지는 재전파
            if attempt < LLM_RETRIES and _is_transient(exc):
                # ASCII only: cp949 콘솔에서도 print 안전 (exc 본문 보간 금지)
                print(f"[llm] transient error, retrying {attempt + 1}/{LLM_RETRIES}", file=sys.stderr)
                _sleep(LLM_RETRY_BACKOFF * (attempt + 1))
                continue
            raise
