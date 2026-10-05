# 재무데이터 최초·정정 공시 이력 보존 및 과거 복구

- 작성·리뷰 반영일: 2026-10-04
- 상태: **설계 수정 / 목록·XBRL 실증 도구 구현·검수 완료 / 금액 복구 미착수**
- 대상: `etl/db/financial_indicators.sqlite3`의 주요 계정 금액과 DART 재무지표
- 기존 완료 문서: `docs/done/PLAN_FINANCIAL_COMPARE.md` (연간·분기 비교 화면 완료 기록)
- ETF 수집기·ETF DB는 대상이 아니다.

## 1. 목적과 작업 범위

목적은 최초·정정 공시 당시 공개된 재무 금액을 복구해 시점 기준 리서치·백테스트에 사용하고 공시 차수별 값과 수집 근거를 조회·감사하는 것이다. 사용 가능일은 해당 차수의 rcept_dt를 기준으로 정하며, 날짜만 확보한 경우 접수일 다음 거래일부터 사용한다. 수집시각은 감사·수집 추적용이다.

공시 목록·최초 접수일 확보 → XBRL 실증 → 과거 금액 복구를 우선한다. 미래 보존 완료를 과거 복구 착수의 전제로 두지 않는다. 다음 작업은 각각 완료 여부를 기록한다.

1. **공시 목록·최초 접수일 선행 확보:** 원문 없이 최초·정정 공시 목록을 독립 산출물로 먼저 제공한다. 회사·보고기간별 최초 접수일 후보와 조회 완전성을 구분한다. 조회 범위가 불완전하면 최초 확정으로 표시하지 않는다.

2. **과거 금액 복구:** 사용자가 요청한 과거 최초·정정 공시 복구를 유지한다. XBRL 실증 후 범위·비용·추출 방법을 확정하고 후속 복구 PLAN을 작성해 실행한다. 미래 응답 보존을 완료했다고 이 요구까지 완료 처리하지 않는다.
3. **미래 응답 보존:** 앞으로 확보한 주요계정 응답은 실제 접수번호별로, 주요지표 응답은 수집시점별 관측으로 보존한다. 수집 전에 지나간 모든 차수를 기간 API로 확보한다고 보장하지 않는다.

과거 DART 지표 차수 복구는 원본 근거가 확보되기 전까지 실행 범위에서 제외한다. 이는 복구 불가 확정이 아니라 보장하지 않는 범위 결정이다. 원문에 해당 지표와 차수 근거가 확인되면 범위를 확장한다. 복구의 중심은 시점 기준 금액이다. 리서치 지표는 그 금액으로 계산할 수 있으며 계산한 지표를 DART 원본 지표로 저장하지 않는다.

목록만 저장한 상태를 과거 재무 값 복구 완료라고 부르지 않는다. 최신 API 값을 옛 공시번호에 붙이는 것도 복구로 인정하지 않는다.

## 2. 현행 구조와 확인된 문제

| 경로 | 현재 동작 | 문제 |
|---|---|---|
| `etl/scripts/build_financial_indicators.py::upsert_accounts` | 회사·연도·보고서·연결/별도·계정명으로 REPLACE | 이전 금액·응답 접수번호 소실 |
| 같은 파일 `upsert_indicators` | 회사·연도·보고서·지표코드로 REPLACE | 이전 관측 지표 소실 |
| 같은 파일 `run_from_filings` | 접수일 목록을 회사·연도·보고서로 합쳐 강제 재적재 | 같은 기간의 개별 접수번호가 처리 단위에서 사라짐 |
| 같은 파일 `run` / `_process_source` | 기간별 적재, 기존 회사 skip 또는 force | 일별 경로만 수정하면 수동 적재가 이력 저장을 우회 |
| `etl/src/new_etf_insight/dart_client.py::fetch_dart_list` | 정상 외 status를 모두 빈 목록으로 반환 | 무자료·API 오류·한도 초과 구분 불가 |
| `broker-web/lib/dart.ts` | DART 직접 조회·연간/분기 비교 | 프로젝트 웹 자체는 이력 저장소가 아님 |

2026-10-04 읽기 전용 DB 조회 기준:

- 테이블은 `corps`, `accounts`, `indicators` 3개. 이력 테이블 없음.
- accounts 2,206,408행, 사업연도 2016~2026; indicators 2,041,014행, 사업연도 2023~2026.
- 두 테이블은 `updated_at`만 저장하고 접수번호·최초 수집시각은 저장하지 않는다.
- 연도·보고서별 적재량이 다르므로 전 종목·전 분기가 완전하다고 볼 수 없다. 실행 전 다시 측정한다.
- **accounts PK에 `sj_div`가 없다.** 같은 회사·연도·보고서·CFS/OFS·계정명인데 BS/IS/CIS가 다르면 충돌할 수 있다. 실제 중복 표본을 조사하고 PK 변경 및 기존 reader 영향 여부를 결정한다. 현재 이력 손실 문제와 별도로 검증하며 단순히 컬럼을 추가해 해결됐다고 하지 않는다.

## 3. 실제 API와 복구 후보

| API와 공식 가이드 | 요청·응답의 확인 사항 | 설계에서의 사용 |
|---|---|---|
| [공시검색 list.json](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001) | `last_reprt_at=N`은 정정 포함. 회사코드 없는 조회는 3개월 제한, 페이지당 최대 100건 | 개별 접수번호·접수일·원래 보고서명을 보존하고 전체 페이지 조회 |
| [다중회사 주요계정 fnlttMultiAcnt](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2019017) | 현행 `fetch_accounts_chunk`. 응답에 `rcept_no` 있음. 요청에 과거 접수번호 지정 인자는 없음 | 실제 응답 접수번호에만 금액 귀속 |
| [다중회사 주요지표 fnlttCmpnyIndx](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2022002) | 현행 `fetch_indicators_chunk`. 응답 명세에 `rcept_no` 없음 | 회사·사업연도·보고서·분류·관측시각 기준으로 보존 |
| [재무제표 원본파일 fnlttXbrl.xml](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019019) | 접수번호와 보고서코드를 지정해 ZIP 요청 가능 | **과거 금액 복구의 1순위 실증 후보** |
| [공시 원본 document.xml](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003) | 접수번호별 공시서류 원본 확보 후보 | XBRL 부재·정정 사유·변경표 대조를 위한 보조 경로 |

단일회사 전체 재무제표 API는 현재 배치 API가 아니므로 현행 근거에서 제외했다. 주요계정 기간 재조회는 과거 차수 선택 기능이 아니다. 주요지표에는 금액 응답 번호나 목록에서 발생한 번호를 대신 붙이지 않는다.

XBRL 파일의 계정·CFS/OFS·기간·단위 식별 가능성과 최초/정정 파일의 내용 차이는 실증 전 미확정이다. document.xml도 모든 파일의 내부 형식이 같다고 가정하지 않으며 본문 표 파싱 비용을 따로 측정한다.

## 4. 0단계에서 확인·확정할 항목

1. 목적은 공개시점 기준 리서치·백테스트와 감사로 확정한다. 날짜만 있는 공시는 다음 거래일부터 적용하며 기존 거래 캘린더를 사용한다. 단순 평일 계산으로 휴장일을 추정하지 않는다.
2. 금액이 바뀐 최초·중간·최종 공시 사례를 확보하고 fnlttXbrl을 우선 대조. 각 번호가 해당 차수 원문을 제공하는지 확인.
3. XBRL에서 주요계정 금액·연결/별도·재무표 종류·기간·통화·단위·당기/누적을 식별. 비12월 결산 회사도 포함.
4. XBRL 부재 시 document.xml의 전체 표/변경표를 조사. 변경표만 있으면 검증한 부모와 결합할 수 있는지 확인. 부모·항목이 불명확하면 복구 실패로 남김.
5. 실제 주요계정 응답 접수번호와 목록 번호 대조. 주요지표는 번호 없는 관측임을 검증하고 원문에 과거 지표 근거가 있는지 별도로 조사.
6. 복구 회사·연도·보고서 범위, 일일 호출 예산·소요일, 표본 ZIP 크기·예상 용량·저장 위치·가용 공간 확정.
7. 최소 이력 표의 키·DDL, current 값의 출처 연결, legacy snapshot 방식, accounts PK 충돌 대응 확정.

이 단계 이전에는 과거 값 전량 복구 가능, API가 항상 최종본 반환, 정정의 계정 수 동일, 과거 지표 복구 불가를 단정하지 않는다.

## 5. 미래 응답 보존의 최소 구조

기존 SQLite에 **API 응답 이력 표 1개**를 추가하는 안을 우선한다. 기존 accounts·indicators는 최신 조회용으로 유지한다. 초기 구현에는 공시/원본/추출 테이블 3개와 파서·추출 버전 체계를 도입하지 않는다.

제안 표 `financial_response_history`의 내용:

- 회사·사업연도·보고서·source·지표분류, 실제 응답 접수번호(nullable).
- 원본 응답 JSON과 요청 맥락, 확보한 목록 메타, 최초 확보시각·최근 관측시각, status·실패 사유.
- accounts는 실제 접수번호별 응답을 보존. 같은 번호라도 내용이 달라지면 이전 내용을 남긴다.
- indicators는 접수번호 없이 내용이 변한 관측을 보존. 동일 내용 반복 수집은 최초 시각을 유지하고 최근 관측시각만 갱신.
- 내용 비교/멱등성을 위한 해시 1개는 검토하되 파서 버전 체계로 확대하지 않는다. nullable 번호가 포함된 단순 UNIQUE로 지표 중복 방지가 된다고 가정하지 않으며 별도 관측 키를 확정한다.

current 변경 최소안:

- accounts에 nullable `rcept_no`와 최초 수집시각 추가를 검토한다.
- indicators는 이력 관측과 연결할 방법과 최초 수집시각을 검토한다. 가짜 접수번호를 만들지 않는다.
- 기존 값은 출처 미귀속 legacy snapshot으로 보존하고 원래 updated_at을 남긴다. updated_at을 과거 최초 수집시각으로 승격하지 않는다.

`rcept_dt`(접수일), 목록 최초 관측시각, 응답 최초 확보시각은 서로 다른 값이다. 수집시각은 UTC ISO 8601로 기록한다. 과거 원문을 오늘 복구하면 수집시각은 오늘이며 예전 최초 수집시각은 근거 없으면 NULL이다.

테이블명·키·DDL·저장 위치는 제안이며 0단계 확정 전 적용하지 않는다. 과거 원문 파일 보관·파서가 필요한 구조는 후속 복구 PLAN에서 표본 결과를 근거로 정한다.

## 6. 수집 경로·과거 복구·비용

### 미래 응답 보존

`run_from_filings`: 개별 공시 목록 보존 → 회사·기간 API 호출 → accounts 응답 번호 검증 / indicators 관측 구분 → 공통 이력 저장 → 검증한 current 값 갱신.

`run`·force·직접 `upsert_accounts`/`upsert_indicators`도 공통 저장 지점을 거쳐야 한다. 보존 이력과 current 갱신은 같은 transaction으로 처리하도록 검토한다. 코드로 강제하기 전에는 자동 보장이라고 표현하지 않는다. source/분류별 부분 실패는 기존 값을 지우지 않고 미완료로 기록한다.

### 공시 목록 선행 확보와 과거 금액 복구

1. 재무 DB 백업·무결성·현재 값 snapshot 확인.
2. 복구 범위는 사용자 확인(2026-10-05)에 따라 현재 재무 DB의 회사·보고기간 집합으로 확정. 이 집합 밖 상폐사·미수집 기간은 이번 전량 복구 범위에 포함하지 않는다. 현재 DB에 포함된 상폐사는 배제하지 않는다.
3. 최초·정정 전체 목록을 독립 산출물로 먼저 제공한다. 회사·보고기간별 최초 접수일 후보 및 조회 범위의 완전성을 구분한다. 원문 다운로드·파싱 없이 목록을 확보하고 페이지 완료 기록. total_count 대비 수량을 대조하고 페이지 제한 도달은 미완료로 기록. 사업연도와 접수연도를 동일하게 제한하지 않음.
4. 접수번호별 XBRL 우선 확보 → 보조 원문 대조 → 금액 검증. 동일 보고기간의 계열 연결은 제목만으로 확정하지 않음.
5. 작은 표본을 직접 대조한 후 승인된 범위에 실행. 목록·원문·금액 확보율과 실패 사유를 별도로 보고.
6. 과거 지표 차수는 기본 범위 제외. 원문 근거 확인 시에만 후속 설계 범위 확대. 최신 지표를 옛 차수에 복사하지 않음.
7. 성공본 재실행은 중복을 만들지 않으며 실패 건만 재시도. 과거 복구로 current 최신 값을 후퇴시키지 않음.

공시번호별 해당 차수 원문에서 검증한 금액은 오늘 백필해도 rcept_dt 기준 백테스트에 사용할 수 있다. 날짜만 제공되므로 기본 사용 시작일은 접수일 다음 거래일이다. first_payload_collected_at은 감사·수집 추적용이며 사용 시작일을 늦추는 기준이 아니다. 최초 A 이후 정정 B가 공개되면 B의 사용 시작일부터 B를 적용하며 B를 A의 접수일에 소급하지 않는다. 실제 공개시각을 검증한 경우에만 별도 장중 적용 규칙을 설계한다.

### 0단계 비용 산정

- 단순 규모 예시: 2016~2026년 × 4보고서 × 2,700사 = **118,800개 회사·기간 슬롯**. 실제 최초 공시 수와는 다르며 상장시기·미제출·정정 등을 목록에서 집계한다.
- 원문당 1회, 하루 20,000회를 전부 쓸 수 있다는 가정이면 118,800회는 약 6일. 정정·목록·재시도·다른 배치 사용량 때문에 실제 기간은 증가한다.
- 공식 가이드의 20,000건은 일반적 요청 제한 안내이며 계정별 설정이 다를 수 있다. 실 계정 한도와 다른 수집기의 예약 사용량을 확인해 이 작업의 하루 예산을 정한다.
- 호출 수 = 목록 페이지 + 실제 원문 다운로드 + API 대조 + 실패 재시도. 회사·연도·보고서당 실측 호출 수로 합산한다.
- 예상 일수 = 총 예상 호출 / 이 작업의 하루 가용 호출 수를 올림. API 예산뿐 아니라 처리 속도도 함께 측정한다.
- 용량 = 실제 원문 수 × 표본 평균 압축 크기 + 추출 결과 + DB/인덱스 + 백업 여유. 12만 개 기준 평균 ZIP 1MB면 약 120GB, 5MB면 약 600GB인 **가정 예시**이며 측정값이 아니다.
- 표본의 평균·상위 크기, 압축 해제 임시 공간, 백업 공간, 저장 디스크를 확인하고 전량 실행 전 기록한다.

## 7. 공용 함수 변경 영향

`fetch_dart_list`의 현재 직접 호출처 (2026-10-04 검색 기준):

- `etl/scripts/build_financial_indicators.py::fetch_accounts_chunk`
- 같은 파일 `fetch_indicators_chunk`
- `etl/tests/test_dart_client.py`의 정상·무자료·기타 status 테스트

두 래퍼를 사용하는 run, run_from_filings, _process_source, _self_check도 영향을 받는다. 공시목록의 fetch_filing_page/fetch_all_filings는 fetch_dart_list를 호출하지 않고 직접 status를 처리하므로 같은 수정이 자동 적용되지 않는다. broker-web의 DART 요청도 별도 경로다.

오류 구분 인터페이스는 0단계에서 확정한다. 013 무자료, 014 파일 없음, 020 한도, 인증 오류, HTTP 오류를 구분하고 기존 빈 목록 기대 테스트를 함께 검토한다. 구현 직전 호출처를 재검색한다. ETF·웹 경로에 재무 복구용 오류 처리나 저장 방식을 임의로 전파하지 않는다.

## 8. 단계·검수와 요구사항 테스트

| 단계 | 수행 | 완료 조건 |
|---|---|---|
| 0a | 공시 목록·최초 접수일 선행 확보 | 원문 없이 목록·조회 완전성·최초 후보 제공 |
| 0b | XBRL·API 실증, 범위·비용·최소 DDL 확정 | 최초/정정 표본·회계 의미·API 귀속·자원 측정 근거 기록 |
| 1 | 실증 결과에 맞춘 과거 금액 복구 PLAN 구체화·실행 | 표본 직접 대조 후 확정 범위 복구·실패 보고 |
| 2a | legacy 보존 + 최소 응답 이력 표 + current 출처 연결 | 기존 값 불변·최초시각 위조 없음 |
| 2b | 일별·run·force·직접 쓰기 연결 | 미래 accounts 번호별 보존, indicators 관측 보존, 오류·중복·부분 실패 검증 |
| 3 | 전체 요구사항 대조·스키마 문서 갱신 | 미래 보존·과거 금액 복구 상태 각각 보고, 부분 복구를 전체 완료로 부르지 않음 |

| 요구사항 | 필수 테스트/검증 |
|---|---|
| 미래 금액 보존 | 확보한 A → B → C: 번호별 응답·값 유지, current=C; 미수집 차수 확보 주장 금지 |
| 지표 관측 보존 | rcept_no 없는 정상 응답 저장, 목록/금액 번호 전파 차단, 연속 동일 내용 중복 방지, A→B→A 재등장 관측 3개 보존 |
| 시각 보존 | 동일 응답 재수집·실패 재시도에도 최초 관측/확보시각 불변, legacy 시각 NULL |
| 응답 귀속 | 트리거 A인데 금액 API 응답 C이면 C에만 귀속, A 복구 완료 표시 금지 |
| 오류·부분 실패 | 013·014·020·인증·HTTP·일부 분류 실패 구분, 기존 값 유지 |
| 모든 쓰기 경로 | 일별·run·force·직접 upsert에서 이력 누락 차단, current 실패 시 transaction 보존 |
| accounts 충돌 | 같은 account_nm·CFS/OFS·기간, 다른 sj_div 표본 및 충돌 거부/분리 검증 |
| 과거 금액 복구 | 후속 PLAN: 값이 다른 A/B XBRL 대조, 최신 재조회 값을 A에 붙이면 거부 |
| 복구 멱등·순서 | 후속 PLAN: 중단 후 재시작·성공본 중복 없음, C 보유 후 A/B 복구 시 current=C |
| 목록 선행 산출물 | 정정 포함·기간 분할·전체 페이지·불완전 조회 시 최초 확정 거부 |
| 백테스트 사용시점 | 오늘 복구한 A도 A 접수일 다음 거래일부터 사용; B는 B 공개 이후 적용; 수집시각으로 사용일 지연 금지 |
| 회계·시점 의미 | CFS/OFS·BS/IS/CIS·기간·단위·당기/누적·비12월 결산 및 공개/수집시점 구분 |
| 완료 범위 | 미래 보존만 성공하거나 과거 목록만 복구했으면 전체 복구 완료 표시 금지 |

코드는 Muse가 작성하고 호출 에이전트가 검수·테스트·실행한다. 명세에는 정확한 파일·범위와 **명령 실행 금지, 파일만 작성. 테스트·실행·검증은 호출 에이전트가 담당한다.**를 넣는다.

후보 파일은 build_financial_indicators.py, dart_client.py, etl/tests/의 재무·공용 API 테스트다. 정확한 파일·함수·DDL은 구현 명세에서 확정한다. 웹 이력 화면은 이 문서의 완료 범위가 아니며 필요하면 별도로 확정한다.

## 9. 진행 상태

- [x] 현행 코드·DB·기존 DONE 문서·브랜치 검토.
- [x] 리뷰 1~8 반영: 실제 API와 번호 유무, XBRL 우선, 지표 범위, 비용, 리서치 목적, 최소 구조, sj_div, 공용 함수 영향.
- [ ] 0단계 실증·목적·범위·비용·DDL 확정.
- [ ] 미래 응답 보존 구현·직접 검수.
- [ ] 후속 과거 금액 복구 PLAN·실행·범위별 검증.

**이 문서는 DONE으로 이동하지 않는다. 과거 복구 요청은 유지되며, 미래 보존 완료와 별도로 완료 여부를 기록한다.**

## 10. 최초 개발 범위 — 목록 선행 확보와 XBRL 실증 기반

- 기존 `etl/scripts/build_financial_indicators.py`에 금융 공시 목록 수집·산출물·접수번호별 XBRL 확보 함수를 추가한다. ETF 호출 경로는 변경하지 않는다.
- 저장 위치는 `etl/exports/financial_filing_history/`: 통합 목록 `manifest.json`과 구간별 `queries/<begin>_<end>_<corp-or-all>.json`, 원문 `xbrl/<rcept_no>_<reprt_code>.zip`, 실증 결과 `probe.json`. 명시한 output_dir로 테스트를 격리한다.
- 목록 산출물은 공시 전체 메타·요청 구간·회사·페이지 수·완전성·최초 접수일 후보를 포함한다. 요청 구간 내 완전 조회가 생애 최초 공시를 증명하지는 않는다. 그룹은 회사·보고서 종류·기말 연월이며 비12월 결산도 목록에서 버리지 않는다. 보고서코드 미확정은 NULL과 이유를 기록한다.
- 기존 CLI에 목록 수집 시작/종료일·회사·산출물 경로와 XBRL 표본 번호/보고서코드 입력을 추가한다. 기존 동작을 유지한다. 기존 CLI로 목록만 보존하거나 접수번호별 원문을 받을 수 없어 추가하는 기능이다.
- 금융 목록 전용 요청은 last_reprt_at=N·pblntf_ty=A를 명시하고 3개월 이내 구간으로 분할한다. 페이지/건수 불일치·중간 실패는 완전 조회로 인정하지 않는다. 인증키·인증 포함 URL을 출력/저장하지 않는다.
- XBRL 요청은 rcept_no·reprt_code를 지정한다. ZIP과 오류 XML을 구분하고 014 파일 없음·020 한도·인증·HTTP 오류를 구별한다. ZIP 내부 파일명·크기·인스턴스 여부·context/unit/fact 메타를 실증 보고서에 남긴다. ZIP만 확보했다고 금액 복구 완료로 표시하지 않는다.
- 실제 계정·연결/별도·기간·단위 검증 전에는 current/과거 금액 DB에 쓰지 않는다. 파서·DB 키는 실증 결과 후 확정하며 미확정 스키마로 개발하지 않는다.
- 이번 개발 완료 조건은 목록/XBRL 실증 도구 구현·직접 테스트다. 전체 재무 이력·금액 복구 완료가 아니며 실증 후 금액 복구 개발도 필수 잔여 작업이다.



### 직접 검수로 추가한 필수 조건

- 부분 실패 재수집은 기존 성공 목록·최초 관측시각을 지우지 않는다. 같은 회사 맥락의 다른 조회 구간도 누적하며 구간별 완전성과 현재 실행의 완전성을 구분한다. 다른 회사 조회는 별도 산출물 폴더로 격리한다.
- 페이지 중복 접수번호·중간 013·total_count/total_page 변동·필수 식별정보 누락은 완전 조회로 인정하지 않는다.
- 실제 DART ZIP의 `.xbrl` 인스턴스와 `.xml` 링크베이스를 구별한다. 파일 크기로 인스턴스를 추정하지 않는다. 복수 인스턴스는 모두 식별하며 QName·namespace 근거를 보존한다.
- 목록 미완료·원문 실패 실행은 실패 종료코드 및 INCOMPLETE/FAILED로 표시한다. 키를 포함한 오류 메시지는 저장하지 않는다.

## 11. 개발·직접 검수 기록 (2026-10-05)

- Muse 코드 작성: `build_financial_indicators.py`의 목록·XBRL 실증 함수 및 `etl/tests/test_financial_filing_history.py`. 현재 재무 writer·스키마·DB는 변경하지 않았다.
- 직접 검수로 수정한 결함: 실제 `.xbrl` 인스턴스 미선택, 부분 실패 목록 덮어쓰기, 중복 페이지/중간 013의 허위 완료, CLI 실패 성공 표시, 오류 키 노출. 관련 회귀 테스트 포함.
- 최종 검수 테스트: `etl/.venv/Scripts/python.exe -m unittest tests.test_financial_filing_history tests.test_dart_client` 56개 통과. 실제 캐시 재사용 시 최초 원문 확보시각 보존도 확인했다.
- 실제 표본: RFHIC(corp_code 01078178) 2024년 공시 목록 5건, 90일 분할 5회 조회, 요청 구간 완전 조회. 2023년 사업보고서 최초 접수일 후보 2024-03-21, 정정 2024-04-30.
- 원문 표본: 최초 20240321001031 및 정정 20240430001183, reprt_code 11011. 각 ZIP 96,750바이트, 인스턴스 1개, context 92개, fact 1,540개. 인스턴스 내용은 동일했다. 금액이 실제 바뀐 차수 복구를 입증한 표본으로 인정하지 않는다.
- 산출물: `etl/exports/financial_filing_history/rfhic_phase0/manifest.json`, `queries/`, `xbrl/`, `probe.json`.
- 미완료: 전량 공시 목록 수집, 금액 변경 사례 원문 대조, 계정/연결·별도/기간/단위 매핑 확정, 과거 금액 복구, 미래 응답 이력 DB 저장, 백테스트 연결. 전체 요청 완료 또는 DONE으로 표시하지 않는다.

- 최종 요구사항 대조: 목록·완전성은 fetch_history_window/collect_filings_history, 누적·최초 관측 보존은 run_filings_history, 원문 및 최초 확보시각은 probe_xbrl/_save_probe_entry, 실제 인스턴스 선택은 _inspect_xbrl_zip으로 구현했다. 관련 테스트는 test_financial_filing_history.py에 있다.
- 백테스트 다음 거래일 적용은 설계 규칙이며 이번 도구는 백테스트 계산을 구현하지 않았다. 계정 금액 추출·공개시점별 조회·미래 응답 이력 저장 요구는 미구현으로 남긴다.
- 이번 산출물은 실증 기반 개발 완료이며 전체 요구사항 완료가 아니다. 전량 복구 회사·기간 범위는 현재 재무 DB 집합으로 사용자 확인 완료(2026-10-05).

## 12. 다음 개발의 최소 DB 스키마 — 사용자 확정 (2026-10-05)

기존 DB 안 이력 표 1개와 current 출처 컬럼만 추가한다. 원문 파일은 §10의 저장 경로를 사용하며 별도 원문·파서 버전 표는 만들지 않는다.

```sql
CREATE TABLE financial_response_history (
    history_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    corp_code TEXT NOT NULL,
    bsns_year TEXT NOT NULL,
    reprt_code TEXT NOT NULL,
    idx_cl_code TEXT NOT NULL DEFAULT '',
    rcept_no TEXT,
    rcept_dt TEXT,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    first_payload_collected_at TEXT,
    last_observed_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_financial_response_history ON financial_response_history(
    source, corp_code, bsns_year, reprt_code, idx_cl_code,
    COALESCE(rcept_no, ''), payload_hash
) WHERE source <> 'indicators';
ALTER TABLE accounts ADD COLUMN rcept_no TEXT;
ALTER TABLE accounts ADD COLUMN first_payload_collected_at TEXT;
ALTER TABLE accounts ADD COLUMN history_id INTEGER;
ALTER TABLE indicators ADD COLUMN first_payload_collected_at TEXT;
ALTER TABLE indicators ADD COLUMN history_id INTEGER;
```

- source 구분: accounts(주요계정 응답), indicators(주요지표 관측), xbrl(접수번호별 원문 실증/추출), legacy_accounts, legacy_indicators. 지표/legacy rcept_no는 NULL이다.
- payload_json은 회사·기간·응답 접수번호·지표분류별 원본 행 묶음을 보존한다. 해시는 정렬한 JSON 내용 기준이며 수집시각/요청 맥락을 섞지 않는다. 주요계정은 실제 접수번호·내용별로 보존한다. 지표는 직전 관측과 내용이 같을 때만 최초시각 유지·최근 관측시각을 갱신한다. A→B→A로 돌아온 지표는 세 번째 관측 행을 새로 만든다. 지표에 전체 내용 해시 UNIQUE를 적용하면 이 순서를 잃으므로 지표는 위 UNIQUE 인덱스 대상에서 제외한다. transaction 안에서 마지막 관측을 읽고 비교·저장한다.
- provenance_json은 API 요청 인자(키 제외), 확보한 목록 메타, 원문 경로·검증 상태·실패 근거를 기록한다. xbrl 원문 확보와 회계 금액 검증은 다른 상태이며 검증되지 않은 fact를 복구된 계정 금액으로 표시하지 않는다.
- legacy snapshot은 현재 accounts/indicators 값과 원래 updated_at을 그대로 보존하며 first_payload_collected_at/rcept_no는 NULL이다. source가 legacy이므로 API 원본 응답이라고 주장하지 않는다.
- 기존 accounts PK는 이번 추가 컬럼 변경에서 유지한다. 서로 다른 sj_div 충돌을 검출하면 이력은 보존하되 current 쓰기를 거부하고 표본/reader 검증 후 별도 PK 결정을 한다.
- ensure_schema에서 기존 테이블 컬럼을 검사해 ALTER를 멱등 적용한다. upsert_accounts/upsert_indicators는 이력 기록과 current 갱신을 같은 savepoint/transaction으로 처리한다. run/run_from_filings/force/직접 호출이 이 지점을 통과하도록 유지한다.
- 알려진 current 접수번호보다 오래된 금액은 이력에만 저장하고 current를 후퇴시키지 않는다. 출처 불명 legacy current의 최신 여부는 단정하지 않으며 과거 원문 복구 경로는 current를 갱신하지 않는다.
- 과거 원문 계정 매핑·시점 기준 조회 구현은 변경 금액 표본 검증 후 별도 명세로 정한다. 이 스키마 추가만으로 과거 값 복구 또는 백테스트 연결이 완료되지 않는다.
- 재무 DB 실제 마이그레이션은 백업·무결성 확인 후 수행하며 코드/테스트는 임시 DB에서 먼저 검수한다.




### 추가 실증: 실제 금액 변경 차수

- 피엔티(corp_code 00654175), 2024년 1분기(11013): 최초 20240516000742(2024-05-16), 정정 20240619000123(2024-06-19).
- XBRL ZIP은 각각 47,772/47,701바이트, fact 321개. 같은 QName·context·KRW unit인 별도 IncomeTaxExpenseContinuingOperations가 -2,474,110,716 → -247,410,716으로 변경됐다(당기 3개월/누적 context 각각).
- 정정 원문은 재무제표 XBRL 오류 정정이며, 접수번호별 XBRL이 서로 다른 차수 값을 제공하는 사례를 확보했다. 전체 회사·기간 복구 가능성을 보장하는 근거로 확대하지 않는다.
- 산출물: etl/exports/financial_filing_history/pnt_phase0/ 아래 목록·원문·실증 보고서.
- 실제 DB 금액 범위: 2,922개 회사, 92,659개 회사·보고기간 조합, 2016~2026년/4종 보고서. DB 956,858,368바이트. 현재 C 디스크 여유 약 41GB이므로 ZIP 규모 표본을 넓혀 저장 예산을 검증한 뒤 전량 실행한다.

## 13. 과거 금액 복구 개발 명세 준비 — 실제 원문 근거

- 금액 복구 대상은 현재 DB의 accounts/indicators 회사·사업연도·보고서 조합의 합집합이다. 목록 검색은 해당 사업연도 이후 늦게 접수된 정정까지 포함한다. 현재상장 필터로 과거 DB 회사를 제거하지 않는다.
- 원문은 접수번호별 ZIP을 보존하고 source=xbrl의 payload_json에 금액 fact·context·unit·QName과 실제 원문 label/presentation 근거를 보존한다. current accounts/indicators를 수정하지 않는다.
- 금액은 Decimal로 검증하고 원문 문자열을 유지한다. context의 기간·명시 차원·통화 단위를 함께 보존한다. ConsolidatedAndSeparateFinancialStatementsAxis의 실제 member로 CFS/OFS를 판정하고 다른 세부 차원은 버리지 않는다.
- `.xsd` roleType definition 및 `_pre.xml` presentationLink가 표 분류의 근거다. 피엔티 표본은 D210005(BS 별도), D431415(CIS 별도), D520005(CF 별도), D610005(SCE 별도)를 제공했다. 이름/QName만으로 재무표 종류를 임의 결정하지 않는다. 여러 표에 포함되면 역할 목록을 유지한다.
- 연결/별도·표·기간·단위가 미확정이면 NULL과 근거/실패 상태로 남기고 완전히 복구됐다고 표시하지 않는다. probe의 fact/context 제한 때문에 잘린 결과도 전체 복구 성공으로 인정하지 않는다.
- 기간별 API 최신 값은 과거 원문 값 대용으로 사용하지 않는다. 당기/비교기간과 단독/누적 context는 별도로 보존한다. 계정 label은 원문 linkbase에서 읽으며 임의 한글 계정명 대응표를 만들어 동일성을 주장하지 않는다.
- 현재 DB와 공시 목록만으로 report code를 확정할 수 없는 비12월 결산 분기는 미해결로 기록한다. 원문 요청 보고서코드를 추정해서 잘못 귀속하지 않는다.
- 백테스트 조회는 접수일 다음 거래일부터 그 차수의 검증된 금액을 적용하며 collection time을 사용일로 쓰지 않는다. 조회 대상 키(회사·기간·QName·연결/별도·표·context 차원·unit)가 충돌하면 임의 MAX/첫 행으로 선택하지 않는다.
- 재실행은 기존 성공 원문/이력과 최초 확보시각을 유지한다. 파일 없음·오류·부분 추출은 별도로 집계하며 성공/실패/대기 및 목록 완전성을 구분한다. 전량 실행에는 일일 호출 예산과 저장공간 실측 확인이 선행된다.
