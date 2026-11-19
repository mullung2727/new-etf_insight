# PLAN_RECORDER_REBOUND_CAND — 리서치 녹화기에 되돌림 후보 종목 추가 + 녹화 이유 표

한 줄: **되돌림이 아침 후보 계산 때 후보 종목코드 파일을 남기면, 리서치 녹화기(0B 틱+0D 호가)가 1분마다 그 파일을 보고 새 종목을 녹화 대상에 더한다. 데이터는 종목당 한 벌만 쌓고, 왜 녹화했는지는 새 `codes` 표(code·tag·added_rt)에 따로 적는다.**

배경 (2026-11-19 측정): 되돌림 백테스트 체결(2026-06~) 중 녹화기 대상(전일 키움 거래량 순위 상위 30)이었던 비율 — 기존 방식 36/48, 상승일 방식 20/23, **빈자리 방식 1/19**. 빈자리 종목은 거래대금·회전율 순위라 녹화 대상과 거의 안 겹침.

## 진행
- [x] 구현·테스트 머지 (PR #167, 2026-11-19 — CodeRabbit 3회 지적 반영: 재기동 시 후보 재등록·codes 쓰기 실패 재시도·시작 tags 재시도, archive 정렬 버그. 되돌림 후보 파일 저장은 비공개 order.py `write_cand_file`, 11-19 20:40 live 반영)
- [x] 운영 등록 (스케줄 작업·config) — 해당 없음 (기존 orderbook-research-recorder·orderbook-archive·rebound-order 그대로)
- [ ] 첫 실가동 확인 (날짜)
- [x] 설계와 달라진 점을 "구현 차이" 절에 반영

---

## 1. 규칙 (누가 정했나)

| # | 항목 | 내용 | 정함 |
|---|---|---|---|
| R1 | 대상 추가 | 되돌림 후보(시가 확인 전, 기존·빈자리·상승일 후보 전부)를 녹화 대상에 더한다 | 사용자 |
| R2 | 중복 저장 금지 | 한 종목이 여러 이유로 걸려도 `trade`·`book` 데이터는 한 벌. 이미 녹화 중인 종목은 다시 등록하지 않는다 | 사용자 |
| R3 | 이유 구분 표시 | 새 표 `codes(code TEXT, tag TEXT, added_rt INTEGER, PRIMARY KEY(code, tag))`. 겹치면 같은 code 에 tag 행이 여러 개 | 사용자(구분·겹침 표시)·Claude(표 구조) |
| R4 | tag 값 | `vol30`(기존 상위 30) · `etf`(지수 ETF) · `rebound_H` · `rebound_F` · `rebound_V3` | Claude |
| R5 | added_rt | 그 종목·이유를 녹화 대상에 넣은 시각(epoch ns, `trade.rt` 와 같은 단위). 시작 대상은 녹화 시작 시각 | Claude |
| R6 | 후보 파일 | `db_dir()/orderbook_research/rebound_cand_YYYYMMDD.csv`, 헤더 `code,src`(src = H/F/V3). 임시 파일에 쓴 뒤 이름 바꾸기(원자적) | Claude |
| R7 | 파일 확인 주기 | 녹화 중 60초마다, 녹화 창 끝까지(시각 마감 없음 — 수능일처럼 장이 밀려도 그대로 동작) | Claude |
| R8 | 등록 상한 | 녹화 대상 전체 100종목 초과 시 초과분은 등록 안 하고 로그 1줄 (`codes` 행도 안 넣음) | Claude |
| R9 | 실패 처리 | 파일 없음·형식 오류·6자리 코드 아닌 행 → 그 행/파일 무시, 기존 녹화 계속. 추가 등록 실패(reg 응답 오류)도 녹화 중단 사유 아님, 로그 1줄 | Claude |

## 2. 코드 변경 (파일:함수)

| 파일 | 변경 |
|---|---|
| `etl/scripts/run_orderbook_research_recorder.py` `open_day_db` | `codes` 표 `CREATE TABLE IF NOT EXISTS` |
| 같은 파일 `_run` | 시작 대상에 tag 부여: `pick_codes` 결과 → `vol30`, `ETF_CODES` → `etf` (둘 다면 두 행) |
| 같은 파일 `run_recording` | 시작 시 `codes` 행 기록. 60초마다 `read_rebound_cand(path)` → 새 (code, tag) 만 `codes` 행 추가, 녹화 목록에 없는 code 만 `sess.reg([...])` 후 녹화 목록(`codes` 리스트)에 append — 재접속 때 다시 등록되는 목록과 같은 리스트 |
| 같은 파일 새 함수 `read_rebound_cand(path) -> list[tuple[str, str]]` | csv 읽기, (code, `rebound_<src>`) 목록. 오류·없음 → `[]` |
| `etl/scripts/orderbook_archive.py` `TABLES` | `("trade", "book", "gaps", "codes")`. 옛 파일은 `_table_exists` 로 건너뜀(기존 동작) |
| `research/private/intraday_rebound/live/order.py` `main` (비공개, gitignore) | `signal.precompute` 직후 후보 `ticker,src` 를 R6 파일로 저장. 저장 실패는 주문 흐름 막지 않음(로그만) |

- csv 는 archive 대상 아님(`_FILE_RE` 가 `orderbook_raw_YYYYMMDD.sqlite3` 만 매칭 — `orderbook_archive.py:44`). 하루 수십 바이트라 정리 안 함.
- 녹화기 창 시작(`WINDOW_START` 08:30)이 되돌림 계산(08:40)보다 먼저라 시작 대상엔 못 넣음 → R7 확인 루프로 추가. 되돌림 후보의 08:40 전 데이터는 `vol30` 와 겹치는 종목만 있음.

## 3. 테스트 (요구사항 → 테스트)

| 요구 | 테스트 (`etl/tests/test_orderbook_research_recorder.py`) |
|---|---|
| R3 표 생성 | `test_codes_table_created` |
| R4 시작 tag (상위30·ETF 겹침 시 두 행) | `test_start_tags_vol30_etf_overlap` |
| R1·R2 파일 종목 추가, 이미 녹화 중이면 reg 안 하고 codes 행만 | `test_cand_added_without_duplicate_reg` |
| R2 같은 파일 다시 읽어도 행·reg 중복 없음 | `test_cand_reread_idempotent` |
| 재접속 때 추가 종목도 다시 등록 | `test_reconnect_reregisters_added` |
| R8 100 초과 거부 | `test_cap_100` |
| R9 파일 없음·깨짐·잘못된 코드 → 녹화 계속 | `test_bad_cand_file_ignored` |
| R9 추가 reg 실패 → 녹화 계속 | `test_add_reg_failure_continues` |
| archive 가 codes 변환, 옛 파일(표 없음) 통과 | `test_orderbook_archive.py::test_codes_table_archived`, `test_old_file_without_codes` |
| order.py 후보 파일 쓰기·실패 무시 | `research/private/intraday_rebound/live/tests` 에 `test_write_cand_file` (비공개) |

## 4. 반영·확인

- 녹화기·archive: worktree 브랜치 → PR → 머지. 녹화기는 매일 08:20 새로 뜨므로 머지 다음 날부터 적용. 장중(08~20시) 머지 금지 — 돌고 있는 녹화기는 영향 없지만 확인 편의상 20시 후.
- order.py: 20시 후 백업(`_bak_YYYYMMDD_candfile`) → muse 수정 → live 테스트.
- 첫 실가동 확인: 다음 거래일 20시 이후 `orderbook_raw_YYYYMMDD.sqlite3` 의 `codes` 행(tag 별 개수, rebound_* added_rt ≈ 되돌림 계산 시각), 추가 종목의 `trade`·`book` 행 존재, 다음 날 parquet 에 codes 있음.

## 5. 버린 안

- 되돌림 종목 전용 별도 녹화 DB/표: 겹치는 종목 데이터가 두 벌 → 사용자 기각(R2)
- `trade`·`book` 에 tag 열 추가: 행마다 반복 저장, 겹침 표현 불가
- 녹화기가 되돌림 코드를 직접 불러 후보 계산: 비공개 코드(gitignore)를 공개 스크립트가 import → 불가
- 09:10까지만 파일 확인: 수능일처럼 장이 밀리면 놓침 → 창 끝까지 60초 주기

## 구현 차이
- 원래 parquet 변환은 TABLES 에 codes 추가만 → 실제 `_order_by` 도 고침(code 있는 표면 무조건 `code, rt` 정렬 → rt 없는 codes 표에서 duckdb 오류, 새 테스트가 찾음)
- 원래 시작 tags 기록 실패는 로그만(R9) → 실제 주기 확인 때 재시도, 후보 행도 쓰기 실패 시 다음 확인 때 재시도·재기동 시 녹화 목록 기준으로 재등록 (CodeRabbit #167)
- MAX_CODES 100 은 키움 등록 한도 실측 없이 정한 값 그대로
