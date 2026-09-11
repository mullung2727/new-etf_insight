"""호가 수집기 설정 로더 (SPEC_ORDERBOOK_SNAPSHOT_RECORDER §3).

파일: scripts/orderbook_recorder.json. 없거나 키가 빠지면 DEFAULTS 로 채운다(중첩 키 포함).
알 수 없는 키·범위 밖 값은 ConfigError(key, reason) — 호출부가 로그를 남기고 종료 코드 1.
프로세스 시작 시 한 번만 읽는다.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODE_RE = re.compile(r"^[0-9A-Z]{6}$")

DEFAULTS: dict = {
    "enabled": True,
    "broker_url": "http://127.0.0.1:8001",
    "windows": {
        "morning": {"start": "08:45:00", "end": "10:00:00"},
        "afternoon": {"start": "15:00:00", "end": "15:30:00"},
    },
    "snapshot_sec": 1,
    "max_stale_sec": 30,
    "venue": "KRX",
    "reg_timeout_sec": 5,
    "reg_retry": 3,
    "symbols": {"static": [], "max": 20, "max_lookback_days": 5},
    "scoring_result_path": "../../reports/recent_3day_probability_scores.json",
}


class ConfigError(ValueError):
    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"{key}: {reason}")
        self.key, self.reason = key, reason


def PATH() -> Path:
    return Path(__file__).resolve().parent / "orderbook_recorder.json"


def _merge(defaults: dict, given: dict, prefix: str = "") -> dict:
    """defaults 에 given 을 덮는다. defaults 에 없는 키는 오류."""
    if not isinstance(given, dict):
        raise ConfigError(prefix.rstrip(".") or "<root>", "must be an object")
    out = copy.deepcopy(defaults)
    for key, value in given.items():
        if key not in defaults:
            raise ConfigError(prefix + key, "unknown key")
        out[key] = _merge(defaults[key], value, f"{prefix}{key}.") if isinstance(defaults[key], dict) else value
    return out


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_hms(x) -> bool:
    if not isinstance(x, str) or not re.fullmatch(r"\d\d:\d\d:\d\d", x):
        return False
    h, m, s = (int(p) for p in x.split(":"))
    return h <= 23 and m <= 59 and s <= 59


def _validate(cfg: dict) -> None:
    if not isinstance(cfg["enabled"], bool):
        raise ConfigError("enabled", "must be true/false")
    url = urlparse(cfg["broker_url"]) if isinstance(cfg["broker_url"], str) else None
    if not url or url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost"):
        raise ConfigError("broker_url", "must be a local http address (127.0.0.1/localhost)")
    for name in ("morning", "afternoon"):
        win = cfg["windows"][name]
        for edge in ("start", "end"):
            if not _is_hms(win[edge]):
                raise ConfigError(f"windows.{name}.{edge}", "must be HH:MM:SS")
        if not win["start"] < win["end"]:
            raise ConfigError(f"windows.{name}", "start must be before end")
    if cfg["windows"]["morning"]["end"] > cfg["windows"]["afternoon"]["start"]:
        raise ConfigError("windows", "morning end must not be after afternoon start")
    if not _is_int(cfg["snapshot_sec"]) or cfg["snapshot_sec"] != 1:
        raise ConfigError("snapshot_sec", "only 1 is supported")
    stale = cfg["max_stale_sec"]
    if isinstance(stale, bool) or not isinstance(stale, (int, float)) or stale <= 0:
        raise ConfigError("max_stale_sec", "must be a positive number")
    if cfg["venue"] != "KRX":
        raise ConfigError("venue", "only KRX is supported")
    if not _is_int(cfg["reg_timeout_sec"]) or cfg["reg_timeout_sec"] != 5:
        raise ConfigError("reg_timeout_sec", "only 5 is supported (broker ACK timeout)")
    if not _is_int(cfg["reg_retry"]) or cfg["reg_retry"] <= 0:
        raise ConfigError("reg_retry", "must be a positive int")
    sym = cfg["symbols"]
    if not isinstance(sym["static"], list) or not all(isinstance(c, str) and CODE_RE.match(c)
                                                        for c in sym["static"]):
        raise ConfigError("symbols.static", "must be a list of 6-char codes ^[0-9A-Z]{6}$")
    if not _is_int(sym["max"]) or sym["max"] <= 0:
        raise ConfigError("symbols.max", "must be a positive int")
    if not _is_int(sym["max_lookback_days"]) or sym["max_lookback_days"] < 0:
        raise ConfigError("symbols.max_lookback_days", "must be an int >= 0")
    if not isinstance(cfg["scoring_result_path"], str) or not cfg["scoring_result_path"]:
        raise ConfigError("scoring_result_path", "must be a non-empty path string")


def load(path: Path | None = None) -> dict:
    """설정을 읽어 검증한 dict. scoring_result_path 는 프로젝트 루트 기준 절대 Path 로 바꾼다."""
    path = path or PATH()
    given: dict = {}
    if path.exists():
        try:
            given = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError("<file>", f"invalid JSON: {exc}") from None
    cfg = _merge(DEFAULTS, given)
    _validate(cfg)
    cfg["symbols"]["static"] = list(dict.fromkeys(cfg["symbols"]["static"]))
    cfg["scoring_result_path"] = (PROJECT_ROOT / cfg["scoring_result_path"]).resolve()
    return cfg
