# 유튜브 하루 2회 통합 시장 브리핑 Implementation Plan

**Status:** 완료 (2026-08-07). Task 등록·실전송까지 끝. 첫 정규 예약 실행은 2026-08-08 10:00.

**Goal:** 기존 채널별 영상 요약을 그대로 보존하면서, 신규 요약들을 시장 이슈별로 통합·중복 제거·정보가치 평가하여 매일 10:00와 18:00 KST에 Discord로 전달한다.

**Architecture:** 기존 `youtube_channels.json`의 채널별 `summary_hint`와 영상 단위 LangGraph는 수정하지 않는다. 기존 `youtube_video_summaries.summary_json`만 입력으로 사용하는 별도 일일 브리핑 계층을 추가한다. 신규 계층은 미전송 영상 선별 → 통합 LLM 1회 → 코드 검증·점수 재계산 → 결과 저장 → 결정론적 Discord 포맷·전송 순서로 동작한다.

**Tech Stack:** Python, SQLite, 기존 `new_etf_insight.llm.generate_json`, JSON Schema, 기존 `notify.py`, unittest, PowerShell, Windows Task Scheduler

---

## 0. 초안 대비 확정 변경 (검토 반영)

| # | 초안 | 확정 | 이유 |
|---|---|---|---|
| 1 | 신규 점수 축 30/25/20/15/10 | **텔레그램과 동일한 25/25/20/20/10** (`market_impact`/`evidence_quality`/`novelty`/`investment_relevance`/`cross_channel`) | `telegram_session_highlights` + `session_overview_schema.json`이 같은 계약을 이미 구현. 두 브리핑 점수를 같은 자로 비교 |
| 2 | 명목 window ∪ 48h grace | **미전송 + cutoff 48h 이내 하나만** | 명목 window(6~18h)는 항상 48h 안쪽 → 조건1 ⊂ 조건2, 죽은 조건 |
| 3 | 최대 5개 하이라이트 | **최대 3개** | 항목당 ~450자 × 5 > 1,900자. 초안은 자기모순 |
| 4 | close 16:00 | **evening 18:00** | 실측(7/1~8/6, 226건) 10~16시 게시는 세션당 1.4개뿐. 16~24시 88건을 포함해야 재료가 됨 |
| 5 | 모듈 3개 + 테스트 4개 | **`youtube_digest.py` 1개 + 테스트 1개** | 하루 6개 영상 규모에 3계층 분리는 과함 |
| 6 | `youtube_digest_langgraph/` 신규 디렉토리 | 기존 **`youtube_langgraph/`** 에 프롬프트·스키마 추가 | 그래프를 만들지 않으므로 새 패키지 불필요 |
| 7 | 제목 유사도로 중복 병합 | **source 영상 집합이 동일할 때만 병합** | 유사도 임계값 튜닝을 피하는 결정론적 규칙 |
| 8 | ps1에서 `collect_youtube --date` | **`run_youtube_channels.py --date`** | `collect_youtube.py`는 `--channel-id` 필수(단일 채널) |
| 9 | `status='no_content'`/`'failed'` | **`generated` / `sent`만** | 영상 0건이면 행을 만들지 않음. 전송 실패는 `generated`로 남아 재전송 |

또한 스키마 `maxLength`가 문장을 중간 절단하는 것을 실측 확인 → **프롬프트에 목표 분량을 쓰고 스키마 상한은 1.5배 가드**로 둔다.

---

## 1. 현재 코드와 변경 경계

### 확인된 현재 구조

- 채널 설정: `etl/scripts/youtube_channels.json` — 5채널 모두 `summary_mode=auto`, 채널별 `summary_hint` 적용됨.
- 영상 분석: `etl/scripts/youtube_langgraph/youtube_analysis_langgraph.py` — 대본 청크 요약 → 이슈 통합 → 종목 추출 → 저장.
- 영상별 결과: `etl/db/youtube_public.sqlite3`의 `youtube_video_summaries.summary_json`
  (`headline`, `issues`, `bullets`, `risk_or_caveat`, `stocks`).
- 기존 자동 배치: `ops/scheduled-tasks/run-youtube-daily.ps1` 01:00 KST — 전일 수집·자동 요약, 본문 digest 없음.
- 처리량 실측: 2026-07-01~08-06 226건, 하루 6.1개. 통합은 LLM 1회로 충분(실측 입력 26k자/11영상).

### 반드시 유지할 부분

- 기존 채널별 `summary_hint`와 영상 요약 프롬프트.
- 대본 수집·STT 폴백·영상별 요약·종목 추출 로직.
- `youtube_videos`, `youtube_video_summaries`, `youtube_stock_insights`의 기존 행과 의미.
- 기존 01:00 작업(야간 선처리·실패 회복).
- 알림은 기존 `notify.py`와 `telegram_report` sender 재사용.

### 추가한 흐름

```text
10:00 / 18:00 session runner
  → 신규 RSS 수집 (morning은 D-1도)
  → 기존 영상별 자동 요약 재사용 (--auto-only --lookback-days 1)
  → 미전송 + 48시간 이내 영상 요약 조회
  → 시장 이슈별 통합·중복 제거·정보가치 평가 (LLM 1회)
  → 출처·점수 코드 검증 + score_total 재계산
  → 브리핑 결과·최종 메시지 저장 (status=generated)
  → 1,900자 이하 Discord 메시지 생성
  → telegram_report Discord 웹훅 전송
  → 전송 성공 후에만 delivery ledger 확정 (status=sent)
```

---

## 2. 제품 동작

### 세션

- `morning` — 매일 10:00 KST. 전날·당일 채널 RSS 수집.
- `evening` — 매일 18:00 KST. 당일 채널 RSS 수집.
- 두 세션 모두 기존 자동 요약을 먼저 실행한 뒤 브리핑을 만든다.

### 입력 선별 (단일 규칙)

**아직 어떤 digest에도 전송되지 않았고, cutoff(세션 시각) 기준 48시간 이내에 게시된 영상 중 요약 내용이 존재하는 것.**

- 요약 행은 있으나 JSON이 깨졌거나 `headline`/`issues`/`bullets`가 모두 비면 제외한다.
  ledger에 넣으면 나중에 정상 요약으로 복구돼도 다시 브리핑되지 않기 때문.
- 게시 시각 비교는 SQL 문자열이 아니라 파싱한 instant 기준(수집 폴백이 `+09:00`을 쓴다).
- 해당 세션에서 STT/LLM이 실패했다가 다음 세션에 성공한 영상은 자동 회수된다.
- 최초 실행에도 48시간 컷이 걸려 과거 전체가 쏟아지지 않는다. backfill 없음.
- 감사용으로 `window_start_utc`(=cutoff−48h)/`window_end_utc`(=cutoff)를 run에 저장한다.

### 내용이 없을 때

- 신규 미전송 요약 0건 → LLM·전송·run 행 모두 생략하고 exit 0.
- 영상은 있으나 60점 이상 0건 → 한 줄 상태 메시지 전송
  (`🧭 유튜브 시장 브리핑 2026-08-07 오전 신규 영상 3개 · 채널 2개 · 60점 이상 주요 이슈 없음`).
- 통합 LLM 실패 → 예외로 exit≠0, ledger 미기록, 다음 세션 회수.
- 전송 실패 → exit 1, run은 `generated`로 남고 재실행 시 저장된 메시지를 그대로 재전송.

---

## 3. 사용자 메시지 형식

```text
🧭 유튜브 시장 브리핑 2026-08-07 오전
신규 영상 11개 · 채널 4개

🔥 주요 내용
• [93점] AI 투자 회수력과 순환금융 위험
  GPU 임대와 빅테크 실적은 AI 인프라의 수익성을 뒷받침해. 반면 투자·보증·구매가 얽혀 수요 과대평가 위험도 커졌어.
  근거: AI 매출 250% 증가·현금흐름 11억달러 흑자와 10GW 센터 보증 2,500억달러 협상이 제시됐어.
  차이: 한쪽은 1년 내 투자비 회수를, 다른 쪽은 순환금융과 현금흐름 악화를 강조해.
  불확실: 대규모 보증·증설은 협상 또는 계획 단계이고 사업 주체가 혼용된 정황이 있어.
  출처: 손에잡히는경제 · 이효석아카데미 · 한경글로벌마켓
  연결: 엔비디아 · 오라클 · NAVER · AI 데이터센터 · 클라우드

※ 정보가치 점수이며 사실 확정도·수익률 전망이 아님
```

### 표시 정책

- 표시 임계값 60점, 최대 **3개**(`MAX_HIGHLIGHTS`).
- 정렬: `score_total` 내림차순, 동점이면 최초 source 영상 게시 시각 오름차순.
- 필수 줄: 제목 / 핵심 내용 / 근거. 선택 줄: 차이·불확실(있을 때만) / 연결(있을 때만).
- 채널 이름은 LLM 출력이 아니라 `youtube_channels.json`의 `label`로 코드에서 치환.
- 1,900자 초과 시 문자열 절단이 아니라 낮은 순위 항목부터 제외.
- 영상 링크는 넣지 않는다.

---

## 4. 정보가치 점수 계약

LLM은 세부 점수만 매기고 `score_total`은 코드가 합산한다. 축·배점은 텔레그램 session overview와 동일하다.

- `market_impact` 0~25, `evidence_quality` 0~25, `novelty` 0~20, `investment_relevance` 0~20, `cross_channel` 0~10.

### 검증 규칙 (`validate_digest`)

- 범위를 벗어난 세부점수는 보정하지 않고 항목 거부 + `score_out_of_range` warning.
- `source_video_ids`는 실제 입력 집합과 교차검증, 유효 ref가 0이면 항목 거부.
- source 채널이 1개면 `cross_channel`을 0으로 강제 + `cross_channel_single_source` warning.
- `investment_links`는 입력 직렬화 텍스트에 실제로 등장하는 값만 남긴다.
- source 영상 집합이 같은 항목은 하나로 병합(점수 높은 쪽 유지).
- 경고 문구는 메시지 하단 고정.

---

## 5. 데이터 모델

기존 `etl/db/youtube_public.sqlite3` 사용, 기존 테이블 변경 없음.

```sql
CREATE TABLE IF NOT EXISTS youtube_digest_runs (
    date_kst TEXT NOT NULL,
    session TEXT NOT NULL CHECK(session IN ('morning', 'evening')),
    window_start_utc TEXT NOT NULL,
    window_end_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('generated', 'sent')),
    source_video_ids_json TEXT NOT NULL,
    digest_json TEXT,
    message_text TEXT,
    warning_json TEXT NOT NULL,
    error_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (date_kst, session)
);

CREATE TABLE IF NOT EXISTS youtube_digest_deliveries (
    channel_id TEXT NOT NULL,
    video_id TEXT NOT NULL,
    digest_date_kst TEXT NOT NULL,
    digest_session TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (channel_id, video_id)
);
```

- delivery insert는 `INSERT OR IGNORE` — 재실행해도 중복 행 없음.
- `mark_run_sent()`가 status 갱신과 delivery insert를 한 트랜잭션에서 수행하고 호출자가 커밋.

---

## 6. 통합 분석 모듈

### 파일

- `etl/scripts/youtube_digest.py` — 저장소·LLM 호출·검증·포맷·러너 전부(1 모듈).
- `etl/scripts/youtube_langgraph/prompts/market_digest.md`
- `etl/scripts/youtube_langgraph/market_digest_schema.json`
- `etl/tests/test_youtube_digest.py`

LangGraph 그래프는 만들지 않는다. 단일 통합 호출이면 충분하므로 `generate_json()`을 한 번 부른다.

### 함수 경계

```python
MAX_HIGHLIGHTS = 3
MIN_DISPLAY_SCORE = 60
MAX_MESSAGE_CHARS = 1900
SESSION_HOUR = {"morning": 10, "evening": 18}
GRACE_HOURS = 48

def ensure_schema(con) -> None: ...
def session_window(date_kst, session) -> tuple[str, str]: ...
def fetch_unsent_summaries(con, *, window_start_utc, cutoff_utc) -> list[dict]: ...
def get_digest_run(con, date_kst, session) -> dict | None: ...
def save_generated_run(con, *, date_kst, session, window, source_refs, digest, message_text, warnings) -> None: ...
def mark_run_sent(con, *, date_kst, session, source_refs) -> None: ...
def undelivered_refs(con, source_refs) -> list[str]: ...
def build_digest_input(rows) -> str: ...
def call_digest_llm(rows, date_kst, session, *, generate_fn=generate_json) -> dict: ...
def validate_digest(raw, input_rows) -> tuple[list[dict], list[str]]: ...
def collect_failure_note(channel_ids) -> str | None: ...
def format_digest_message(date_kst, session, rows, highlights, notes=None) -> str | None: ...
def run_digest(*, date_kst, session, db_path, channel, dry_run, collect_failed, summarize_failed, generate_fn, send_fn) -> int: ...
```

### 입력 원칙

- 원문 대본을 다시 넣지 않고 각 영상의 `summary_json`만 직렬화.
- 각 블록에 코드가 부여한 `source_ref = channel_id/video_id` 포함.
- 채널별 `summary_hint`는 재주입하지 않는다(이미 영상 요약에 반영됨).
- 종목 중심으로 사전 필터링하지 않는다(거시·정책·산업도 후보 유지).

### 출력 구조·길이

```json
{
  "overall_headline": "전체 흐름 한 줄",
  "highlights": [{
    "title": "...", "summary": "...", "evidence": "...",
    "view_difference": null, "uncertainty": null,
    "investment_links": ["산업 또는 종목"],
    "source_video_ids": ["channel_id/video_id"],
    "score_breakdown": {"market_impact": 0, "evidence_quality": 0, "novelty": 0,
                        "investment_relevance": 0, "cross_channel": 0}
  }]
}
```

- 프롬프트 목표 분량: title 30자, summary 120자, evidence 100자, 차이·불확실 각 70자, 링크 15자.
- 스키마 `maxLength`는 그 1.5배 가드(50/180/150/110/110/20) — 상한을 목표치에 맞추면 API가 문장을 중간에서 자른다(실측).
- 구조화 출력 제약: `required`에 모든 property를 넣어야 한다(누락 시 `invalid_json_schema`). null 허용은 `["string","null"]`.

---

## 7. 세션 오케스트레이션

### 파일

- `ops/scheduled-tasks/run-youtube-digest-session.ps1`
- `ops/scheduled-tasks/youtube-digest-morning.xml`, `youtube-digest-evening.xml`
- `ops/batches/youtube-twice-daily-digest.md`

### CLI

```text
.venv\Scripts\python.exe scripts\youtube_digest.py --date 2026-08-07 --session morning
  [--channel telegram_report] [--db <path>] [--dry-run]
```

1. 세션 cutoff와 48시간 window 계산.
2. 이미 `sent`면 아무것도 하지 않고 0.
3. `generated`가 있으면 저장 메시지 재사용(LLM 재호출 없음).
4. 아니면 미전송 요약 조회 → 0건이면 0으로 종료.
5. 통합 LLM → 검증 → 메시지 → run 저장(`generated`).
6. `--dry-run`이면 메시지·warning만 출력하고 DB·전송 없음.
7. 전송 성공 시 run `sent` + delivery ledger를 한 트랜잭션에서 커밋.
8. 전송 실패면 exit 1, ledger는 비어 있음.

### PowerShell runner

기존 `run-youtube-daily.ps1`/`run-telegram-session.ps1`의 `Invoke-Step` 패턴 재사용(native stderr가 exit 0을 실패로 승격하지 않게 호출 구간만 `Continue`). 파일은 **ASCII 전용**(PS 5.1의 no-BOM UTF-8 오독 회피).

```text
morning: run_youtube_channels(D-1) → run_youtube_channels(D) → run_youtube_analysis(D, --auto-only --lookback-days 1) → youtube_digest(D, morning)
evening: run_youtube_channels(D)   → run_youtube_analysis(D, --auto-only --lookback-days 1) → youtube_digest(D, evening)
```

### Task Scheduler

- `\new-etf_insight\daily-youtube-digest-morning` 10:00, `-evening` 18:00.
- `MultipleInstancesPolicy=IgnoreNew`, 배터리에서도 실행, `ExecutionTimeLimit=PT2H`
  (2시간 제한이 morning 실행과 evening 시작의 겹침도 막는다).
- 기존 `\new-etf_insight\daily-youtube` 01:00은 유지.

---

## 8. 구현 결과

| Task | 내용 | 상태 |
|---|---|---|
| 1 | 저장소 계약(미전송 선별·ledger 확정) 테스트 | 완료 |
| 2 | 통합 프롬프트·스키마 | 완료 |
| 3 | 검증·점수 재계산 | 완료 |
| 4 | Discord 포매터 | 완료 |
| 5 | 세션 러너·재시도 멱등성 | 완료 |
| 6 | PowerShell 세션 wrapper | 완료 |
| 7 | Task XML·운영 문서 | 완료 |
| 8 | 회귀 테스트 | 완료 (digest 55 + 기존 youtube 55, 모두 OK) |
| 9 | 운영 DB 복사본 dry-run | 완료 (11영상 → 하이라이트 8, 표시 3, 전송·운영 DB 무변경 확인) |
| 10 | Task 등록·실전송 1회 | 완료 (morning/evening Ready, 2026-08-07 evening 1회 실전송) |

### 검증 명령

```bash
cd C:/Users/mullu/.openclaw/workspace/etl/new-etf_insight/etl
PYTHONPATH=. uv run python -m unittest tests.test_youtube_digest -v
PYTHONPATH=. uv run python -m unittest \
  tests.test_youtube_analysis_langgraph tests.test_run_youtube_analysis \
  tests.test_collect_youtube tests.test_youtube_channels -v
```

---

## 9. 변경 파일

### 신규

- `etl/scripts/youtube_digest.py`
- `etl/scripts/youtube_langgraph/prompts/market_digest.md`
- `etl/scripts/youtube_langgraph/market_digest_schema.json`
- `etl/tests/test_youtube_digest.py`
- `ops/scheduled-tasks/run-youtube-digest-session.ps1`
- `ops/scheduled-tasks/youtube-digest-morning.xml`
- `ops/scheduled-tasks/youtube-digest-evening.xml`
- `ops/batches/youtube-twice-daily-digest.md`

### 최소 수정

- `docs/done/youtube_tech.md` (§13.3에 신규 배치 3줄)

### 변경하지 않은 파일

- `etl/scripts/youtube_channels.json`, `youtube_langgraph/youtube_analysis_langgraph.py`,
  `run_youtube_analysis.py`, `collect_youtube.py`, `notify.py`, `run-youtube-daily.ps1`
- 텔레그램 수집·요약·digest 전체, 주문·Kiwoom·백테스트 전체

---

## 10. 주요 위험과 대응

| 위험 | 대응 |
|---|---|
| 통합에서 채널별 관점 소실 | `source_video_ids` 필수 + `view_difference` 계약, 채널 label 코드 치환, 단일 채널 교차확인 점수 차단 |
| 늦은 STT·LLM 성공분 누락 | 48시간 미전송 회수, ledger 없는 영상만 대상 |
| 전송 실패 후 중복 | 전송 성공 후에만 ledger 기록, `generated` 메시지 재사용, 새 재시도 wrapper 없음 |
| Discord read timeout | `notify.py`는 read timeout을 재시도하지 않음 → 실제 전달됐는데 ledger가 비면 다음 세션에 같은 영상이 다시 통합될 수 있다(**at-least-once**, 수용) |
| 2,000자 제한 | 표시 3개 + 낮은 순위부터 제외, 프롬프트 분량 규칙, 스키마 가드 |
| 점수 오해 | 하단 고정 경고, 세부점수·warning을 `digest_json`/`warning_json`에 보존 |
| 01:00 작업과 중복 비용 | `--auto-only` + 요약 존재 확인으로 스킵, 01:00은 야간 선처리로 유지 |
| 임계값 60의 근거 부족 | dry-run 실측에서 상위 3건이 90/90/93점으로 나옴 — 운영 후 분포 보고 조정 |

---

## 11. 이번 구현에서 제외

- 채널별 영상 요약 프롬프트 재설계, 채널 추가·삭제, `summary_hint` 수정
- 과거 전체 backfill, 원문 대본 재분석, 외부 웹검색 사실 검증
- 매수·매도 추천, 자동 주문 연결
- 텔레그램 digest 로직·DB 통합, 01:00 작업 삭제·비활성화
- 웹 UI digest 이력 표시

---

## 12. 운영 개시 기록 (Task 10)

- morning/evening Task 등록 완료, 둘 다 `Ready`.
- 2026-08-07 evening 세션을 예약 시각(18:00) 전 13:57 KST에 수동으로 1회 실전송.
  `youtube_digest_runs`에 `sent` 1건, `youtube_digest_deliveries` 10건 기록.
- 그 결과 2026-08-07 18:00 예약 실행은 같은 `(date_kst, session)`이 이미 `sent`라
  아무 일도 하지 않고 종료된다(설계대로). 되돌리지 않고 그대로 둔다.
- 첫 정규 예약 실행: 2026-08-08 10:00 morning.

### 개시 후 확인할 것

1. 2026-08-08 10:00 실행의 `LastTaskResult`와 `etl/logs/youtube-digest-20260808.log`.
2. `youtube_digest_runs`에 `2026-08-08/morning` = `sent`.
3. 08-07 evening에서 이미 배달된 10영상이 다시 통합되지 않았는지 ledger로 확인.
