# PLAN — 종가베팅 청산을 개장 동시호가 시장가로 전환

**한 줄 요약** — 익일 청산을 `09:01 시장가 전량`에서 **08:55 KRX 동시호가 시장가 전량**으로 바꾼다.
잔량(하한가 잠김·VI)은 건드리지 않는다. 롤백 = `close_bet.json` 의 `exit_time` 을 `09:01:00` 으로 되돌림.

근거: 실매매 종목 기준 청산 시점 비교에서 시가(동시호가) 청산이 09:01 시장가보다 유리했다.
수치는 저장소 공개라 옮기지 않는다.

---

## 0. 사용자 확정 사항 (2026-09-17)

### Q1. 동시호가 주문 유형 = **시장가** (`trde_tp=3`)

- **체결 우선:** 시장가는 동시호가에서 최우선 체결
- **호가 노출 차이 없음:** 동시호가 중 공개되는 건 예상체결가·수량·호가잔량. 지정가로 내도 매도잔량에 똑같이 잡힌다
- **하한가 체결 위험 낮음:** 우리 수량은 익일 09:00 1분봉 거래량의 중앙 0.1% 미만, 최대 수 % 수준
- **남는 위험:** 시장 전체가 하한가로 시작하는 악재일. 현행 09:01 시장가도 똑같이 노출

### Q2. 청산 주문 거래소 = **KRX 고정**

- SOR 의 장 시작 전 라우팅 규칙이 키움 문서에 없음. NXT 프리마켓(08:00~08:50)과 KRX 동시호가(08:30~09:00) 시간대 불일치
- 시가는 KRX 동시호가 한 곳에서 결정 → SOR 이점 없음

### Q3. 매도1호가 − 1틱 추격 = **제외**

- 동시호가 시장가 잔량이 남는 경우는 하한가 잠김·VI 뿐
  - 잠김: 정정·취소 후 재주문 모두 시간우선 상실 → 대기열 맨 뒤
  - VI: 걸린 시장가가 단일가 종료 때 체결 → 지정가 전환은 덜 공격적
- 두 경우 모두 **잔량 유지가 최선**. 추격이 이득 보는 경우가 없음
- 키움 정정 `kt10002` body 에 주문유형(`trde_tp`) 필드 없음 → 시장가→지정가 정정 자체도 미검증

### Q4. 전환 스위치 = **`exit_time` 재사용** (`exit_mode` 신설 안 함)
### Q5. 백스톱 시각 = **09:01:00**
### Q6. 실계좌 1주 검증 없이 **바로 전체 적용**

---

## 1. 흐름

```
08:50  워커 기동 (기존 스케줄 그대로)
08:55  exit_time 도달 → 대상 종목 전량 시장가 매도 (KRX). 시세 조회 실패여도 주문
09:00~ 체결 확인만 (기존 settle_pending). 잔량 주문은 유지
09:01  백스톱: sell_status NULL 잔존분만 시장가 (ordered 는 제외 = 잔량 유지와 일치)
09:10  stop-time. 미체결 잔존이면 디스코드 알림 (기존)
```

## 2. 확정 결정

| # | 결정 | 구현 위치 |
|---|---|---|
| D1 | 강제청산 판정을 연속매매 창(09:00~) 밖에서도 발동. `window_end` 이후는 안 함 | `etl/scripts/run_close_bet_exit.py` `run_loop` |
| D2 | 강제청산은 시세가 없어도 주문 (08:55 호가 공백 대비) | 같은 곳 |
| D3 | `OrderRequest.exchange`: `KRX`/`NXT`/`SOR`, 기본 `SOR`. 신규 주문 body `dmst_stex_tp` 에 반영 | `broker/kiwoom/models.py`, `broker/kiwoom/orders.py` |
| D4 | 청산 매도는 연속매매 창(09:00) 전 주문만 `exchange=KRX`, 장중(tp/sl·09:01 백스톱)은 `SOR` | `etl/scripts/run_close_bet_exit.py` |
| D5 | `exit_reason` 은 `forced` 유지 | — |
| D6 | 백스톱 XML `09:01:00`. "exit_time +30초" 연동 문구 삭제 | `ops/scheduled-tasks/close-bet-force-exit.xml`, `run_close_bet_force_exit.py` docstring |
| D7 | config 검증 변경 없음 (형식만 검사 → `08:55:00` 통과) | `close_bet_config.py`, `broker-web/lib/close-bet-config.ts` |

## 3. 체크리스트

### A. 요구사항

- [x] A1 `exit_time=08:55:00` → 08:55 에 전량 시장가 매도 — T1
- [x] A2 `exit_time=09:01:00` → 09:00 이전 미주문 (현행 동일) — T2
- [x] A3 강제청산 시각이면 시세 없어도 주문 — T3
- [x] A4 `exchange` 미지정 → body `SOR` (매수·눌림목·수동 주문 불변) — T4
- [x] A5 `exchange=KRX` → body `KRX` — T5
- [x] A6 08:55 동시호가 매도만 `exchange=KRX`, 장중 매도는 `SOR` — T6

### B. 주의사항

- [ ] B1 **배포 순서:** broker 재기동(`restart_all_servers.ps1`, :8001/:8002 같이) → 그다음 `exit_time=08:55:00`.
  역순이면 구 broker 가 `exchange` 를 무시(`extra="ignore"`) → SOR 로 나감
- [ ] B2 broker 재기동은 장 마감 후 (장중 high52 워커 등 운용 중)
- [ ] B3 백스톱 XML 은 파일 수정만으로 반영 안 됨 → 스케줄 작업 재등록
- [ ] B4 개장 지연일: 08:55 주문 거부 → 매 폴링 재시도·디스코드 알림 (기존 동작). 날짜 대응 제외
- [ ] B5 `exit_time` 08:30 이전은 오설정(동시호가 접수 전). 검증 추가 안 함

## 4. 테스트

| ID | 검증 | 위치 |
|---|---|---|
| T1 | 08:55 + exit_time 08:55 → 강제청산 판정 True | `etl/tests/test_close_bet_exit.py` |
| T2 | 08:59 + exit_time 09:01 → False / window_end 이후 → False | 같은 파일 |
| T3 | 강제청산 판정 True 면 시세 없어도 매도 대상 | 같은 파일 |
| T4 | `exchange` 미지정 → body `SOR` | `broker/test_orders.py` |
| T5 | `exchange=KRX` → body `KRX` | 같은 파일 |
| T6 | 08:55 매도 → `KRX` / 09:01 매도 → `SOR` / 기본값 `SOR` | `etl/tests/test_close_bet_exit.py` |

## 5. 손 안 대는 것 (요구사항 커버 재확인 완료)

- 정정·취소 API — 추격 없음. `exchange` 추가 안 함
- `settle_pending`·`load_ordered_pending` — 주문번호 1개 구조 그대로
- 백스톱 선별 로직 — `ordered` 제외가 곧 잔량 유지
- 매수 배치 `run_close_bet.py` — 보유기간 문구는 `exit_time` 에서 자동 생성
- broker-web 설정 UI — 형식 검증만이라 `08:55:00` 입력 가능
- 워커 기동 XML(08:50), ps1 인자
