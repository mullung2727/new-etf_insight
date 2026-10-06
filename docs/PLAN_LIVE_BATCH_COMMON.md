# PLAN — 실매매 배치 공통 실행 틀

한 줄: 실매매 배치 20개가 각자 짜던 시작 절차(거래일 확인 → 계좌 확인 → 예외 알림 → 종료 코드)와 broker 주문 호출을
`trading_batch_common.run_live_batch()` 와 `broker_client.BrokerClient` 로 모은다. 전략 규칙·파라미터는 각 전략에 그대로 둔다.
전략 하나씩, 장 끝난 뒤 옮긴다.

## 진행
- [ ] 구현·테스트 머지 (PR #)
- [ ] 운영 등록 (스케줄 작업·config) — 해당 없음 (기존 작업 그대로, 러너 수정만)
- [ ] 첫 실가동 확인 (날짜) — 전략별 전환 다음 거래일 로그 확인
- [ ] 설계와 달라진 점을 "구현 차이" 절에 반영

## 0. 왜 — 2026-10-05 사고

- 대체공휴일에 jevq 청산이 돌아 broker 422 → 상태 `unknown_ah` → 이 상태를 "매도 완료"로 쳐서 다음 거래일 청산이 건너뜀(3종목 미청산, 10-06 수동 추격 매도로 복구).
- 같은 날 high52 16:00 확인이 휴장일에 보유일 +1 (추석 포함 3일 과다, 10-06 정정).
- 원인: 휴장일 가드·결과 불명 처리를 배치마다 따로 짬. rights 는 있고 jevq 청산·rebound 는 없었다.
- 응급 조치(2026-10-06): 러너 공통 사전검사 `ops/scheduled-tasks/lib_trading_day_guard.ps1` (휴장일이면 파이썬 안 띄움),
  jevq 청산·high52 주문/확인에 파이썬 가드, jevq `unknown_ah` 재시도 대상화.

## 1. 확인된 사실 (2026-10-06 코드 기준)

| 배치 | 계좌 확인 | 달력 가드 | 장 확인(intraday_ranking) | 자체 알림 | 직접 주문 호출 |
|---|---|---|---|---|---|
| envelope order·expire | O | O | | O | trading_batch_common |
| envelope verify | O | | O | O | |
| high52 order | O | O(10-06) | O | O | O |
| high52 exit | O | | | O | O |
| high52 verify | O | O(10-06) | | O | |
| rebound order·exit·verify | O | | | O | order·exit |
| rights detect·order·expire·verify·monitor | detect 제외 O | O | | 러너 | expire |
| jevq prep·outcomes·report | | (prep 러너) | | O | |
| jevq order·buy_ah | O | | | O | O |
| jevq exit | O | O(10-06) | | O | O |

- `etl/scripts/broker_client.py` `BrokerClient`(PR #48): 호가·지정가·시장가·정정·취소·미체결·체결내역 + 결과 어휘(submitted/rejected/failed/unknown/dry_run/cancelled).
  **사용하는 전략 0개.** jevq 추격(`buy_ah`)·rights 추격(`chase.py`)이 각자 같은 호출을 갖고 있다.
- 결과 불명 처리 규칙이 전략마다 다름: rights 는 다음 실행에서 미체결 목록으로 재판정(`expire.recheck_unknown`), jevq 는 10-06 전까지 완료로 침.
- 시세 날짜 미확인: high52 verify 는 휴장일에 받은 직전 거래일 시세를 오늘 봉처럼 썼다(달력 가드로 막았지만 날짜 검증은 없음).

## 2. 범위

포함
- A. `run_live_batch()` — 시작 절차 공통 함수 (`etl/scripts/trading_batch_common.py`)
- B. 결과 불명 공통 규칙 — "불명은 완료가 아니다, 다음 실행에서 잔고·미체결·체결내역으로 재판정"
- C. jevq `buy_ah` 추격·rights `chase.py` 추격 → `BrokerClient` 로 교체
- D. 시세 날짜 검증 — 보유 갱신에 쓰는 시세가 오늘 날짜가 아니면 갱신 안 함

제외
- 전략 규칙·파라미터·시각(전략 파일 그대로)
- 러너 휴장일 사전검사(10-06 완료, 이중 방어로 유지)
- 원장 스키마 통합(전략별 테이블 그대로)

## 3. 설계

### A. `run_live_batch(tag, body, *, broker_url, profile, notify_channel="batch", trading_day=True) -> int`

순서 (전부 이 함수 안, 전략 `main()` 은 `return run_live_batch(...)` 한 줄 + 인자 파싱만):
1. `trading_day=True` 이고 `is_krx_trading_day(오늘)` 거짓 → `print(f"{tag} 거래일 아님 — 종료")`, **0** (알림 없음)
2. `broker_url` 있으면 `require_profile(broker_url, profile)` 거짓 → 알림, **1**
3. `body()` 실행 → 반환 dict `{"status", "lines"}` 의 lines 를 알림. 0
4. 예외 → 트레이스백 출력 + 알림 시도. 알림 성공 **2** / 실패 **1** (러너는 2면 중복 알림 생략 — 스윙 배치와 같은 규약)

- 데이터 배치(jevq outcomes·report, rights detect)는 `broker_url=None`.
- 거래일과 무관하게 돌아야 하는 배치는 `trading_day=False` 를 명시한다(현재 해당 없음).

### B. 결과 불명

- `BrokerClient` 의 `unknown` 은 원장에 그대로 남기되, 완료 집합에 넣지 않는다.
- 다음 실행 시작에서 재판정: 미체결에 우리 주문번호가 있으면 유지(수동 확인 알림), 없으면 체결내역 반영 후 재시도 대상.
- 재시도 수량은 항상 `min(원장 수량, 실잔고)` — 이중 매도 방지 (jevq 10-06 수정과 같음).

### C. BrokerClient 교체

- jevq `buy_ah.BrokerApi` → `BrokerClient(sources={"buy": "jevq_buy_ah", "sell": "jevq_exit"})`
- rights `chase.py` 의 주문·정정·시장가 → `BrokerClient(sources={... "rights_dip"})`
- 추격 엔진 로직(시각·정정 간격·시장가 전환)은 전략 쪽에 그대로. 호출만 바꾼다.

### D. 시세 날짜

- `BrokerClient.best_quote` 가 쓰는 `quote_fresh(base_tm, now, max_age_s)` 를 보유 갱신(high52 verify `update_holdings`, envelope verify)에도 적용.
  오늘 장중·장후 시각이 아니면 그 종목 갱신 건너뜀 + 알림 1줄.

## 4. 단계 (전략 하나씩, 장 마감 후 전환 → 다음 거래일 로그 확인)

```text
P1 run_live_batch + 결과 불명 공통 헬퍼 + 테스트            → verify: 단위 테스트 (휴장·계좌불일치·예외·알림실패 각 종료 코드)
P2 rebound 3개 전환 (가장 작음)                              → verify: 다음 거래일 로그·원장 동일
P3 envelope 3개 → P4 high52 3개(+D) → P5 rights 5개(+C) → P6 jevq 6개(+B·C)
   각 단계                                                    → verify: 전환 전후 같은 날 dry-run 출력 비교 + 다음 거래일 실로그
```

- 실매매 코드라 각 단계는 사용자 승인 후 전환. dry-run 이 있는 전략은 전환 당일 dry-run 비교를 먼저 한다.

## 5. 요구사항 → 테스트

| 요구사항 | 테스트 |
|---|---|
| 휴장일이면 주문·알림·원장 기록 0, 종료 0 | `run_live_batch` 휴장일 → body 미호출, notify 미호출, 0 |
| 계좌 불일치면 주문 0, 알림, 종료 1 | profile 거짓 → body 미호출, 1 |
| 예외 시 알림 성공 2 / 실패 1 | notify True → 2, False·예외 → 1 |
| 결과 불명은 완료 아님 | unknown 행 → 다음 실행 대상 포함, 미체결 잔류면 제외+알림 |
| 재시도 이중 매도 없음 | 잔고 0 → skip_not_held |
| 오래된 시세로 보유 갱신 안 함 | base_tm 전일 → update 0 + 알림 |
| BrokerClient 교체 후 추격 동작 동일 | 기존 buy_ah·chase 테스트 전부 통과 (가짜 클라이언트 주입) |

## 6. 결정 출처

| 결정 | 누가 |
|---|---|
| 매매 공통화 방향, 러너 공통 사전검사 즉시 적용, 2번은 설계문서부터 | 사용자 (2026-10-06) |
| 함수 1개(`run_live_batch`) 형태, 종료 코드 0/1/2, 전환 순서(작은 전략부터) | 내 판단 — 검토 필요 |
| 시세 날짜 검증(D) 포함 | 내 판단 — 10-05 high52 원인에서 도출 |

## 구현 차이

없음 (구현 전)
