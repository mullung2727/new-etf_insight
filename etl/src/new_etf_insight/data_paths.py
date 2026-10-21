"""데이터 루트 공용 경로 함수 (설계: docs/PLAN_DATA_DRIVE_MOVE.md).

`NEW_ETF_DATA_ROOT` 가 있으면 그 아래 db/exports/runs/… 를 쓰고,
없으면 기존 경로(저장소 etl/ 아래, D: 계열은 기존 D: 경로) 그대로 쓴다.
키가 가리키는 폴더가 없으면 C: 로 폴백하지 않고 RuntimeError 로 중단한다.

stdlib 만 사용 — 호출 스크립트의 load_dotenv 여부와 무관하게 동작한다.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ETL_DIR = REPO_ROOT / "etl"
ENV_KEY = "NEW_ETF_DATA_ROOT"


def _read_env_file(path: Path) -> str | None:
    """루트 .env 에서 NEW_ETF_DATA_ROOT= 한 줄만 직접 파싱. 없으면 None."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if not sep or key.strip() != ENV_KEY:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value
    return None


def data_root() -> Path | None:
    """데이터 루트. 환경변수 → 루트 .env 순. 둘 다 없거나 빈 값이면 None.

    값이 있는데 폴더가 없으면 RuntimeError (C: 로 폴백하지 않음).
    매 호출마다 다시 읽는다 (캐시 없음).
    """
    value = os.environ.get(ENV_KEY)
    if value is None:
        value = _read_env_file(REPO_ROOT / ".env")
    if value is None or not value.strip():
        return None
    root = Path(value.strip())
    if not root.is_absolute():
        root = REPO_ROOT / root
    if not root.is_dir():
        raise RuntimeError(
            f"{ENV_KEY}={value} 폴더 없음 — 데이터 드라이브 인식 확인 (C: 로 폴백하지 않음)"
        )
    return root


def db_dir() -> Path:
    root = data_root()
    return root / "db" if root is not None else ETL_DIR / "db"


def exports_dir() -> Path:
    root = data_root()
    return root / "exports" if root is not None else ETL_DIR / "exports"


def runs_dir() -> Path:
    root = data_root()
    return root / "runs" if root is not None else ETL_DIR / "runs"


def orderbook_dir() -> Path:
    root = data_root()
    return root / "orderbook" if root is not None else Path("D:/orderbook")


def sec_edgar_dir() -> Path:
    root = data_root()
    return root / "sec_edgar" if root is not None else Path("D:/sec_edgar")


def ken_french_dir() -> Path:
    root = data_root()
    return root / "ken_french" if root is not None else Path("D:/ken_french")


def code_backup_dir() -> Path | None:
    root = data_root()
    return root / "code_backup" if root is not None else None
