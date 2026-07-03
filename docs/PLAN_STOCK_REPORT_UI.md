# PLAN: 종목별 리포트 다운로드 화면

**요약: broker-web에 종목 검색(자동완성)+기간(시작/종료) 화면을 만들고, api(:8000)가 stock_names 자동완성·리포트 목록(다운로드 여부 표시)·백그라운드 다운로드(진행률 폴링)를 제공한다. 다운로드 코어는 이미 완성된 `download_naver_research.run_stock`(멱등) 재사용.**

## 왜 api(:8000)인가
- `krx_ohlcv.duckdb`(= `stock_names` 매핑) 이미 접근 → 자동완성 in-process.
- 라우터 패턴 + broker-web 프록시(company/corps/financial) 이미 존재.
- 리포트=데이터 도메인. broker(:8001)는 Kiwoom 트레이딩 전용(etl/duckdb 접근 없음).
- etl `download_naver_research`는 stdlib(urllib/re)만 씀 → api가 경로 추가해 직접 import 가능(서브프로세스 불필요, 진행률 추적에 유리).

## 백엔드 (api:8000) — `routers/research.py`

| 엔드포인트 | 역할 |
|---|---|
| `GET /research/search?q=` | stock_names(code/name) LIKE 조회 → `[{code,name}]` 자동완성 후보 |
| `GET /research/stock/{code}/reports?since&until` | `list_stock_reports` + 각 리포트 **downloaded 여부**(dest 존재) → 목록 + 요약(total/이미받음) |
| `POST /research/stock/{code}/download` (body: name?,since?,until?) | **백그라운드 작업 시작** → `{job_id}` 반환 |
| `GET /research/jobs/{job_id}` | 진행률 `{status, total, downloaded, skipped, failed, done, error?}` |

### 백그라운드 방식 (단일 사용자 로컬 → 최소구성)
- 인메모리 job 레지스트리 `dict[job_id] = {...상태}` + `threading.Thread`로 다운로드 루프 실행.
- 루프: `list_stock_reports(code,name,since,until)` → 각 건 `dest_path` 존재 시 skip, 아니면 `download_pdf` → 매 건 job 상태 갱신.
- 폴링으로 진행률 표시. (Celery/큐 = YAGNI, 재시작 시 job 소실 허용.)
- `run_stock`에 `on_progress(stats)` 콜백 추가하거나, api 레이어에서 `list_stock_reports`+`download_pdf` 직접 루프(진행률 갱신 위해 후자 선호).

### 멱등 / 저장소
- 저장: 기존 `etl/exports/stock_reports/<종목명>_<종목코드>/<날짜>_<증권사>_<researchId>.pdf` (일자별 배치와 **동일 경로 공유** → 교차 중복제거 자동).
- 파일명 `researchId`가 고유키 → 이미 받은 건 무조건 스킵(함수 구현 완료).
- "이미 받은 리스트 확인" = reports 엔드포인트가 목록 각 건에 `downloaded:true/false` 부착(디스크 대조).

## 프론트 (broker-web) — `app/research/page.tsx` + `app/api/research/*` 프록시
- 종목 입력(코드/명) + 자동완성 드롭다운(`/research/search` 디바운스 호출).
- 시작/종료 = 네이티브 `<input type="date">` 2개(라이브러리 없음).
- **조회**: reports 목록 → 표(제목/증권사/날짜 + 다운로드됨 표시). 이미 받은 건 시각 구분.
- **다운로드**: POST → job_id → `/jobs/{id}` 폴링(1~2s) → 진행바 → 완료 요약. 이미 받은 건은 skip 카운트.

## 단계 (TDD, 단계별 보고)
1. **api 백엔드**: research 라우터 4엔드포인트 + job 레지스트리 + 진행률. 유닛테스트(fetch/download 주입). main.py 등록.
2. **broker-web 페이지**: 프록시 route + page.tsx(자동완성/날짜/조회표/진행바).
3. **e2e 검증**: 서버 3종 실행, 실제 종목 조회→다운로드→진행률 확인(사용자 화면 확인 필요).

## 결정 (확정)
1. **저장소**: 기존 `exports/stock_reports/` **공유** — 일자배치와 자동 중복제거. ✅
2. **job 소실 허용**: api 재시작 시 진행상태만 소실(파일 유지). ✅ 단 "이미 받은 건"은 `reports` 목록의 `downloaded` 플래그로 항상 확인 가능하고, 다운로드 시 스킵.
3. **동시 최대 3개**: 활성 job 3 초과 요청은 거절(HTTP 429/busy). 종목 내 다운로드는 순차(예의).
4. 프론트 검증: dev 서버 + 사용자 화면 확인.

## 소요시간(예측)
- 신규 1건 ≈ 0.6~0.9초(PDF fetch + throttle 0.4s). 이미 받은 건 즉시 스킵.
- 6개월치(≈60건) 첫 다운로드 ≈ ~50초. 재조회(다 받음) = 몇 초.
- 3종목 동시 = 병렬, 벽시계 ≈ 최장 종목.

## 재사용 (이미 완료)
- `download_naver_research`: `list_stock_reports(code,name,since,until,max_pages)`, `run_stock(...)`, `download_pdf`, `dest_path`, `sanitize`, stock_names 매핑. 13 tests green, 라이브 검증됨. CLI도 동작.
