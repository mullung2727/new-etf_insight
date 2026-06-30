"""Environment config + host selection for the Kiwoom broker.

Loaded once at import. ``KIWOOM_ENV`` picks the REST/WebSocket host: ``paper``
(모의투자) is the safe default, ``real`` (실전) moves actual money.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load broker/.env then repo-root .env (repo root = two levels up from this file).
# broker/.env takes precedence; root .env is the fallback where shared keys live.
_BROKER_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BROKER_ROOT.parent
_ENV_PATH = _BROKER_ROOT / ".env"
load_dotenv(_ENV_PATH)                    # broker/.env  (may not exist)
load_dotenv(_REPO_ROOT / ".env")          # repo root .env  (override=False keeps broker wins)

# REST hosts. The :10000 WebSocket endpoints are used by v2 (condition realtime).
_HOSTS = {
    "paper": "https://mockapi.kiwoom.com",
    "real": "https://api.kiwoom.com",
}
_WS_HOSTS = {
    "paper": "wss://mockapi.kiwoom.com:10000/api/dostk/websocket",
    "real": "wss://api.kiwoom.com:10000/api/dostk/websocket",
}


def _get_first(*names: str) -> str:
    """Return the first non-empty env value among the given names."""
    for name in names:
        val = os.getenv(name, "").strip()
        if val:
            return val
    return ""


def _require_first(*names: str) -> str:
    val = _get_first(*names)
    if not val:
        raise RuntimeError(
            f"None of {names} are set. "
            "Copy broker/.env.example to broker/.env and fill in, "
            "or set the variable in the repo-root .env."
        )
    return val


@dataclass(frozen=True)
class Config:
    appkey: str
    secretkey: str
    env: str
    rest_host: str
    ws_host: str
    account_no: str
    max_order_amount: int
    token_cache_path: Path

    @property
    def is_paper(self) -> bool:
        return self.env == "paper"


_runtime_env: str | None = None


def set_runtime_env(env: str) -> None:
    global _runtime_env
    _runtime_env = env


def get_current_env() -> str:
    return _runtime_env or os.getenv("KIWOOM_ENV", "paper").strip().lower()


def load_config() -> Config:
    env = get_current_env()
    if env not in _HOSTS:
        raise RuntimeError(f"KIWOOM_ENV must be 'paper' or 'real', got: {env!r}")

    cache = os.getenv("TOKEN_CACHE_PATH", ".token_cache.json").strip()
    cache_path = Path(cache)
    if not cache_path.is_absolute():
        cache_path = _ENV_PATH.parent / cache_path

    return Config(
        # Accept both canonical names and the KIWOON_MOCK_TR_* convention.
        appkey=_require_first("KIWOOM_APPKEY", "KIWOON_MOCK_TR_APP_KEY"),
        secretkey=_require_first("KIWOOM_SECRETKEY", "KIWOON_MOCK_TR_APP_SECRET"),
        env=env,
        rest_host=_HOSTS[env],
        ws_host=_WS_HOSTS[env],
        account_no=_get_first("KIWOOM_ACCOUNT_NO", "KIWOON_MOCK_TR_ACCOUNT_NO"),
        max_order_amount=int(os.getenv("MAX_ORDER_AMOUNT", "1000000")),
        token_cache_path=cache_path,
    )
