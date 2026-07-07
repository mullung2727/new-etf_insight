"""etl/scripts 공통 부트스트랩 — cp949 stdout 가드 + sys.path 보장.

scripts 는 두 방식으로 실행된다:
  - 테스트/`-m`: cwd=etl → `from scripts.X import ...` (패키지 스타일)
  - ops 러너/직접: `python scripts\foo.py` → sys.path[0]=scripts/ → `from X import ...` (bare)
기존엔 스크립트마다 `try: from scripts.X except ImportError: from X` 폴백 + cp949 3줄을
복붙했고, 폴백 체인이 러너별로 깨졌다(pytest에서 붕괴 실측). 이 모듈이 etl/·scripts/
둘 다 sys.path 에 넣어 `from scripts.X` 와 bare `from X` 가 **모든 실행 모드에서** 풀리게
하고, 비-ascii print 크래시(cp949) 가드를 건다.

사용 (스크립트 맨 앞, 경로 독립 2줄):
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import _bootstrap  # noqa: F401  (cp949 가드 + path)
그 뒤부터는 bare import (`from notify import notify`) 로 통일 — 폴백 불필요.
"""
import pathlib
import sys

_scripts = pathlib.Path(__file__).resolve().parent
_etl = _scripts.parent
# etl/(패키지 스타일 from scripts.X) + scripts/(bare from X) + src/(new_etf_insight,
# 미설치 폴백) 모두 sys.path 에. new_etf_insight 는 보통 uv sync 로 설치돼 있지만
# 직접 .venv 실행 등 미설치 경로 대비.
_paths = [str(_etl), str(_scripts)]
_src = _etl / "src"
if _src.is_dir():
    _paths.append(str(_src))
for _p in _paths:
    if _p not in sys.path:
        sys.path.insert(0, _p)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 크래시 가드
