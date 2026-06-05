"""Thin httpx wrapper for Kiwoom REST TR calls.

Every TR is a ``POST {host}{endpoint}`` with the bearer token and the TR code
in the ``api-id`` header. Continuation paging uses the ``cont-yn``/``next-key``
header pair, echoed back in the response headers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from .auth import get_token
from .config import Config, load_config

logger = logging.getLogger(__name__)

_cfg: Config | None = None


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


def clear_cache() -> None:
    global _cfg
    _cfg = None


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
    try:
        resp = httpx.post(
            f"{cfg.rest_host}{endpoint}",
            json=body or {},
            headers=headers,
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise KiwoomError(f"{api_id} request failed: {exc}") from exc

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
