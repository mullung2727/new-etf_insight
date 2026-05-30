"""Access-token issuance, caching and refresh.

Kiwoom issues a bearer token from ``POST {host}/oauth2/token`` with the app
key/secret. Tokens expire, so we cache to a file and refresh before expiry.
Mirrors the lazy single-instance pattern in ``api/duck.py``.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))

import httpx

from .config import Config, load_config

logger = logging.getLogger(__name__)

# Refresh this many seconds before the reported expiry to avoid edge races.
_REFRESH_SKEW = 600

_cfg: Config | None = None
_token: str | None = None
_expires_at: float = 0.0  # epoch seconds


def _config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg


def _parse_expiry(data: dict) -> float:
    """Return epoch seconds for the token expiry.

    Kiwoom returns ``expires_dt`` as ``YYYYMMDDHHMMSS`` (KST). Fall back to a
    conservative 12h window if the field is missing/unparseable.
    """
    raw = str(data.get("expires_dt") or "").strip()
    if len(raw) == 14 and raw.isdigit():
        try:
            dt = datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=_KST)
            return dt.timestamp()
        except ValueError:
            pass
    return time.time() + 12 * 3600


def _load_cache(cfg: Config) -> bool:
    global _token, _expires_at
    path = cfg.token_cache_path
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    if data.get("env") != cfg.env:
        return False
    token = data.get("token")
    expires_at = float(data.get("expires_at", 0))
    if token and expires_at - _REFRESH_SKEW > time.time():
        _token, _expires_at = token, expires_at
        return True
    return False


def _save_cache(cfg: Config) -> None:
    try:
        cfg.token_cache_path.write_text(
            json.dumps({"env": cfg.env, "token": _token, "expires_at": _expires_at}),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("token cache write failed: %s", exc)


def _issue(cfg: Config) -> None:
    global _token, _expires_at
    resp = httpx.post(
        f"{cfg.rest_host}/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "appkey": cfg.appkey,
            "secretkey": cfg.secretkey,
        },
        headers={"content-type": "application/json;charset=UTF-8"},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RuntimeError(f"token issue returned no token: {data}")
    _token = token
    _expires_at = _parse_expiry(data)
    _save_cache(cfg)
    logger.info("kiwoom token issued (env=%s)", cfg.env)


def get_token() -> str:
    """Return a valid bearer token, issuing/refreshing as needed."""
    cfg = _config()
    if _token and _expires_at - _REFRESH_SKEW > time.time():
        return _token
    if _load_cache(cfg):
        return _token  # type: ignore[return-value]
    _issue(cfg)
    return _token  # type: ignore[return-value]
