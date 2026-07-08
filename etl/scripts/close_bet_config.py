"""종가베팅 전략값 config 로더 — 웹(/admin/settings)이 쓰고 배치가 읽는 단일 소스.

파일: scripts/close_bet.json (broker-web/lib/close-bet-config.ts 와 같은 파일).
배치(run_close_bet / run_close_bet_exit / report_close_bet_order)가 실행 시점에 load()
로 읽어 전략값을 얻는다. 파일이 없거나 키가 빠지면 하드코딩 기본값(=기존 동작)으로 폴백해
config 없이도 배치가 그대로 돈다. 값이 범위를 벗어나면 ValueError 로 배치를 abort 해
깨진 값으로 실주문 하는 사고를 막는다(웹 저장단·py 로더 이중 방어).

사용 (scripts, _bootstrap 뒤):
    from close_bet_config import load
    cfg = load()
    threshold = cfg["score_threshold"]  # int
    budget = cfg["budget_by_count"][n]  # int, n∈{1,2,3}
"""
from __future__ import annotations

import json
from pathlib import Path

# 하드코딩 기본값 = 기존 배치 값(run_close_bet default 70, exit tp/sl, _BUDGET_BY_COUNT).
# config 파일/키가 없을 때 폴백 → config 도입 전과 동일 동작 보존.
DEFAULTS: dict = {
    "score_threshold": 70,
    "tp": 0.05,
    "sl": 0.03,
    "budget_by_count": {1: 3_000_000, 2: 2_000_000, 3: 5_000_000 // 3},
}

_COUNTS = (1, 2, 3)


def PATH() -> Path:
    """config 파일 경로 (이 모듈과 같은 scripts/ 디렉터리)."""
    return Path(__file__).resolve().parent / "close_bet.json"


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _validate(cfg: dict) -> None:
    """범위 검증. 실패 시 ValueError(배치 abort)."""
    st = cfg["score_threshold"]
    if not _is_int(st) or not (0 <= st <= 100):
        raise ValueError(f"score_threshold must be int 0~100, got {st!r}")
    for k in ("tp", "sl"):
        v = cfg[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 <= v <= 1):
            raise ValueError(f"{k} must be number 0~1, got {v!r}")
    budget = cfg["budget_by_count"]
    if not isinstance(budget, dict):
        raise ValueError(f"budget_by_count must be dict, got {budget!r}")
    for n in _COUNTS:
        if n not in budget:
            raise ValueError(f"budget_by_count missing key {n}")
        amt = budget[n]
        if not _is_int(amt) or amt <= 0:
            raise ValueError(f"budget_by_count[{n}] must be positive int, got {amt!r}")


def load(path: Path | None = None) -> dict:
    """config 로드 → 범위검증된 dict. 파일/키 없으면 기본값 폴백."""
    p = path or PATH()
    raw: dict = {}
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))

    cfg = {
        "score_threshold": raw.get("score_threshold", DEFAULTS["score_threshold"]),
        "tp": raw.get("tp", DEFAULTS["tp"]),
        "sl": raw.get("sl", DEFAULTS["sl"]),
    }
    if "budget_by_count" in raw:
        # JSON 키는 문자열 → int 키로 변환(배치는 int n 으로 조회).
        cfg["budget_by_count"] = {int(k): v for k, v in raw["budget_by_count"].items()}
    else:
        cfg["budget_by_count"] = dict(DEFAULTS["budget_by_count"])

    _validate(cfg)
    return cfg


if __name__ == "__main__":  # 셀프체크
    import tempfile

    def _tmp(cfg: dict) -> Path:
        p = Path(tempfile.mktemp(suffix=".json"))
        p.write_text(json.dumps(cfg), encoding="utf-8")
        return p

    # 정상
    ok = load(_tmp({"score_threshold": 50, "tp": 0.05, "sl": 0.03,
                    "budget_by_count": {"1": 3000000, "2": 2000000, "3": 1666666}}))
    assert ok["score_threshold"] == 50 and ok["budget_by_count"][1] == 3000000

    # 파일 없음 → 기본값
    assert load(Path(tempfile.gettempdir()) / "nope.json") == DEFAULTS

    # 범위 초과
    for bad in ({"score_threshold": 101}, {"tp": 2}, {"sl": -1},
                {"budget_by_count": {"1": 1, "2": 0, "3": 1}}):
        try:
            load(_tmp(bad))
            raise AssertionError(f"expected ValueError for {bad}")
        except ValueError:
            pass

    print("[close_bet_config] self-check OK")
