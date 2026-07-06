"""Telegram 채널별 처리 config 로더.

채널별 주소·첨부 다운로드 여부/패턴을 `telegram_channels.json`에서 읽는다.
스케줄(시간/간격)은 여기 두지 않는다 — ops registry cron 담당.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent / "telegram_channels.json"


def load_all_channels(config_path: Path | str = DEFAULT_CONFIG) -> dict:
    return json.loads(Path(config_path).read_text(encoding="utf-8"))


def load_channel_config(channel: str, config_path: Path | str = DEFAULT_CONFIG) -> dict:
    channels = load_all_channels(config_path)
    if channel not in channels:
        raise KeyError(f"channel '{channel}' not in {config_path}")
    return channels[channel]


def load_discovery_channels(config_path: Path | str = DEFAULT_CONFIG) -> dict:
    """`feed_role=discovery_source`인 채널만 필터."""
    channels = load_all_channels(config_path)
    return {ch: cfg for ch, cfg in channels.items() if cfg.get("feed_role") == "discovery_source"}
