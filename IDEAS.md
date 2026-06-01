# Ideas & Future Directions

아이디어 스케치 공간. 확정 계획은 PLAN, 지금 작업은 CLAUDE.md 참고.

---

## 투자노트

### 키움 체결내역 자동 연결
체결 시 LLM이 자동으로 `add_note_event` 호출 → 사용자 대화 없이 거래 기록.
현재는 사용자가 대화로 알려주면 LLM이 기록하는 방식으로 대체 중.
**막힌 이유:** 키움 체결내역 TR 코드 검증 필요 (`broker/tr.py` 미완).

### 이벤트 추가 웹 UI
상세 모달에서 직접 거래 기록 추가 (LLM 없이). 현재는 MCP로만 가능.

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
