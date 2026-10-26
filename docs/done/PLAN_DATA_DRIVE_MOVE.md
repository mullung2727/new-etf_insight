# 데이터를 새 SSD(E:)로 이전 + E: 폴더 통째 외장하드 백업

한 줄: 코드는 C: 저장소에 두고, DB·리포트 PDF·호가 원본 등 git 밖 데이터는 E:\new-etf_insight_data 로 옮긴다. 경로는 공용 함수 하나(`data_paths`)가 정하고, 외장하드(D:)는 E: 폴더의 사본이 된다.

## 진행
- [x] 구현·테스트 머지 (PR #110, 후속 PR #111 경로 누락분)
- [x] 운영 등록 (2026-10-18) — .env NEW_ETF_DATA_ROOT=E:\new-etf_insight_data, daily-code-backup 23:30 등록·1회 실행 성공
- [x] 첫 실가동 확인 (2026-10-19~ 서버·배치가 E: 원본으로 운영, 첫 E:→D: 백업 2026-10-24 weekly-db-backup slot_B 완료·`latest.txt`=B)
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

## 1. 배경 (2026-10-18 실측)
- C: 233GB 중 여유 16.8GB. 프로젝트 39.4GB = `etl/db` 27GB · `etl/exports`(리포트 PDF) 6.2GB(월 1~2GB 증가) · research 2.9GB · temp 1.3GB · 기타.
- 새 내장 SSD E: 465GB 비어 있음. 외장하드 D: 566GB 여유(빼두면 없음, 다시 꽂으면 D 로 복귀).
- 데이터 경로가 코드에 직접 박힌 파일: etl/scripts 약 50, research 약 20, api 6, broker 1, ops 3 (grep `"db"|etl/db|exports|D:/`).
- D: 에 직접 쓰는 원본: `orderbook_archive.py` `DEFAULT_DST = D:/orderbook`, `build_us_facts.py` `RAW_ROOT = D:/sec_edgar`, `research/ccc/factors.py` `D:/ken_french`.
- etl/scripts 77개 중 50개는 `load_dotenv` 를 안 부른다 → 경로 설정을 env 로만 받으면 일부 배치가 기본값(C:)에 계속 쓴다.

## 2. 확정 결정
| # | 결정 | 누가 |
|---|---|---|
| D1 | 코드(git)·`.env`·`research/private` 는 C: 저장소에 그대로. 나머지 git 밖 데이터는 E: 로 이동 | 사용자 |
| D2 | `.env`·`research/private` 는 E: 에 사본을 둔다 | 사용자 |
| D3 | 백업 원칙 = E: 의 프로젝트 데이터 폴더를 그대로 외장하드로 복사 | 사용자 |
| D4 | 경로는 링크(junction) 말고 공용 함수 한 곳에서 정한다 (2026-10-16 junction 삭제 사고) | 사용자 |
| D5 | E: 루트 `E:\new-etf_insight_data\` | 내 판단 |
| D6 | 설정 키 `NEW_ETF_DATA_ROOT` 를 루트 `.env` 에 둔다. 공용 함수가 `.env` 를 **직접** 읽는다(호출 스크립트의 load_dotenv 여부 무관) | 내 판단 |
| D7 | 키가 없으면 지금 경로(저장소 안) 그대로 = 테스트·worktree 영향 없음. 키가 있는데 그 폴더가 없으면(E: 인식 실패) **예외로 중단**, C: 로 폴백 금지 | 내 판단 |
| D8 | DB 백업은 단순 복사 금지 — 기존 `backup_db.py` 방식(sqlite 백업 API·행 수 비교, duckdb 복사·행 수 비교) 유지, 원본 위치만 E: | 내 판단 |
| D9 | 호가 parquet·SEC 원본·Ken French 원본도 원본은 E:, D: 는 사본 | 내 판단 (D3 원칙 적용) |

## 3. E: 폴더 구조
```
E:\new-etf_insight_data\
  db\            ← etl/db (sqlite·duckdb·ticks·orderbook_raw·orderbook_research)
  exports\       ← etl/exports (stock_reports·sector_reports 등)
  runs\          ← etl/runs (ETF 파이프라인 JSON)
  orderbook\     ← D:\orderbook (호가 parquet)
  sec_edgar\     ← D:\sec_edgar
  ken_french\    ← D:\ken_french
  code_backup\   ← .env, research\private 사본 (D2)
```
- `etl/logs`(86MB)·`temp/`·`research` 결과물은 대상 아님. temp·오래된 결과물 정리는 별도.

## 4. 코드
### 4.1 공용 함수 `data_paths.py` (etl/src/new_etf_insight/data_paths.py, stdlib 만)
- `data_root() -> Path | None`: 환경변수 → 없으면 루트 `.env` 에서 `NEW_ETF_DATA_ROOT=` 한 줄 직접 파싱. 없으면 None. 있는데 폴더 없으면 `RuntimeError`.
- `db_dir()`, `exports_dir()`, `runs_dir()`, `orderbook_dir()`, `sec_edgar_dir()`, `ken_french_dir()`: root 있으면 그 아래, 없으면 지금 기본값(`<repo>/etl/db` 등, D: 계열은 지금 D: 경로).
- etl venv 는 패키지 설치로 import. api·broker venv 는 파일 상단에서 <repo>/etl/src 를 sys.path 에 넣고 import (stdlib 만이라 가능).

### 4.2 호출부 교체
- etl/scripts·research·api·broker·ops 의 하드코딩 경로를 위 함수로 교체. 기존 per-DB env 오버라이드(`KRX_DB_PATH` 등)는 우선순위 유지(env > data_paths 기본).
- `.ps1` 러너가 경로를 직접 쓰는 곳(3개)은 파이썬 기본값에 맡기거나 같은 키를 읽게.
- 범위가 커서 PR 을 나눈다: P1 함수+etl/scripts, P2 api·broker·research·ops. 각 PR 끝에서 grep 으로 남은 하드코딩 0 확인.

### 4.3 백업 `backup_db.py`
- 원본 = `data_root()` 폴더 전체(D3). 대상 = 외장 `new-etf_insight_backup\slot_A|B` 교대(기존).
- 시작 시 `.env`·`research/private` 를 `code_backup\` 로 먼저 복사(D2). 코드 사본은 매일 1회 가벼운 작업으로도 복사(백업 주기가 월 1회라).
- 기존 slot 의 옛 구조(저장소 상대 경로)는 첫 성공 뒤 정리 여부 사용자 확인.

### 4.4 호가 아카이브 `orderbook_archive.py`
- 대상 `orderbook_dir()`(E:). 여유 공간 검사도 E:. D: 사본은 4.3 백업이 만든다.

### 4.5 research/private (git 밖, 실매매 코드 포함)
- 2026-10-18 grep 기준 약 55개 파일이 `etl/db` 등을 직접 쓴다(jev·유증·엔벌로프·초소형 live 등). PR 로 못 다루므로 이전 당일 같은 방식으로 교체하고 그 폴더 테스트를 돌린다.
- 평일 장중 실매매가 이 코드를 쓰므로, 교체는 장 마감 뒤 이전 작업 때 한 번에(미리 바꿔 두다 import 가 깨지면 다음 날 주문 실패).

## 5. 이전 절차 (서버·배치 정지 상태, 일요일 또는 20:10~02:00)
1. P1·P2 머지 전, `NEW_ETF_DATA_ROOT` 미설정 상태로 배포 → 동작 변화 없음 확인.
2. 서버 정지, 해당 시간 배치 없음 확인(녹화기·아카이브·분봉 02:00).
3. robocopy C:→E: (db·exports·runs), D:→E: (orderbook·sec_edgar·ken_french). D: 는 드라이브 최적화 끝난 뒤.
4. 검증: 파일 수·크기 일치, sqlite/duckdb 테이블별 행 수 원본=사본.
5. `.env` 에 `NEW_ETF_DATA_ROOT=E:\new-etf_insight_data` → 서버 재기동 → API·웹·배치 1개 수동 실행 확인.
6. C: 원본은 `etl/db_moved_YYYYMMDD` 등으로 이름만 바꿔 1주 보관(새로 C: 에 쓰는 코드가 있으면 `etl/db` 가 다시 생겨 드러남) → 1주 뒤 링크 검사 후 삭제.
7. 첫 E:→D: 백업 수동 실행, 결과 확인.

## 6. 테스트
| 요구 | 테스트 |
|---|---|
| 키 없으면 기존 경로 | `data_paths` 단위: env·.env 없음 → `<repo>/etl/db` |
| `.env` 직접 읽기 | 임시 .env 에 키 → load_dotenv 없이도 해당 경로 |
| E: 없으면 C: 폴백 금지 | 키가 없는 폴더 → RuntimeError |
| per-DB env 우선 | `KRX_DB_PATH` 설정 시 그 값 |
| 하드코딩 0 | grep 검사 테스트(허용 목록 외 `etl/db`·`D:/` 문자열 실패) |
| 백업 원본=data_root, 코드 사본 포함 | backup_db 임시 폴더 테스트 |
| 아카이브 대상 E: | orderbook_archive 기본 dst = orderbook_dir() |

## 7. 위험
- 놓친 하드코딩 경로 → C: 에 새 DB 가 생겨 데이터가 갈라짐. 대응: grep 테스트 + 5-6 이름 변경 감지.
- E: 문자가 바뀜 → D7 예외로 바로 드러남(조용히 C: 에 안 씀).
- 외장 D: 를 뺀 동안 백업 실패 알림(정상).

## 구현 차이
- 이전 당일(2026-10-18 일) 실측: DB 26.6GB C:→E: robocopy 55초(약 650MB/s). 파일 수·크기 전부 일치, DB 20개 테이블별 행 수 일치.
- research/private 는 계획대로 이전 당일 54개 파일 교체(원본 `_backup_20261018_datapaths`), 그 폴더 테스트 통과.
- 루트 `.env` 의 `KRX_DB_PATH`·`WATCHLIST_DB_PATH`(`./etl/db/…`)가 data_paths 를 이겨 /watchlist 500 → 두 줄 삭제. DB별 env 는 넣지 않는 걸 규칙으로(etl/AGENTS.md).
- P1·P2 grep 이 놓친 cwd 상대경로 4곳(`"db/…"`, `Path("runs")`, ps1 `db\`·`runs\`) → PR #111 에서 수정, 검사 패턴 추가.
- C: 원본: `etl/db_moved_20261018`·`etl/runs_moved_20261018` 보관(10-25 이후 삭제). exports 사본은 E:·D: slot_A 확인 후 당일 삭제.
- D: 의 orderbook·sec_edgar·ken_french 옛 원본은 그대로 남음 — 정리 여부 미정.

