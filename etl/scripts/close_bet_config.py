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
    cap_max = cfg["cap_max"]            # int, 원. 시총 상한(미만)
    tv_min = cfg["turnover_min"]        # int, 원. 전일 거래대금 하한(이상)
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
    # 여기 값은 자리표시자다. 실제 운영값은 close_bet.json(gitignore, 저장소가 공개라
    # 검증된 파라미터를 커밋하지 않는다) 에 있고 근거는 research/private/close_bet_overnight.
    # 파일이 없으면 이 값으로 폴백하는데 필터가 사실상 안 걸리므로, load() 가 경고를 낸다.
    "cap_max": 10_000_000_000_000,   # 10조 = 사실상 상한 없음
    "turnover_min": 1,               # 1원 = 사실상 하한 없음
    # 청산 워커(run_close_bet_exit)의 강제청산 시각. ps1 인자가 아니라 여기가 단일 소스라
    # 값만 바꾸면 다음 기동부터 반영된다.
    # ⚠ 백스톱 배치 시각(ops/scheduled-tasks/close-bet-force-exit.xml 의 StartBoundary)은
    #   스케줄러 소관이라 자동 연동되지 않는다. 이 값을 바꾸면 그 XML 도 같이 바꿀 것.
    "exit_time": "15:19:00",
}

_COUNTS = (1, 2, 3)


def PATH() -> Path:
    """config 파일 경로 (이 모듈과 같은 scripts/ 디렉터리)."""
    return Path(__file__).resolve().parent / "close_bet.json"


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_hms(x) -> bool:
    """'HH:MM:SS' 형식이고 실재하는 시각인지. 워커가 datetime.replace 로 쓰므로 범위까지 본다."""
    if not isinstance(x, str):
        return False
    parts = x.split(":")
    if len(parts) != 3 or not all(len(p) == 2 and p.isdigit() for p in parts):
        return False
    h, m, s = (int(p) for p in parts)
    return h <= 23 and m <= 59 and s <= 59


def _validate(cfg: dict) -> None:
    """범위 검증. 실패 시 ValueError(배치 abort)."""
    st = cfg["score_threshold"]
    if not _is_int(st) or not (0 <= st <= 100):
        raise ValueError(f"score_threshold must be int 0~100, got {st!r}")
    for k in ("tp", "sl"):
        v = cfg[k]
        if v is None:  # null = 장중 TP/SL 판정 끔(09:01 강제청산만)
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 <= v <= 1):
            raise ValueError(f"{k} must be number 0~1 or null, got {v!r}")
    for k in ("cap_max", "turnover_min"):
        v = cfg[k]
        if not _is_int(v) or v <= 0:
            raise ValueError(f"{k} must be positive int (원), got {v!r}")
    if not _is_hms(cfg["exit_time"]):
        raise ValueError(f"exit_time must be 'HH:MM:SS', got {cfg['exit_time']!r}")
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
    """config 로드 → 범위검증된 dict. 파일/키 없으면 기본값 폴백.

    close_bet.json 은 gitignore 라 새 체크아웃에는 없다. 그때 폴백되는 DEFAULTS 의
    cap_max·turnover_min 은 자리표시자(사실상 필터 없음)라 그대로 매매하면 안 된다.
    조용히 넘어가지 않도록 경고를 낸다 — close_bet.example.json 을 복사해 값을 채울 것.
    """
    p = path or PATH()
    raw: dict = {}
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8"))
    else:
        print(f"[close_bet] ⚠️ config 없음({p}) — 기본값 폴백. "
              f"시총·거래대금 필터가 사실상 비활성이다. close_bet.example.json 참고")

    cfg = {
        "score_threshold": raw.get("score_threshold", DEFAULTS["score_threshold"]),
        "tp": raw.get("tp", DEFAULTS["tp"]),
        "sl": raw.get("sl", DEFAULTS["sl"]),
        "cap_max": raw.get("cap_max", DEFAULTS["cap_max"]),
        "turnover_min": raw.get("turnover_min", DEFAULTS["turnover_min"]),
        "exit_time": raw.get("exit_time", DEFAULTS["exit_time"]),
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
                    "cap_max": 100_000_000_000, "turnover_min": 2_000_000_000,
                    "exit_time": "09:01:00",
                    "budget_by_count": {"1": 3000000, "2": 2000000, "3": 1666666}}))
    assert ok["score_threshold"] == 50 and ok["budget_by_count"][1] == 3000000
    assert ok["cap_max"] == 100_000_000_000 and ok["turnover_min"] == 2_000_000_000
    assert ok["exit_time"] == "09:01:00"

    # 파일 없음 → 기본값
    assert load(Path(tempfile.gettempdir()) / "nope.json") == DEFAULTS

    # 범위 초과
    for bad in ({"score_threshold": 101}, {"tp": 2}, {"sl": -1}, {"cap_max": 0},
                {"turnover_min": -1}, {"exit_time": "9:1"}, {"exit_time": "25:00:00"},
                {"budget_by_count": {"1": 1, "2": 0, "3": 1}}):
        try:
            load(_tmp(bad))
            raise AssertionError(f"expected ValueError for {bad}")
        except ValueError:
            pass

    print("[close_bet_config] self-check OK")
