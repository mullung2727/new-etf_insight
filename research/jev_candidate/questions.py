"""Jev 2차 질문 v2 (10개, q7 제거) + 원점수·규칙 — PLAN_JEV_CANDIDATE_JUDGE §6·§7.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m unittest research.jev_candidate.tests.test_questions
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.jev_candidate.state import JEV_MODEL

QUESTION_SET_VER = "v2"

# §6 표 그대로 — "이 종목"은 익명화 이름 종목A 로 적는다.
QUESTIONS: dict[str, dict] = {
    "q1": {
        "type": "score",
        "instructions": "신호일에 종목A 주가가 움직인 원인이 자료에 구체적으로 나타나 있다",
        "criteria": ["원인 없음", "추정 가능", "명시됨"],
    },
    "q2": {
        "type": "choice",
        "instructions": "신호일 주가 움직임의 주된 원인 종류",
        "criteria": {
            "earnings": "실적",
            "contract": "수주·계약",
            "policy": "정책·규제",
            "tech": "신제품·기술",
            "mna": "인수합병·지배구조",
            "macro": "산업·매크로",
            "other": "수급·기타",
        },
    },
    "q3": {
        "type": "score",
        "instructions": "재료가 회사의 실적·수요·판매가격·시장점유율·밸류에이션에 실질적으로 영향을 줄 크기",
        "criteria": ["미미", "보통", "큼"],
    },
    "q4": {
        "type": "noul",
        "instructions": "재료가 한 번의 발표로 끝나는 성격이다",
    },
    "q5": {
        "type": "noul",
        "instructions": "실적발표·회의·정책결정·제품출시·계약 같은 앞으로 예정된 구체적 후속 이벤트가 자료에 나타나 있다",
    },
    "q6": {
        "type": "noul",
        "instructions": "신호일 섹션의 핵심 재료는 이전 2일 섹션에 없던 새로운 정보다",
    },
    "q8": {
        "type": "score",
        "instructions": "자료가 종목A를 해당 재료의 대장주·대표주로 서술하는 정도",
        "criteria": ["아니다", "후보로 언급", "명확한 주도주"],
    },
    "q9": {
        "type": "score",
        "instructions": "핵심 재료를 뒷받침하는 출처의 공신력",
        "criteria": ["루머·커뮤니티", "언론 보도", "공시·회사 발표"],
    },
    "q10": {
        "type": "noul",
        "instructions": "자료에 상승 논리를 약화시키는 의미 있는 반대 재료가 있다"
        " (유상증자·CB·대주주 매도·소송·실적 악화·규제·경쟁 심화·수요 둔화 등)",
    },
    "q11": {
        "type": "score",
        "instructions": "자료 전체를 종합할 때 이 재료와 종목A에 대한 시장 관심이"
        " 다음 거래일까지 이어질 구체적 근거의 강도",
        "criteria": ["없음", "약함", "강함"],
    },
}

_SCORE_QIDS = ("q1", "q3", "q8", "q9", "q11")


def answer_cache_key(state_text: str, model: str = JEV_MODEL) -> str:
    """정확 입력 키 — state_text + QUESTIONS + model 전체 sha1."""
    payload = {"state": state_text, "questions": QUESTIONS, "model": model}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _tokens(obj: Any, key: str) -> Any:
    """usage 토큰 읽기 — 객체·dict·None 모두 받는다."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def ask(
    client: Any, state_text: str, model: str = JEV_MODEL, cache: Any = None
) -> tuple[list[dict], dict]:
    """2차 10문항 1회 호출 — 행 정규화 + usage 반환. 예외는 호출자가 처리.

    cache(get/put) 히트 시 호출 없이 (rows, 0토큰 usage) 반환.
    """
    key = answer_cache_key(state_text, model) if cache is not None else None
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit, {"input_tokens": 0, "output_tokens": 0, "cached": True}
    resp = client.system_one(state_text, QUESTIONS, model=model)
    answers = getattr(resp, "answers", {}) or {}
    rows: list[dict] = []
    for qid, q in QUESTIONS.items():
        qtype = q["type"]
        ans = answers[qid]
        if qtype == "score":
            value = float(ans.score)
            confidence = getattr(ans, "confidence", None)
            probs = {str(k): v for k, v in dict(ans.probabilities).items()}
        elif qtype == "choice":
            value = ans.choice
            confidence = getattr(ans, "confidence", None)
            probs = {str(k): v for k, v in dict(ans.probabilities).items()}
        else:
            value = float(ans.noul)
            confidence = None
            probs = {"yes": value}
        rows.append(
            {"qid": qid, "type": qtype, "value": value, "confidence": confidence, "probs": probs}
        )
    usage = getattr(resp, "usage", None)
    out = {
        "input_tokens": _tokens(usage, "input_tokens"),
        "output_tokens": _tokens(usage, "output_tokens"),
    }
    if cache is not None:
        cache.put(key, rows, out)
    return rows, out


def _by_qid(rows: list[dict]) -> dict[str, dict]:
    """행 목록 → qid 매핑."""
    return {r["qid"]: r for r in rows}


def _unknown(row: dict) -> bool:
    """불명 게이트 (§7) — score·choice 는 confidence < 0.5, noul 은 0.35~0.65."""
    t = row.get("type")
    if t in ("score", "choice"):
        c = row.get("confidence")
        return c is None or c < 0.5
    if t == "noul":
        v = float(row.get("value"))
        return 0.35 <= v <= 0.65
    return True


def jev_score(rows: list[dict]) -> float:
    """§7 A — score/2 합 + q5 − q4 − q10. 불명 항은 0."""
    by = _by_qid(rows)
    total = 0.0
    for qid in _SCORE_QIDS:
        r = by.get(qid)
        if r is None or _unknown(r):
            continue
        total += float(r["value"]) / 2
    for qid, sign in (("q5", 1.0), ("q4", -1.0), ("q10", -1.0)):
        r = by.get(qid)
        if r is None or _unknown(r):
            continue
        total += sign * float(r["value"])
    return total


def rule_buy(rows: list[dict]) -> bool:
    """v2 — q1>=1.0 AND q11>=0.5 AND q10<0.3. 불명 답이 낀 조건은 False."""
    by = _by_qid(rows)

    def _score_ge(qid: str, thr: float) -> bool:
        r = by.get(qid)
        return r is not None and not _unknown(r) and float(r["value"]) >= thr

    def _noul_lt(qid: str, thr: float) -> bool:
        r = by.get(qid)
        return r is not None and not _unknown(r) and float(r["value"]) < thr

    return (
        _score_ge("q1", 1.0)
        and _score_ge("q11", 0.5)
        and _noul_lt("q10", 0.3)
    )
