# PLAN — 투자노트 `idea` 상태 + 진입가 도달 알림

## 요약

투자노트에 **매수 전 조사 단계**를 나타내는 `idea` 상태를 추가하고, `entry_price`(매수 진입
희망가)를 기재하면 broker가 **장중 5분 폴링(ka10095 일괄시세)** 으로 도달 여부를 감시해
**Discord 웹훅으로 하루 1회** 알린다. 도달 알림을 받은 뒤에만 사용자가 알림을 끌 수 있고,
한 번 끄면 자동 재개 없음(가격 도달 시점의 사용자 판단을 존중).

확정 결정(사용자):
- 상태명 = **`idea`**. "조사했으나 안 사기로 함"도 별도 상태 없이 `idea`로 둔다.
- **종목당 노트 1개 원칙 유지**. 재매수해도 같은 노트에 이벤트가 누적되어 히스토리 추적.
  → `merge_notes_by_symbol`(체결 자동연결 경로) **수정 없음**.
  ⚠️ **정정(사후)**: 이 원칙은 자동연결에서만 강제됐고 **수동 `create_note`는 우회**했다.
  "이 원칙으로 자동 보장"은 틀린 가정 — 수동 생성 가드를 별도로 넣어야 했다.
  → 후속 커밋 `35823ef`: `store.active_note_for_symbol` + 라우터 409 가드로 메움.
- 진입가는 **새 컬럼 `entry_price`**. 기존 `target_price`(매도 목표가)와 의미가 달라 재사용 안 함.
- 감시 주기 = **장중 09:00~15:30, 5분**. 하루 78콜, idea 종목 전부 1콜에 묶임.
- 알림 채널 = **broker가 직접 Discord 웹훅 POST**(A안). etl `notify()`는 venv가 달라 import 불가.
- 알림 끄기(`alert_off`)는 **도달 이력이 있을 때만** 허용. 서버에서도 검증(MCP `update_note` 대비).
- **자동 리셋 없음**. 주가가 진입가 위로 벗어나도 `alerted_on`/`alert_off`를 되돌리지 않는다.

---

## 상태 생애주기

```
idea  ── 사용자가 수동 생성. 이벤트 0건. entry_price 감시 대상
  │
  │ 첫 매수 체결(sync_trades → _reclassify)
  ▼
open ── 보유 중. 감시 종료(알림 자동 중지)
  │
  ├─ 일부 매도 → partial
  └─ 전량 매도 → closed
        │
        │ 재매수 → 같은 노트가 다시 open (_reclassify가 보유수량으로 재계산)
        ▼
```

`_reclassify`는 이미 보유수량 흐름으로 status를 재계산하므로 `closed → open` 복귀는 코드
변경 없이 동작한다. `idea → open` 승격도 "이벤트가 하나라도 생기면 _reclassify가 status를
덮어쓴다"는 기존 동작으로 자동 처리된다.

---

## 범위 / 비범위

- **범위**: `NoteStatus.idea`, `notes` 컬럼 3개(ALTER + 백필), `create_note` 기본값 변경,
  `alert_off` 서버 가드, broker 감시 루프 + Discord 알림, broker-web UI(진입가 입력·idea
  구분·알림 토글), unittest.
- **비범위**: 알림 이력 테이블, 스누즈(N일만 끄기), 조건식 DSL(상향돌파 등), 실시간 WS 감시,
  `dropped`/`archived` 같은 추가 상태.

---

## 스키마

`notes` 테이블에 컬럼 3개 추가(`db._migrate`, ALTER, 기존행 NULL/기본값):

| 컬럼 | 타입 | 의미 |
|---|---|---|
| `entry_price` | INTEGER NULL | 매수 진입 희망가(원). NULL이면 감시 안 함 |
| `alert_off` | INTEGER NOT NULL DEFAULT 0 | 1이면 사용자가 알림을 끔 |
| `alerted_on` | TEXT NULL | 마지막 알림 발송일 `YYYYMMDD`. NULL이면 도달 이력 없음 |

`NoteStatus`에 `idea = "idea"` 추가.

### 마이그레이션 (멱등)

1. 위 3개 컬럼을 `PRAGMA table_info` 확인 후 없을 때만 ALTER.
2. **백필**: `UPDATE notes SET status='idea' WHERE status='open' AND uid NOT IN
   (SELECT DISTINCT note_uid FROM note_events)` — 이벤트 0건인 open 노트는 실제로는 조사
   단계였으므로 idea로 내린다. 이벤트가 있는 노트는 건드리지 않는다.
3. 백필은 컬럼 추가 시점에만 1회 성격이지만, 조건 자체가 멱등이라 재실행해도 안전.

---

## 감시 루프

**위치**: `broker/main.py` — 기존 `_fill_sync_loop` 옆에 `_idea_price_loop`. lifespan에서 함께 기동.

**주기**: 5분(`_IDEA_POLL = 300`). 매 tick마다 KST 현재시각이 09:00~15:30 이고 평일일 때만 동작.

**대상 조회** (`store.list_idea_alert_candidates()`):

```sql
SELECT uid, symbol, name, entry_price FROM notes
WHERE status = 'idea'
  AND entry_price IS NOT NULL
  AND alert_off = 0
  AND (alerted_on IS NULL OR alerted_on != :today)
```

**판정**: 대상 종목 코드를 모아 `kiwoom.quotes.get_watchlist_quotes(codes)` **1콜**.
현재가 `<= entry_price` 면 도달.

**발송**: 도달 건마다 Discord 웹훅 POST 후 `alerted_on = 오늘` 기록.
- 기록은 발송 성공/실패와 무관하게 남긴다 — 웹훅이 죽었을 때 5분마다 재시도하며 스팸이 되는
  것보다, 하루 1회 시도로 끝내는 쪽이 안전. (실패는 로그로 남김)
- 알림 문구: `[진입가 도달] 삼성전자(005930) 68,000원 (목표 68,000원)`

**실패 격리**: 루프 전체를 `try/except`로 감싸 시세 조회 실패가 루프를 죽이지 않게 한다
(`_fill_sync_loop`와 동일 패턴).

### 알림 전송 (`broker/notes/alert.py`, 신규 ~15줄)

`DISCORD_WEBHOOK_URL`(root `.env`, 이미 존재)을 읽어 `requests.post`. 미설정이면 조용히
스킵하고 False 반환, 예외는 던지지 않는다. `etl/scripts/notify.py`의 축약판이며 재시도 없음
(하루 1회 시도 정책이라 재시도 가치가 낮다).

---

## API / 가드

- `NoteCreate`에 `entry_price` 추가. `create_note`는 status를 **`'idea'`로 시작**.
- `autolink._reconcile`이 체결로 만드는 노트는 `open`으로 만들어야 한다
  → `store.create_note(..., status=...)` 인자로 구분하거나, 생성 직후 `_reclassify`가
  덮어쓰므로 그대로 둬도 결과는 같다. **후자 채택**(이벤트가 붙는 즉시 `_reclassify` 호출됨).
- `NoteUpdate`에 `entry_price`, `alert_off` 추가.
- **가드**: `update_note`에서 `alert_off=1`로 바꾸려는데 해당 노트의 `alerted_on`이 NULL이면
  `HTTPException(400, "alert can be muted only after a price hit")`.
- `alert_off=0`(재개)은 언제나 허용.

---

## UI (broker-web)

- 노트 목록에 `idea` 구분(탭 또는 배지). `idea`는 손익 대신 진입가/현재가 표시.
- 노트 모달에 `진입가` 입력 필드.
- `alerted_on`이 있을 때만 "알림 끄기" 토글 노출.
- `pnl-summary`는 open/partial만 시세 조회하므로 idea는 자연히 제외 — 확인 필요.

---

## 구현 순서 (TDD)

각 단계는 **테스트 먼저 작성 → 실패 확인 → 구현 → 통과 확인**.

```
1. 스키마 + 상태
   test_notes_idea.py: idea 컬럼 마이그레이션, 구버전 DB 백필, create_note 기본 idea
   → verify: python -m unittest test_notes_idea

2. 감시 대상 조회 + 알림 가드
   test_notes_idea.py 확장: list_idea_alert_candidates 필터 4종,
                            alert_off 가드(400), alerted_on 기록
   → verify: 같은 명령

3. 감시 루프 + Discord 알림
   test_notes_idea_alert.py: 도달/미도달 판정, 하루 1회, 웹훅 미설정 시 무예외
   → verify: 같은 명령 (시세·웹훅은 monkeypatch)

4. broker-web UI
   → verify: 화면 확인 (idea 탭, 진입가 입력, 토글 노출 조건)
```

**테스트 실행**: broker는 pytest 없음. `.venv/Scripts/python.exe -m unittest test_notes_idea`
(기존 `test_notes_name.py` 규약과 동일: `NOTES_DB_PATH`를 tempfile로 바꾸고 `db._conn=None` 리셋)

---

## 손 안 대는 것

- `merge_notes_by_symbol` — 종목당 노트 1개 원칙 그대로.
- `_reclassify` — 보유수량 기준 재계산이라 idea→open, closed→open 모두 자동.
- `etl/scripts/notify.py` — broker는 별도 venv라 독립 전송 함수를 쓴다.
