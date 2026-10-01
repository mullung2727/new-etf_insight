# 신규·재부각 테마 알림 — 설계

> 한 줄 요약: 텔레그램·유튜브 세션 배치 끝에 codex로 테마를 뽑고, 마지막 언급 후 `quiet_days`일(기본 30) 공백이면 `[신규 테마]`, 진행 단계가 바뀌면 `[진전]`을 테마 전용 디스코드 채널로 1테마 1통 보낸다.

- 상태: 설계 완료(2026-10-01). 구현·스케줄 변경·외부 전송은 아직 하지 않음.
- 목적: 조용하던 투자 테마가 다시 등장하거나 실질 진행상황이 바뀌면 원문과 함께 알린다. `PLAN_EARLY_THEME_SIGNALS.md`(종목별 중기 후보 선정)와는 별개인 **테마 이벤트 알림**.
- 기존 텔레그램·유튜브 분석, 주문 프로세스는 바꾸지 않는다.

## 확정 결정

| # | 항목 | 결정 | 누가 정했나 |
|---|---|---|---|
| 1 | 테마 단위 | 자동 발견 + 등록 테마 병행. 원문마다 테마를 추출하고 처음 보는 이름이면 새 테마로 등록. 사용자가 등록한 테마(`themes.registered=1`)는 ① 추출 프롬프트에 "반드시 확인할 테마" 목록으로 별도 전달 ② 알림 머리말 앞에 ⭐ 표시. 판정 규칙(공백·진전)은 자동 발견 테마와 동일 | 사용자 (2026-10-01) |
| 2 | LLM | 기존 분석과 같은 `codex` (`new_etf_insight.llm.generate_json`). 기존 분석 프롬프트는 수정하지 않고 테마 전용 호출을 별도로 추가 (기존 하이라이트·종목 결과 불변) | 사용자 (2026-10-01) |
| 3 | 처리 단계 | ① 테마 추출 호출: 세션 증분 원문 묶음 전체 → `테마명 + 근거 원문 번호`만 출력 ② 코드 판정: 테마별 마지막 언급을 DB 조회 → 공백 ≥ `quiet_days`면 `신규`, 아니면 `진전 후보` ③ 요약 호출: 알림 후보 테마만, 이번 원문 + 직전 `quiet_days`일 원문을 넣어 요약·진행 단계·근거 수준 판정. 원문 본문·링크는 LLM이 아닌 코드가 원문 번호로 DB에서 붙임 | 사용자 (2026-10-01) |
| 4 | 공백 기간 | `quiet_days` 파라미터(기본 30, 달력일). 코드 하드코딩 금지, 설정 파일에서 관리 | 사용자 (2026-10-01) |
| 5 | 텔레그램 범위 | 기존 분석과 같은 채널만 — `feed_role=discovery_source`(관리화면 "종목탐색 소스" 체크). `load_discovery_channels()` 재사용 | 사용자 (2026-10-01) |
| 6 | 유튜브 입력 | 대본 원문이 아니라 기존 영상 요약(`youtube_video_summaries.summary_json`) + 제목. 요약이 아직 없는 영상은 다음 실행까지 대기(제목만으로 판정 안 함). 원문 번호는 `video_id` | 사용자 (2026-10-01) |
| 6a | 유튜브 제외 | 운영에서는 유튜브를 테마 알림에 쓰지 않음 — `run-youtube-digest-session.ps1`의 테마 단계 제거(2026-10-01, 첫 실전 전). `run_theme_alert.py --source youtube` 코드는 남겨둠 | 사용자 (2026-10-01) |
| 9a | 채우기 중단 | 2026-10-01 텔레그램 채우기를 9/1~9/23(23일치)까지만 하고 중단. 기준 단계 저장·9/24~9/30·유튜브 채우기는 하지 않음 — codex 5시간 한도의 약 80%를 23회 호출로 소모(하루치 묶음 1회 ≈ 3.5%). 16:00 첫 실행의 미전송 기록 80건이 기준 단계 역할 | 사용자 (2026-10-01) |
| 7a | 21:00 추가 | 하루 3회: 10:00·16:00(기존 텔레그램 세션 끝) + 21:00 전용 작업 `daily-theme-alert-night`(`run-theme-alert-night.ps1`: 텔레그램 수집 → 테마 알림, 기존 종목 분석 없음). 목적: 당일 저녁까지 반영해 다음날 장 대응 | 사용자 (2026-10-01) |
| 7 | 실행 주기 | 1차: 새 스케줄 없이 기존 세션 끝에 붙임 — 텔레그램 `run-telegram-session.ps1`(10:00/16:00 — 00:00 evening 작업은 비활성) 파이프라인 뒤, 유튜브 `run-youtube-digest-session.ps1`(10:00/18:00) 영상요약 뒤. 알림의 게시·수집시각으로 지연 실측 후 고빈도 별도 스케줄 재검토 | 사용자 (2026-10-01) |
| 8 | 상태 저장 | 새 DB `etl/db/theme_alert.sqlite3` (텔레그램·유튜브 공용). 기존 텔레그램·유튜브 DB는 읽기 전용 | 사용자 (2026-10-01) |
| 9 | 첫 가동 | 가동 전 과거 30일(= `quiet_days`)치 텔레그램 원문·유튜브 요약에 테마 추출을 1회 돌려 언급 이력을 채우고, 끝에 그 기간 언급된 테마마다 요약 호출을 1회 돌려 현재 단계를 `theme_alerts`에 `kind='baseline'`, `sent_at=NULL`로 조용히 저장. 알림 없음. 진입점은 평소 코드에 `--backfill` 옵션만 붙여 재사용. (기준 단계가 없으면 첫 언급마다 "단계 없음→언급/전망"이 진전으로 잡혀 알림 폭탄이 나서) | 사용자 (2026-10-01) |
| 10 | 동의어 묶기 | 사전 + LLM. ① 코드가 정규화(앞뒤·중간 공백 제거, 소문자) 후 `theme_aliases`와 일치하면 해당 테마로 묶음 ② 나머지는 추출 호출에 **전체 테마 이름 목록**(최근 N일 한정 아님)을 같이 주고 LLM이 기존 테마 매칭 또는 새 이름 제시 ③ LLM이 기존 테마에 매칭한 새 표기는 `theme_aliases`에 `source='llm'`으로 자동 추가 | 사용자 (2026-10-01) |
| 11 | 수동 관리 | 관리화면·CLI 만들지 않음. 테마 등록·동의어 수정·테마 합치기는 에이전트에게 요청해 `theme_alert.sqlite3`에 SQL로 직접 반영. 그래서 스키마는 아래에 명시하고 단순하게 유지. 관리화면은 추후 가능성만 열어둠 | 사용자 (2026-10-01) |
| 12 | 전송 채널 | 테마 알림 전용 새 디스코드 채널. `.env`에 `THEME_ALERT_DISCORD_WEBHOOK_URL` 추가, `etl/scripts/notify.py`에 `theme_alert` 채널 1개 추가(`send_telegram_report`와 같은 방식). 진입점은 `load_dotenv` 필수(안 하면 웹훅 빈값으로 조용히 스킵) | 채널: 사용자 / 변수명: Claude 제안 (2026-10-01) |
| 13 | 알림 문구 | 머리말 `[신규 테마] 테마명 (N일 만에 재등장)` 또는 `[진전] 테마명 — 이전 단계 → 새 단계`, 본문 요약 2~3줄 · 근거 수준 · 게시시각/수집시각 · 원문 링크 **최대 5개**(게시 이른 순, 초과분 "외 N건") | 링크 수: 사용자 / 문구·정렬: Claude (2026-10-01) |
| 14 | 전송 단위 | 테마 1개 = 메시지 1통. 세션 내 여러 테마도 묶지 않음 | 사용자 (2026-10-01) |
| 15 | 진전 알림 | 진행 단계 `언급/전망 → 검토 → 협의 → 계약·발주 → 취소`. 요약 호출이 판정한 단계가 마지막 알림 단계와 다르면 `[진전]` 발송. 같은 단계 재보도는 무시. 횟수 제한 없음 | 단계 정의: Claude 제안 / 제한 없음: 사용자 (2026-10-01) |
| 16 | 공백 기준점 | 공백은 **마지막 언급의 게시시각**부터 잰다. 알림 안 보낸 반복 언급도 마지막 언급을 갱신. 예) 9/1 신규 알림 → 9/20 반복 언급 → 10/15 언급은 25일 공백이라 신규 아님, 10/21 이후 언급이면 신규 | 사용자 (2026-10-01) |
| 17 | 근거 수준 | `참여 가능성 / 증권사 전망 / 공식 확정`. `공식 확정`은 정부·기업 공시·보도자료가 근거일 때만. 원문이 "확정"이라 써도 출처가 공식이 아니면 `증권사 전망` 이하 | 사용자 (2026-10-01) |
| 18 | 실패 알림 | 테마 알림 단계가 실패하면 같은 테마 알림 채널(`theme_alert`)로 `[테마 알림 실패] 소스·날짜·세션·에러 요약` 전송. 기존 세션은 성공 처리 유지 | 사용자 (2026-10-01) |
| 19 | 과거 원문 상한 | 요약 호출에 넣는 과거 원문은 테마당 최근 `max_past_refs`건(기본 10 — 2026-10-01 호출량 때문에 20→10). 설정 파일에서 관리 | 사용자 (2026-10-01) |

## 판정 기준

- **신규**: 테마의 마지막 언급 게시시각과 이번 원문 게시시각 차이 ≥ `quiet_days`일, 또는 언급 이력이 아예 없음. "세상 최초"가 아니라 **우리가 수집한 소스 기준 재등장**.
- **진전**: 공백 < `quiet_days`이지만 요약 호출이 판정한 단계 ≠ `theme_alerts`의 마지막 단계(`baseline` 포함).
- **반복**: 그 외. 언급 이력만 저장하고 알림 없음.
- 묶지 않는 것: 막연한 LNG 일반론, 관광지 알래스카처럼 투자 테마가 아닌 동음어 — 추출 프롬프트에 예시로 명시.
- 복제글·재전송: 별도 제거 로직 없음. 판정은 언급 "횟수"를 쓰지 않고 "마지막 언급 시각"만 쓰므로 복제글은 반복 언급으로 흡수됨.
- 한 묶음 안에 같은 테마 원문이 여럿이면 가장 이른 게시시각으로 공백을 잰다.

## 흐름 (1회 실행)

진입점 `etl/scripts/run_theme_alert.py --source telegram|youtube --date D [--start-date S] [--session X] [--no-send] [--backfill]`

- `--no-send`: 전송 대신 로그 출력(실데이터 재생용). `--backfill`: 결정 9의 첫 가동 채우기(알림 없음 + 끝에 baseline 저장).

1. **원문 읽기** (읽기 전용 연결)
   - 텔레그램: `telegram_posts` 중 기간 내·종목탐색 채널·`theme_processed`에 없는 글. 게시=`posted_at_utc`, 수집=`created_at`
   - 유튜브: `youtube_video_summaries`가 있고 `theme_processed`에 없는 영상 + `youtube_videos.title`. 게시=`published_at_utc`, 수집=`created_at`
2. **추출 호출** → 테마명 + 원문 번호 목록. 입력 원문 없으면 호출 안 함.
3. **묶기**(결정 10) → 테마 id 확정, 새 테마는 `themes`에 추가.
4. **판정**(판정 기준) → 테마별 `신규 / 진전 후보 / 반복`.
5. **요약 호출** — `신규`·`진전 후보` 테마만. 과거 원문은 테마당 최근 `max_past_refs`건까지. 단계·근거 수준·요약 2~3줄. `진전 후보` 중 단계 동일이면 반복으로 강등.
6. **전송** — 테마 1통씩 `notify(..., channel="theme_alert")`. `--no-send`면 전송 대신 로그 출력.
7. **커밋** — `theme_mentions`·`theme_alerts`·`theme_processed`를 한 트랜잭션으로 저장.
   - 재실행: 처리된 원문은 1단계에서 빠지므로 중복 없음.
   - 한계: 6 전송 후 7 커밋 전에 죽으면 재실행 시 같은 알림 1회 재전송 가능. 감수.

## DB 스키마 — `etl/db/theme_alert.sqlite3`

```sql
CREATE TABLE themes (
  theme_id   INTEGER PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,      -- 대표 이름
  registered INTEGER NOT NULL DEFAULT 0, -- 1 = 사용자 등록 테마
  created_at TEXT NOT NULL
);
CREATE TABLE theme_aliases (
  alias_norm TEXT PRIMARY KEY,          -- 정규화 표기 (공백 제거·소문자)
  theme_id   INTEGER NOT NULL REFERENCES themes(theme_id),
  source     TEXT NOT NULL,             -- 'manual' | 'llm'
  created_at TEXT NOT NULL
);
CREATE TABLE theme_mentions (
  theme_id     INTEGER NOT NULL REFERENCES themes(theme_id),
  source       TEXT NOT NULL,           -- 'telegram' | 'youtube'
  ref          TEXT NOT NULL,           -- post_ref | video_id
  posted_at    TEXT NOT NULL,           -- UTC ISO
  collected_at TEXT NOT NULL,           -- 원본 DB created_at
  PRIMARY KEY (theme_id, source, ref)
);
CREATE TABLE theme_alerts (
  alert_id       INTEGER PRIMARY KEY,
  theme_id       INTEGER NOT NULL REFERENCES themes(theme_id),
  kind           TEXT NOT NULL,         -- 'new' | 'progress' | 'baseline'(첫 가동, 미전송)
  stage          TEXT NOT NULL,         -- 결정 15 단계
  evidence_level TEXT NOT NULL,         -- 결정 17
  refs_json      TEXT NOT NULL,         -- [{source, ref}]
  message        TEXT NOT NULL,
  sent_at        TEXT                   -- --no-send면 NULL
);
CREATE TABLE theme_processed (
  source       TEXT NOT NULL,
  ref          TEXT NOT NULL,
  processed_at TEXT NOT NULL,
  PRIMARY KEY (source, ref)
);
```

- 마지막 언급 = `MAX(theme_mentions.posted_at)`, 현재 단계 = 마지막 `theme_alerts.stage`. 따로 저장하지 않음(두 군데 두면 어긋남).
- 처리 표시는 기존 분석 진행표(`telegram_analysis_watermark`)와 공유하지 않음 — 공유하면 서로 건너뜀.
- 테마 합치기(수동, SQL): 흡수되는 테마의 `theme_aliases`·`theme_mentions`·`theme_alerts`의 `theme_id`를 남길 테마로 바꾸고 `themes`에서 삭제.

## 설정 — `etl/scripts/theme_alert/theme_alert.json`

```json
{ "quiet_days": 30, "max_past_refs": 10 }
```

- 위치는 `etl/scripts/close_bet.json` 관례를 따름 (Claude 제안).

## 파일

| 구분 | 경로 | 내용 |
|---|---|---|
| 신규 | `etl/scripts/run_theme_alert.py` | 진입점(흐름 1~7) |
| 신규 | `etl/scripts/theme_alert/` | `theme_alert.json`, `prompts/theme_extract.md`, `prompts/theme_summary.md`, 출력 스키마 json 2개 |
| 신규 | `etl/tests/test_theme_alert.py` | 아래 코드 테스트 |
| 수정 | `etl/scripts/notify.py` | `theme_alert` 채널 1개 |
| 수정 | `ops/scheduled-tasks/run-telegram-session.ps1`, `run-youtube-digest-session.ps1` | 기존 단계 뒤에 테마 알림 1단계 추가. 실패해도 기존 세션 결과는 실패 처리 안 함(실패 알림은 결정 18) |
| 안 건드림 | 텔레그램·유튜브 분석 코드·프롬프트, 원문 DB | 읽기만 |

## 테스트

- 코드 테스트 (codex 응답 가짜 고정, `PYTHONPATH=. uv run python -m unittest`):
  - 공백 경계: 마지막 언급 후 29일/30일/31일 → 신규 여부
  - 알림 없는 반복 언급도 마지막 언급 갱신 (결정 16 예시 그대로)
  - `quiet_days`·`max_past_refs`를 설정에서 바꾸면 판정·요약 입력이 따라 바뀜 (하드코딩 금지 확인)
  - 실패: 추출 호출 예외 시 `theme_alert` 채널로 실패 메시지 1통, 종료코드는 기존 세션을 깨지 않음, 처리 표시 미저장(다음 실행에 재처리)
  - 동의어: 사전 일치 시 LLM 결과와 무관하게 묶임, LLM 매칭 표기가 사전에 자동 추가
  - 진전: 단계가 바뀔 때만 발송, 같은 단계 재보도는 미발송
  - 재실행: 같은 세션 두 번 돌려도 언급 이력·알림 중복 없음
  - 문구: 링크 6개 이상이면 5개 + "외 N건", 게시 이른 순
  - 유튜브: 요약 없는 영상은 판정 안 하고 다음 실행에 처리
  - 등록 테마: 추출 프롬프트에 "반드시 확인할 테마" 목록이 들어가고, 알림 머리말에 ⭐ 붙음 (미등록은 둘 다 없음)
  - `--no-send`: 알림 0건 전송, 언급 이력만 저장
  - `--backfill`: 전송 0건, 언급된 테마마다 `baseline` 1행 저장 → 직후 같은 단계 언급은 미발송, 단계 상승 시만 `[진전]`
  - 금지 확인: 원문 DB는 읽기 전용 연결이라 쓰기 시도 시 에러
- 실데이터 재생 (진짜 codex, `--no-send`) — 사례 1개: **알래스카 LNG**
  - 텔레그램 DB 월별 언급: 1월 7 · 2~7월 2~5건 · 8월 0건 · 9월 17건 (전체 채널 기준, 종목탐색 채널 한정 시 재확인)
  - 9월 첫 언급 직전 30일을 채운 뒤 9월 세션을 순서대로 재생 → 9월 첫 언급에서 `신규` 1회, 이후 반복은 미발송이면 통과
  - 사후 적재 글은 당시 실시간 포착으로 주장하지 않음 (수집시각 `created_at` 함께 표시)

## 보류

- 요약 호출 대상 축소: 추출 호출 출력에 테마별 "상태 변화 신호" 칸을 추가해 `신규 + 신호 있는 테마`만 요약하는 안(세션당 요약 8회 → 1~2회 추정). 2026-10-01 현행 유지 결정 — 실행마다 신규 원문만 처리하므로 하루 3회가 3배 비용은 아님. 21:00 실행 후 실제 codex 한도 소모를 보고 재검토 — 사용자 (2026-10-01)

- 종목 급등 뒤 따라붙는 사후 해설 억제: 1차 구현엔 넣지 않음(진전은 단계 동일이면 걸러지지만, 신규는 안 걸러짐). 운영하며 오탐 빈도를 보고 요약 호출 판정 추가 여부 결정 — 사용자 (2026-10-01)
