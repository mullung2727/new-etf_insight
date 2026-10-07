"""스윙 후보 순위: 점수 합산·정렬·상위 선정. 순수 함수 + 조회 주입.

설계: docs/done/PLAN_SWING_PICK.md §0-2(총점·동점 규칙).

왜 순위 단계에서만 정리매매·테마를 조회하나:
- 후보 전부(하루 ~100종목)를 키움(ka10100·ka90001)으로 조회하면 호출 부담이
  크다. 2번 리스크 탈락 후 점수순 상위부터 최대 10개만 상태를 확인하고 3개를
  채우면, 버려질 종목의 조회가 사라진다.
- 상태 조회 실패(예외·None)는 제외 사유가 아니다. 상태 모름이 곧 위험은
  아니고, 2번 Jev가 이미 관리종목 지정 같은 이벤트를 봤기 때문이다. 그래서
  실패 종목은 통과시키고 status_unknown 비고만 남긴다 (fail open — Jev 실패의
  fail closed와 반대. Jev 실패는 희석 리스크를 모르는 종목이라 추천에서 뺀다).

이 모듈은 broker를 직접 안 부른다. get_status(ticker) -> dict를 인자로 받아
테스트에서 가짜로 대체한다.
"""
from __future__ import annotations

from typing import Callable


def total_score(row: dict) -> int:
    """s1+s3+s4+s5. None(판정 실패)은 0 — 실패 항목이 점수를 깎진 않지만
    더하지도 않는다. 오류 여부는 errors 컬럼이 따로 기록한다."""
    return sum(row.get(k) or 0 for k in ("s1", "s3", "s4", "s5"))


def rank_candidates(rows: list[dict]) -> list[dict]:
    """순위 대상만 골라 정렬한 새 리스트. 원본 순서는 안 건드린다.

    대상: excluded_reason is None이고 risk_out is False인 행.
    risk_out None = Jev 실패 → 호출 전에 excluded_reason='jev_error'로 이미
    빠져 있어야 한다 (희석 리스크를 모르는 종목은 추천하지 않음, fail closed).
    정렬: total 내림차순 → jev_sustain 내림차순(None은 뒤) → ticker 오름차순.
    """
    eligible = [
        r for r in rows
        if r.get("excluded_reason") is None and r.get("risk_out") is False
    ]

    def _key(r: dict):
        sustain = r.get("jev_sustain")
        total = r.get("total")
        if total is None:
            total = total_score(r)
        # jev_sustain None은 꼴찌 동률로 — (1, 0) vs 값 있음 (0, -값).
        sustain_key = (1, 0.0) if sustain is None else (0, -sustain)
        return (-total, sustain_key, r.get("ticker") or "")

    return sorted(eligible, key=_key)


def _status_reason(status: dict) -> str | None:
    """broker GET /quotes/{ticker}/status 응답 → 제외 사유. 통과면 None.

    order_warning '2' = 정리매매. audit_info는 감리구분 문자열이라 포함 검사로
    본다 ('관리종목'·'거래정지').
    """
    if str(status.get("order_warning") or "") == "2":
        return "정리매매"
    audit = str(status.get("audit_info") or "")
    if "관리종목" in audit:
        return "관리종목"
    if "거래정지" in audit:
        return "거래정지"
    return None


def pick_top(
    ordered: list[dict],
    get_status: Callable[[str], dict | None],
    k: int = 3,
    max_checks: int = 10,
) -> tuple[list[dict], dict[str, str]]:
    """앞에서부터 상태 확인하며 k개 선정. (선정 목록, {ticker: 제외/비고 사유}).

    - 제외 종목은 건너뛰고 다음 후보로 k개를 채운다.
    - 조회 실패(예외·None·dict 아님)는 제외하지 않고 통과 + 'status_unknown' 비고.
    - k개를 채우거나 max_checks번 조회하면 멈춘다. 후보가 k개 미만이면 있는 만큼만.
    - 반환 dict에는 제외 사유와 status_unknown만 들어간다. 깨끗하게 통과한
      종목은 기록이 없다 (사유 없음 = 정상 통과).
    """
    picked: list[dict] = []
    reasons: dict[str, str] = {}
    checks = 0
    for row in ordered:
        if len(picked) >= k or checks >= max_checks:
            break
        ticker = row["ticker"]
        # 조회 시도 횟수로 센다 — 실패한 호출도 broker를 때린 건 같아서다.
        # 성공만 세면 broker 장애 때 전 후보를 무의미하게 다 찌른다.
        checks += 1
        try:
            status = get_status(ticker)
            if status is None or not isinstance(status, dict):
                raise ValueError("status_unknown")
            reason = _status_reason(status)
        except Exception:
            # 상태 모름은 위험이 아님 (모듈 docstring) — 통과 + 비고.
            reasons[ticker] = "status_unknown"
            picked.append(row)
            continue
        if reason is None:
            picked.append(row)
        else:
            reasons[ticker] = reason
    return picked, reasons
