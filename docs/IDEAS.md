# Ideas & Future Directions

아이디어 스케치 공간. 확정 계획은 PLAN, 지금 작업은 CLAUDE.md 참고.

---

## 투자노트

### 키움 체결내역 → 투자노트 자동 연결

**목표:** API로 성공한 거래는 전부 자동으로 투자노트에 기록. 사용자 대화/수동입력 불필요.
그 위에 사용자가 매수 배경·목표가·보유기간·주의점을 덧붙일 수 있는 공간.

**현재 상태 (재료는 거의 다 있음):**
- `kiwoom_trade_history` 테이블: broker 통과 주문 전부 기록 중 (`record_trade`, order_no PK).
  단 지금은 `place_order` 시점의 *제출(submitted)*만 기록. 실제 *체결*은 별개.
- 실제 체결 확인 = kt00007 (당일 체결내역, `GET /orders/history`) — 이미 구현됨.
- `notes` 테이블에 사용자 메타 컬럼 **이미 존재**: `target_price`(목표가),
  `holding_period`(보유기간), `buy_reason`(매수배경), `memo`(주의점/자유메모).
  → 새 스키마 거의 불필요. UI만 붙이면 됨.
- order_no 포맷(7자리 zero-pad) 두 경로 일치 검증 완료 → 조인 키로 안전.

**남은 작업 (브리지):**
1. **체결 → 노트 연결 규칙.** ticker로 매칭.
   - 매수 체결인데 해당 ticker open 노트 없음 → 노트 신규 생성 + 매수 이벤트.
   - 매수 추가/매도 체결 → 기존 open 노트에 `note_events` append.
   - 매도로 잔량 0 → 노트 `status='closed'`.
2. **자동 입력 트리거.** 둘 중 택1:
   - (a) kt00007 폴링/배치로 체결 확정분을 주기적으로 노트에 반영 (LLM 불필요, 단순 동기화).
   - (b) `place_order` 성공 직후 잠정 이벤트 기록 후 kt00007로 체결 확정.
   - → (a) 권장. 체결은 비동기라 제출 시점 기록은 부정확.
3. **kiwoom_trade_history ↔ note_events 중복 방지.** order_no를 note_events에도 저장해
   재실행 시 멱등 (현재 note_events에 order_no 컬럼 없음 → 추가 필요).

**막혔던 이유 해소:** "키움 체결내역 TR 미완"은 옛 메모. kt00007 구현 완료됨. 이제 브리지 로직만 남음.

### 사용자 메타 입력 웹 UI
체결로 자동 생성된 노트에 매수 배경·목표가·보유기간·주의점을 사용자가 채워넣는 화면.
- 컬럼은 이미 `notes`에 있음(`target_price`/`holding_period`/`buy_reason`/`memo`).
- 상세 모달에서 인라인 편집 (PATCH `/notes/{uid}` 재활용).
- 거래 이벤트는 자동, 메타는 수동 — 역할 분리.

---

## 정보 확장 (api 서버)

### 재무정보 (DART)
DART OpenAPI로 분기·연간 재무제표 조회. ETF 보유 종목 분석에 활용 가능.

### 증권사 리포트 요약
파이프라인 구축 난이도 높음. LLM 또는 MCP로 요약하는 방식 고려 필요.

---

## 인프라

### GDrive SQLite 동기화
`NOTES_DB_PATH`를 GDrive 경로로 설정하면 자동 백업. 코드 변경 불필요.
Litestream 등으로 자동화 가능하나 지금은 수동으로 충분.

### OAuth / 멀티유저
`notes` 테이블에 `user_id` 컬럼 예약됨. api 서버 배포 시 필요.
