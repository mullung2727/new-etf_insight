"""YouTube 채널 config 로더 + channel_id 파싱/resolve.

채널 목록 single source: `youtube_channels.json` (key = UC… channel_id).
스펙: docs/youtube_tech.md §3.2–3.3
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Callable

DEFAULT_CONFIG = Path(__file__).resolve().parent / "youtube_channels.json"

# UC + 22 chars (standard channel_id 24 total)
_CHANNEL_ID_RE = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")
_CHANNEL_PATH_RE = re.compile(
    r"/channel/(UC[a-zA-Z0-9_-]{22})(?:/|$|\?)",
    re.IGNORECASE,
)
# Separate patterns for canonical / og:url (attribute order flexible)
_LINK_CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_LINK_CANONICAL_RE2 = re.compile(
    r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
    re.IGNORECASE,
)
_OG_URL_RE = re.compile(
    r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_URL_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']',
    re.IGNORECASE,
)
_CHANNEL_ID_JSON_RE = re.compile(r'"channelId"\s*:\s*"(UC[a-zA-Z0-9_-]{22})"')

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def load_all_channels(config_path: Path | str = DEFAULT_CONFIG) -> dict[str, dict]:
    """active json 전체. 파일 없으면 {} 반환."""
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_channel_config(channel_id: str, config_path: Path | str = DEFAULT_CONFIG) -> dict:
    """없으면 KeyError."""
    channels = load_all_channels(config_path)
    if channel_id not in channels:
        raise KeyError(f"channel '{channel_id}' not in {config_path}")
    return channels[channel_id]


def load_discovery_channels(config_path: Path | str = DEFAULT_CONFIG) -> dict[str, dict]:
    """feed_role == 'discovery_source' 만."""
    channels = load_all_channels(config_path)
    return {
        ch: cfg
        for ch, cfg in channels.items()
        if cfg.get("feed_role") == "discovery_source"
    }


def load_auto_summary_channels(
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, dict]:
    """summary_mode == 'auto' 만. 기본(미기입)은 manual."""
    channels = load_all_channels(config_path)
    out: dict[str, dict] = {}
    for ch, cfg in channels.items():
        mode = str((cfg or {}).get("summary_mode") or "manual").strip().lower()
        if mode == "auto":
            out[ch] = cfg
    return out


def load_summary_hint(
    channel_id: str, config_path: Path | str = DEFAULT_CONFIG
) -> str:
    """채널별 요약 특성 문구. 미등록 채널·미기입이면 빈 문자열."""
    cfg = load_all_channels(config_path).get(channel_id) or {}
    return str(cfg.get("summary_hint") or "").strip()


def parse_channel_id(raw: str) -> str:
    """UC /channel/UC 만(네트워크 없음). 실패 시 ValueError."""
    s = (raw or "").strip()
    if not s:
        raise ValueError("빈 채널 입력")

    if _CHANNEL_ID_RE.match(s):
        return s

    # reject video URLs early
    low = s.lower()
    if "youtu.be/" in low or "/watch" in low or "/shorts/" in low:
        raise ValueError(f"영상 URL은 채널이 아님: {raw}")

    # bare handle without resolve
    if s.startswith("@") or "youtube.com/@" in low:
        raise ValueError(f"@handle 은 resolve 필요: {raw}")

    m = _CHANNEL_PATH_RE.search(s)
    if m:
        cid = m.group(1)
        if _CHANNEL_ID_RE.match(cid):
            return cid

    raise ValueError(f"channel_id 파싱 실패: {raw}")


def _uc_from_url(url: str) -> str | None:
    m = _CHANNEL_PATH_RE.search(url)
    if not m:
        return None
    cid = m.group(1)
    return cid if _CHANNEL_ID_RE.match(cid) else None


def _extract_channel_id_from_html(html: str) -> str:
    """canonical/og:url 우선, 없으면 선두 channelId 폴백."""
    for pattern in (_LINK_CANONICAL_RE, _LINK_CANONICAL_RE2, _OG_URL_RE, _OG_URL_RE2):
        m = pattern.search(html)
        if m:
            cid = _uc_from_url(m.group(1))
            if cid:
                return cid

    # head-scoped or first channelId only
    head_end = html.lower().find("</head>")
    head_chunk = html[: head_end + 7] if head_end >= 0 else html[:8000]
    m = _CHANNEL_ID_JSON_RE.search(head_chunk)
    if m:
        cid = m.group(1)
        if _CHANNEL_ID_RE.match(cid):
            return cid

    # first in full doc as last resort (still first match only)
    m = _CHANNEL_ID_JSON_RE.search(html)
    if m:
        cid = m.group(1)
        if _CHANNEL_ID_RE.match(cid):
            return cid

    raise ValueError("HTML에서 channel_id를 찾지 못함")


def _normalize_handle_url(raw: str) -> str:
    s = raw.strip()
    if s.startswith("@"):
        return f"https://www.youtube.com/{s}"
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("youtube.com/") or s.startswith("www.youtube.com/"):
        return "https://" + s
    raise ValueError(f"resolve 할 수 없는 입력: {raw}")


def resolve_channel_id(
    raw: str,
    *,
    opener: Callable = urllib.request.urlopen,
) -> str:
    """parse 성공 시 그대로. @handle URL이면 GET HTML 후 channelId 추출.

    실패 시 ValueError. opener 주입으로 테스트 mock.
    """
    try:
        return parse_channel_id(raw)
    except ValueError:
        pass

    s = (raw or "").strip()
    if not s:
        raise ValueError("빈 채널 입력")

    # video URLs still rejected
    low = s.lower()
    if "youtu.be/" in low or "/watch" in low or "/shorts/" in low:
        raise ValueError(f"영상 URL은 채널이 아님: {raw}")

    url = _normalize_handle_url(s)
    req = urllib.request.Request(url, headers={"User-Agent": _DEFAULT_UA})
    try:
        with opener(req, timeout=40) as resp:
            data = resp.read() if hasattr(resp, "read") else resp
            if isinstance(data, bytes):
                html = data.decode("utf-8", errors="replace")
            else:
                html = str(data)
    except Exception as exc:  # noqa: BLE001 — surface as ValueError for API
        raise ValueError(f"채널 페이지 조회 실패: {raw}") from exc

    return _extract_channel_id_from_html(html)
