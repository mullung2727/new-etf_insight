"""Jev 후보 판단 P1 — 후보 글 조회 + 1차 필터 + state 조립.

파이프라인(PLAN_JEV_CANDIDATE_JUDGE §4·§5) 중 LLM 이전 단계만 둔다.
판단 시각 차단선은 posted_at_utc 기준, date_kst 는 속도용 사전 필터에만 쓴다.
"오늘"이라는 말은 라벨·본문 어디에도 쓰지 않는다(시가 트랙 판단일이 D+1이라 어긋남).

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m unittest research.jev_candidate.tests.test_state
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from math import inf
from sqlite3 import Connection
from typing import Any
from zoneinfo import ZoneInfo

import typesafe_sdk

KST = ZoneInfo("Asia/Seoul")
EXCLUDED_CHANNELS = ("awake_realtimeCheck",)
JEV_MODEL = "jev-1.13.0"
STATE_TOKEN_LIMIT = 3000  # 합계 상한, approx_tokens 기준
FILTER_PASS = 0.5  # 1차 필터 통과 하한 (noul >= PASS)
AMBIG_LO = 0.35  # 애매 구간 하한
AMBIG_HI = 0.65  # 애매 구간 상한
# (하한 포함, 상한 제외) — pct 는 % 단위
PRICE_BUCKETS = [
    (-inf, -10, "-10% 미만 하락"),
    (-10, 0, "0~-10% 하락"),
    (0, 5, "0~+5% 상승"),
    (5, 10, "+5~+10% 상승"),
    (10, 20, "+10~+20% 상승"),
    (20, inf, "+20% 이상 상승"),
]
LABEL_TODAY = "[신호일]"
LABEL_AFTER = "[신호일 장마감 후]"
LABEL_PAST = "[이전 2일]"
# state 본문 출력 순서 = 문서 순서
SECTION_ORDER = (LABEL_TODAY, LABEL_AFTER, LABEL_PAST)
_D15_30 = (15, 30)


def approx_tokens(text: str) -> int:
    """한글 대충 세기 — len(text) // 2."""
    return len(text) // 2


def _parse_day(day: str) -> datetime:
    """'YYYY-MM-DD' → 그날 00:00 KST."""
    dt = datetime.strptime(day, "%Y-%m-%d")
    return dt.replace(tzinfo=KST)


def as_of_kst(signal_date: str, track: str, next_trading_date: str | None = None) -> datetime:
    """판단 시각 — close 트랙은 D 15:10, open 트랙은 다음 거래일 08:00 KST.

    휴일 계산은 여기서 안 하고 next_trading_date 를 그대로 받는다.
    open 트랙인데 next_trading_date 가 없으면 ValueError.
    """
    day0 = _parse_day(signal_date)
    if track == "close":
        return day0.replace(hour=15, minute=10)
    if track == "open":
        if next_trading_date is None:
            raise ValueError("open 트랙은 next_trading_date 필요")
        return _parse_day(next_trading_date).replace(hour=8, minute=0)
    raise ValueError(f"track은 close|open, 입력={track!r}")


def section_bounds(
    signal_date: str,
    track: str,
    prev2_trading_date: str,
    next_trading_date: str | None = None,
) -> list[tuple[str, datetime, datetime]]:
    """구간 경계 (라벨, 시작 포함, 끝 제외) — 시간 순서.

    [이전 2일] = prev2_trading_date 00:00 → D 00:00 (거래일 기준 2거래일 전,
    사이 주말·휴일 포함)
    [신호일] = D 00:00 → min(as_of, D 15:30)
    [신호일 장마감 후] = D 15:30 → as_of (open 트랙만)
    """
    as_of = as_of_kst(signal_date, track, next_trading_date)
    day0 = _parse_day(signal_date)
    past_start = _parse_day(prev2_trading_date)
    close_cut = day0.replace(hour=_D15_30[0], minute=_D15_30[1])
    sections = [(LABEL_PAST, past_start, day0), (LABEL_TODAY, day0, min(as_of, close_cut))]
    if track == "open":
        sections.append((LABEL_AFTER, close_cut, as_of))
    return sections


def _posted_kst(raw: str) -> datetime:
    """posted_at_utc(오프셋 포함 ISO) → KST. naive 면 UTC 로 간주."""
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def mentions_target(text: str, names: list[str]) -> bool:
    """대상 실언급 여부 — 증권사명·괄호 약어·작성자 표기는 오탐으로 건너뛴다.

    occurrence 가 진짜가 아닌 경우: (a) 바로 뒤가 "증권" (현대차증권),
    (b) "(" + 이름 + ")" 형태 (요약 글 증권사 약어), (c) 바로 앞이
    "작성자:" / "작성자: " (리포트 작성자 표기). 모든 occurrence 를
    str.find 로 검사해 하나라도 진짜면 True.
    """
    for name in names:
        if not name:
            continue
        start = 0
        while True:
            i = text.find(name, start)
            if i < 0:
                break
            end = i + len(name)
            if text[end : end + 2] == "증권":
                start = i + 1
                continue
            before = text[i - 1] if i > 0 else ""
            after = text[end] if end < len(text) else ""
            if before == "(" and after == ")":
                start = i + 1
                continue
            head = text[:i]
            if head.endswith("작성자:") or head.endswith("작성자: "):
                start = i + 1
                continue
            return True
    return False


def load_candidate_posts(
    con: Connection, names: list[str], start: datetime, end: datetime
) -> list[dict]:
    """후보 글 조회 — 채널 제외 + 종목명 포함 + [start, end) KST 필터.

    date_kst 는 속도용 사전 필터(±1일 여유)에만 쓰고, 실제 차단선은
    posted_at_utc → KST 변환값으로 파이썬에서 가른다.
    시간 통과 글 중 mentions_target False 는 버린다.
    본문 앞 200자 sha1 중복은 최초 1건만 남긴다.
    """
    if not names:
        return []
    lo = (start.date() - timedelta(days=1)).isoformat()
    hi = (end.date() + timedelta(days=1)).isoformat()
    placeholders = ", ".join("?" for _ in EXCLUDED_CHANNELS)
    like = " OR ".join("text LIKE ?" for _ in names)
    rows = con.execute(
        f"SELECT channel, post_id, posted_at_utc, text FROM telegram_posts"
        f" WHERE channel NOT IN ({placeholders})"
        f" AND date_kst BETWEEN ? AND ? AND ({like})",
        (*EXCLUDED_CHANNELS, lo, hi, *(f"%{n}%" for n in names)),
    ).fetchall()
    posts = []
    for channel, post_id, posted_at_utc, text in rows:
        posted = _posted_kst(posted_at_utc)
        if start <= posted < end:
            if not mentions_target(text, names):
                continue
            posts.append(
                {
                    "channel": channel,
                    "post_id": post_id,
                    "posted_at_kst": posted,
                    "text": text,
                    "text_hash": hashlib.sha1(text.encode("utf-8")).hexdigest(),
                }
            )
    posts.sort(key=lambda p: p["posted_at_kst"])
    seen: set[str] = set()
    out = []
    for p in posts:
        # 복제 글 판정 — 앞 200자 해시
        key = hashlib.sha1(p["text"][:200].encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def anonymize(text: str, mapping: dict[str, str]) -> str:
    """실명·티커 → 플레이스홀더. 긴 키부터 치환."""
    for real in sorted(mapping, key=len, reverse=True):
        text = text.replace(real, mapping[real])
    return text


def price_sentence(pct: float) -> str:
    """신호일 등락률 → '[가격] 신호일 {구간}' 1줄."""
    for lo, hi, label in PRICE_BUCKETS:
        if lo <= pct < hi:
            return f"[가격] 신호일 {label}"
    raise ValueError(f"구간 없음: {pct}")


def filter_question(placeholder: str = "종목A") -> dict:
    """1차 필터 질문 — 이 글이 대상 종목 자체를 다루는지 noul 1개."""
    return {
        "about": {
            "type": "noul",
            "instructions": (
                f"이 글의 주제가 {placeholder} 자체다. {placeholder}가 다른 회사 글에서"
                f" 발행 증권사 이름, 지수·테마 구성종목 나열,"
                f" 다른 단어의 일부로만 등장하면 아니다."
            ),
        }
    }


def filter_cache_key(text: str, mapping: dict[str, str]) -> str:
    """정확 입력 키 — 익명화 글 + filter_question + 모델 전체 sha1."""
    payload = {"state": anonymize(text, mapping), "questions": filter_question(), "model": JEV_MODEL}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def filter_posts(
    client: Any, posts: list[dict], mapping: dict[str, str], cache: Any = None
) -> list[dict]:
    """글마다 Jev 1차 필터 — noul >= FILTER_PASS 만 통과.

    client 는 덕타이핑(system_one(state, questions, model=...) → resp.answers["about"].noul).
    호출 실패(TypeSafeError)는 탈락 처리하고 에러명을 남긴다.
    cache(get/put) 히트 시 호출 없이 캐시 noul 을 쓴다. 에러는 캐시 안 함.
    """
    out = []
    for post in posts:
        row = dict(post)
        key = filter_cache_key(post["text"], mapping) if cache is not None else None
        if cache is not None:
            hit = cache.get(key)
            if hit is not None:
                noul = float(hit)
                row["about_noul"] = noul
                row["passed"] = noul >= FILTER_PASS
                row["ambiguous"] = AMBIG_LO <= noul <= AMBIG_HI
                out.append(row)
                continue
        try:
            resp = client.system_one(
                anonymize(post["text"], mapping), filter_question(), model=JEV_MODEL
            )
            noul = float(resp.answers["about"].noul)
            row["about_noul"] = noul
            row["passed"] = noul >= FILTER_PASS
            row["ambiguous"] = AMBIG_LO <= noul <= AMBIG_HI
            if cache is not None:
                cache.put(key, noul)
        except typesafe_sdk.TypeSafeError as e:
            row["about_noul"] = None
            row["passed"] = False
            row["ambiguous"] = False
            row["error"] = type(e).__name__
        out.append(row)
    return out


def _collapse(text: str) -> str:
    """공백 뭉개기 — 개행·연속 공백을 1칸으로."""
    return " ".join(text.split())


def build_state(
    pct: float, sections: list[tuple[str, list[dict]]], mapping: dict[str, str]
) -> tuple[str, dict]:
    """state_text 조립 — 가격 1줄 + 섹션별 최신순 글.

    섹션 출력 순서: [신호일], [신호일 장마감 후], [이전 2일].
    글이 없는 섹션은 "관련 글 없음" 1줄.
    합계가 STATE_TOKEN_LIMIT 을 넘기면 그 글부터 뒤 섹션까지 전부 중단(truncated).
    """
    by_label = dict(sections)
    ordered = [lb for lb in SECTION_ORDER if lb in by_label]
    ordered += [lb for lb, _ in sections if lb not in SECTION_ORDER]
    text = price_sentence(pct)
    n_included = 0
    truncated = False
    n_by_section: dict[str, int] = {}
    for label in ordered:
        posts = by_label[label]
        if posts and all("posted_at_kst" in p for p in posts):
            posts = sorted(posts, key=lambda p: p["posted_at_kst"], reverse=True)
        else:
            posts = posts[::-1]
        lines = [label]
        included = 0
        if not truncated:
            for post in posts:
                line = "- " + _collapse(anonymize(post["text"], mapping))
                if approx_tokens(text + "\n" + "\n".join([*lines, line])) > STATE_TOKEN_LIMIT:
                    truncated = True
                    break
                lines.append(line)
                included += 1
                n_included += 1
        if included == 0:
            lines.append("관련 글 없음")
        text = text + "\n" + "\n".join(lines)
        n_by_section[label] = included
    stats = {
        "n_passed": sum(len(posts) for _, posts in sections),
        "n_included": n_included,
        "truncated": truncated,
        "n_by_section": n_by_section,
    }
    return text, stats


def shuffle_posts(
    groups: dict[str, list[dict]], price_bucket: dict[str, str], seed: int
) -> tuple[dict[str, str], list[str]]:
    """비교군 I용 글 섞기 — 같은 가격 구간 안에서 목적지→출처 배정만 바꾼다.

    호출자는 dst 의 I state 를 posts groups[src] (assign[dst] = src) 로 조립한다:
    src 의 이름 매핑으로 익명화 → "종목A", 가격 문장은 dst 것.
    Sattolo 순열이라 다수 구간에서는 어느 티커도 자기 글을 안 가진다.
    1개뿐인 구간은 자기 자신에 두고(identity) unswapped 로 돌려준다.
    빈 글 목록도 정상 참가.
    """
    rng = random.Random(seed)
    buckets: dict[str, list[str]] = {}
    for ticker in groups:
        buckets.setdefault(price_bucket.get(ticker), []).append(ticker)
    assign: dict[str, str] = {}
    unswapped: list[str] = []
    for tickers in buckets.values():
        if len(tickers) < 2:
            for t in tickers:
                assign[t] = t
                unswapped.append(t)
            continue
        # Sattolo — 단일 사이클이라 고정점 없음(derangement)
        idx = list(range(len(tickers)))
        for i in range(len(idx) - 1, 0, -1):
            j = rng.randrange(i)
            idx[i], idx[j] = idx[j], idx[i]
        for dst, src in zip(tickers, (tickers[i] for i in idx)):
            assign[dst] = src
    return assign, unswapped
