# Telegram 공개 채널 수집 배치 — 구현 계획

## 한 줄 요약

Telegram 공개 채널(`https://t.me/s/<channel>`)의 원문 글을 **채널 + 일자 입력**으로 수집해 SQLite에 누적 저장하고, 요약·전송은 별도 프로세스로 분리한다. 운영 배치는 Hermes/OpenClaw에 의존하지 않고 이 프로젝트의 `ops/` + Windows Task Scheduler 기준으로 관리한다.

---

## 배경

현재 Hermes 외부 스크립트로 `butler_works` 공개 채널의 2026-07-01 KST 글 45개를 JSON으로 수집해 검증했다.

- 테스트 채널: `https://t.me/butler_works`
- 실제 접근 URL: `https://t.me/s/butler_works`
- 테스트 결과: 2026-07-01 KST 기준 45개 글 수집 성공
- 임시 결과 파일: `C:/Users/mullu/AppData/Local/hermes/cache/butler_works_2026-07-01.json`
- **검증된 수집 스크립트: `C:/Users/mullu/AppData/Local/hermes/scripts/telegram_public_channel_scrape.py`** — stdlib(urllib+re)만 사용, UA `Mozilla/5.0`, `timeout=40`, `?before=` pagination 구현 완료. 파서를 새로 쓰지 말고 이 스크립트를 포팅한다.

이제 임시 Hermes 캐시가 아니라 `new-etf_insight` 프로젝트 안의 정식 배치로 편입한다.

---

## 핵심 결정

1. **Hermes 의존 제거**
   - 수집, 저장, 규칙 기반 가공, Telegram/Discord 전송은 Python 배치로 처리한다.
   - Hermes gateway가 꺼져 있어도 Windows Task Scheduler로 돌아가야 한다.
   - LLM 요약이 필요할 경우에도 수집 원본과 분리된 후속 단계로 둔다.

2. **원문 저장은 SQLite**
   - 날짜/채널이 늘어나면 JSON 파일 관리가 어려워진다.
   - 예: 2년 × 10개 채널이면 날짜별 JSON보다 SQLite가 조회·중복방지·재처리에 유리하다.
   - JSON은 운영 저장소가 아니라 export/debug 용도로만 사용한다.

3. **수집과 요약 분리**
   - 수집기는 Telegram 원문을 최대한 있는 그대로 저장한다.
   - 요약기는 저장된 원문 DB를 읽어 별도로 결과를 만든다.
   - 전송기는 요약 결과 또는 원문 추출 결과만 받아 알림 채널로 보낸다.

4. **프로젝트 배치 관리 규칙 준수**
   - 배치 정의는 `ops/batches/`를 source of truth로 둔다.
   - 실행 스크립트는 `ops/scheduled-tasks/`에 둔다.
   - 알림은 기존 `etl/scripts/notify.py`(Telegram `send_telegram` 이미 구현됨), `etl/scripts/send_report_messages.py`를 재사용한다. 새 알림 코드는 만들지 않는다.

5. **채널 목록/처리규칙의 source of truth는 `telegram_channels.json`** (2026-07 갱신 — 아래 "진행 상태 + 일반화 계획" 참조)
   - 수집 스크립트는 호출 1회당 채널 1개만 처리한다(단일 `--channel-url`).
   - 채널별 주소·첨부 다운로드 여부·패턴은 `etl/scripts/telegram_channels.json`에 데이터로 둔다.
   - 스케줄(시간/간격)은 config가 아니라 `ops/batches/openclaw-cron.registry.json` cron이 담당한다 — 소스 이원화 금지.

---

## 목표

### 1차 목표

- 공개 Telegram 채널 URL과 KST 일자를 입력받아 해당 날짜 글을 수집한다.
- 수집 결과를 `etl/db/telegram_public.sqlite3`에 upsert한다.
- 같은 채널/글을 여러 번 수집해도 중복 저장하지 않는다.
- 특정 채널/일자의 원문을 JSON 또는 Markdown으로 export할 수 있다.
- Hermes 없이 CLI와 Windows Task Scheduler에서 실행 가능하다.

### 비목표

- Telegram User API/Telethon 로그인 방식은 이번 범위에서 제외한다.
- 비공개 채널/초대가 필요한 방 수집은 제외한다.
- LLM 요약 품질 개선은 수집 배치 완료 후 별도 단계로 둔다.
- 매매/주문 자동화와 연결하지 않는다.

---

## 추천 파일 구조

```text
etl/
  db/
    telegram_public.sqlite3              # 생성 파일 (etl/db/는 이미 .gitignore 처리됨)
  exports/
    telegram/                            # export 결과물. .gitignore에 etl/exports/ 추가
  scripts/
    collect_telegram_public.py           # 수집: URL/channel/date → SQLite
    export_telegram_public.py            # export: SQLite → JSON/Markdown
    summarize_telegram_public.py         # 후속 요약: SQLite → 요약 결과
    send_telegram_public_report.py       # 후속 전송: 요약/메시지 → notify
  tests/
    test_collect_telegram_public.py
    test_export_telegram_public.py

ops/
  batches/
    daily-telegram-public-channel.md
  scheduled-tasks/
    run-telegram-public-channel.ps1
    telegram-public-channel.xml           # registry에서 Export-WindowsScheduledTaskSpecs.ps1로 생성
```

새 패키지가 필요하면 `etl/AGENTS.md` 규칙에 따라 `etl` 디렉토리에서 `uv add <pkg>`를 사용한다. `pip install`은 사용하지 않는다.

**HTML 파싱은 새 의존성 없이 간다.** 검증된 Hermes 스크립트가 stdlib(urllib+re+html)만으로 이미 동작했으므로 그대로 포팅한다. beautifulsoup4/lxml은 추가하지 않는다(포팅 중 정말 막히면 그때 `uv add beautifulsoup4` 재검토).

---

## 데이터 모델

SQLite 파일:

```text
etl/db/telegram_public.sqlite3
```

### `telegram_channels`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `channel` | TEXT PRIMARY KEY | URL slug. 예: `butler_works` |
| `source_url` | TEXT NOT NULL | 입력 URL 또는 정규화 URL |
| `title` | TEXT NULL | 가져올 수 있으면 채널명 |
| `created_at` | TEXT NOT NULL | ISO8601 |
| `updated_at` | TEXT NOT NULL | ISO8601 |

### `telegram_posts`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `channel` | TEXT NOT NULL | 예: `butler_works` |
| `post_id` | INTEGER NOT NULL | Telegram post id |
| `post_ref` | TEXT NOT NULL | 예: `butler_works/20505` |
| `posted_at_utc` | TEXT NOT NULL | ISO8601 UTC |
| `date_kst` | TEXT NOT NULL | `YYYY-MM-DD` (KST 변환 후 날짜) |
| `text` | TEXT NOT NULL | 원문 텍스트. 미디어만 있는 글은 빈 문자열 `''` 허용 |
| `links_json` | TEXT NOT NULL | 링크 배열 JSON 문자열 |
| `raw_json` | TEXT NULL | 파서 결과 원본 JSON (미디어 유무 등 부가정보 포함) |
| `created_at` | TEXT NOT NULL | ISO8601 |
| `updated_at` | TEXT NOT NULL | ISO8601 |

주의:

- KST 시각이 필요하면 `posted_at_utc`에서 파생한다. 별도 `posted_at_kst` 컬럼은 두지 않는다(중복).
- 이미지/영상만 있고 텍스트 없는 글도 저장한다(`text=''`). skip하지 않는다 — post_id 연속성이 재수집 검증에 필요하다.
- 텔레그램 앨범(사진 묶음)은 메시지 여러 개가 한 묶음으로 렌더링될 수 있다. 파서는 post_id 단위로 1글=1row를 유지한다.

제약:

```sql
UNIQUE(channel, post_id)
```

인덱스:

```sql
CREATE INDEX IF NOT EXISTS idx_telegram_posts_date
ON telegram_posts(channel, date_kst);
```

조회는 전부 `(channel, date_kst)` 경유이므로 이 인덱스 하나면 충분하다.

### `telegram_summaries` — 2차 단계

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | 내부 id |
| `channel` | TEXT NOT NULL | 채널 |
| `date_kst` | TEXT NOT NULL | 요약 대상 일자 |
| `summary_type` | TEXT NOT NULL | 예: `brief`, `table`, `llm` |
| `content` | TEXT NOT NULL | Markdown 또는 JSON 문자열 |
| `source_post_count` | INTEGER NOT NULL | 사용한 글 수 |
| `created_at` | TEXT NOT NULL | ISO8601 |
| `updated_at` | TEXT NOT NULL | ISO8601 |

제약:

```sql
UNIQUE(channel, date_kst, summary_type)
```

재실행 정책: 같은 `(channel, date_kst, summary_type)` 재요약 시 upsert로 `content`/`source_post_count`/`updated_at`을 덮어쓴다. 에러로 두지 않는다.

---

## CLI 설계

### 수집

```bash
uv run python etl/scripts/collect_telegram_public.py \
  --channel-url https://t.me/butler_works \
  --date 2026-07-01 \
  --db etl/db/telegram_public.sqlite3
```

옵션:

- `--channel-url`: `https://t.me/<channel>` 또는 `https://t.me/s/<channel>` 허용
- `--channel`: URL 대신 slug 직접 입력. `--channel-url`과 상호배타, 둘 중 하나 필수
- `--date`: KST 기준 `YYYY-MM-DD`. 필수
- `--db`: 기본값은 **cwd가 아니라 스크립트 위치 기준으로 앵커**한다 — `Path(__file__).resolve().parents[1] / "db" / "telegram_public.sqlite3"`. 운영 runner는 `Set-Location etl` 후 실행하므로 root 기준 상대경로 문자열을 기본값으로 쓰면 `etl/etl/db/`가 생긴다(.env 상대경로 사고와 동일 클래스)
- `--limit-pages`: 페이지 상한 안전장치. 기본 30 (페이지당 약 20글 → 하루치 커버 충분)
- `--dry-run`: DB 저장 없이 수집/파싱 결과만 출력

출력 예:

```text
[collect_telegram_public] channel=butler_works date=2026-07-01 fetched=45 inserted=45 updated=0 skipped=0
```

### Export

```bash
uv run python etl/scripts/export_telegram_public.py \
  --channel butler_works \
  --date 2026-07-01 \
  --format json \
  --out etl/exports/telegram/butler_works_2026-07-01.json
```

지원 포맷:

- `json`: 원문 전달/debug용
- `md`: 사람이 읽는 요약 전 단계 확인용

### 요약 — 2차 단계

```bash
uv run python etl/scripts/summarize_telegram_public.py \
  --channel butler_works \
  --date 2026-07-01 \
  --summary-type brief
```

초기 구현은 LLM 없이 규칙 기반으로 한다. **단, 이는 채널 글이 고정 템플릿(증권사 리포트 요약 형식)이라는 가정 위에서만 성립한다.** 구현 전 실제 저장된 원문 샘플로 템플릿 일관성을 먼저 확인하고, 규칙으로 안 되면 이 단계를 건너뛰고 바로 `summary_type=llm`으로 간다.

추출 대상:

- 종목명/코드
- 제목
- 작성자/증권사
- 투자의견
- 목표주가
- 핵심 bullet 1~2개

LLM 요약은 나중에 `summary_type=llm`으로 추가한다.

---

## 수집 방식

1. 채널 URL 정규화
   - 입력 `https://t.me/butler_works` → `https://t.me/s/butler_works`
   - 입력 `https://t.me/s/butler_works`는 그대로 사용

2. 공개 웹 HTML 요청
   - Bot token, Telegram login, Hermes 필요 없음
   - User-Agent `Mozilla/5.0`, **timeout 명시(검증 스크립트 기준 40초)** — timeout 없으면 응답 지연 시 배치가 무한 hang
   - 실패 시 HTTP status와 URL을 명확히 로그에 남긴다.
   - 일부 채널은 웹 미리보기 비활성으로 `t.me/s/`가 글 목록 없이 리다이렉트된다. 이 경우 "이 채널은 웹 수집 불가" 명확한 에러 메시지 + exit code 1로 종료한다. 조용히 fetched=0 처리하지 않는다.
   - 페이지 간 요청에 1초 내외 딜레이를 둔다(차단 방지).

3. 글 파싱
   - post id
   - 게시시각 UTC
   - KST 변환 일자
   - 텍스트
   - 링크 목록
   - raw 파서 결과

4. 날짜 필터
   - 반드시 KST 기준 `date_kst`로 필터한다.
   - UTC 날짜로 자르면 새벽 글이 어긋날 수 있다.

5. Pagination
   - `t.me/s/<channel>?before=<post_id>` 패턴을 사용해 이전 글을 가져온다.
   - 중단 조건: 현재 페이지에서 가장 오래된 글(고정/pinned 글 제외)의 `date_kst`가 대상 날짜보다 과거이면 중단한다.
   - 고정 글은 페이지 최상단에 반복 노출될 수 있으므로 중단 판단에서 제외한다.
   - `--limit-pages`(기본 30)로 무한 루프를 방지한다.

6. 저장
   - `(channel, post_id)` 기준 upsert
   - 이미 있는 글은 text/link/raw 변경 가능성을 고려해 update 가능

---

## 운영 배치 설계

`ops/batches/daily-telegram-public-channel.md`에는 다음을 적는다.

- 대상 채널 목록
- 실행 시간
- 수집 대상 날짜 규칙
  - 기본: 전일 KST
  - 필요 시 당일도 가능
- 실행 명령
- 검증 기준
- 실패 시 알림 규칙

예시:

```text
채널: butler_works (추가 채널은 runner ps1 + 이 문서에 함께 등록)
스케줄: 매일 07:30 KST
대상 일자: 전일 KST
실행: ops/scheduled-tasks/run-telegram-public-channel.ps1
성공 기준:
  - 채널별 HTTP 요청 성공 + HTML 파싱 성공 + exit code 0
  - fetched=0은 실패가 아니지만(글 없는 날 가능) 보고 메시지에 명시해 사람이 확인
실패 알림: send_report_messages.py --best-effort (기존 runner 패턴)
```

PowerShell runner는 기존 `ops/scheduled-tasks/run-*.ps1` 스타일을 따른다.

- 채널별 `Invoke-Step` 반복 호출, 로그는 `etl/logs/`에 날짜별 파일
- python은 기존 운영 배치와 동일하게 `.venv\Scripts\python.exe` 절대/상대 경로 직접 호출 (`uv run`은 개발/수동 실행 전용)
- 한 채널 실패가 다른 채널 수집을 막지 않게 채널 단위로 오류를 모아 마지막에 실패 보고

---

## 알림/전송

단순 알림은 Hermes 없이 처리한다.

- Discord: 기존 `NOTIFY_CHANNEL=discord`, `DISCORD_WEBHOOK_URL` 구조 재사용
- Telegram: `notify.py`의 `send_telegram()` 이미 구현되어 있음 — 그대로 재사용, 새 코드 불필요
- Bot token은 `.env`에만 둔다.
- 토큰을 로그/문서/테스트 fixture에 남기지 않는다.

Telegram 직접 전송에 필요한 값:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

주의:

- Bot token은 비밀번호와 같으므로 절대 출력하지 않는다.
- 코드/문서에는 실제 토큰을 쓰지 않는다.

---

## 구현 순서

### 1단계 — DB + 수집기

- `collect_telegram_public.py` 작성
- SQLite 테이블 자동 생성
- `butler_works`, `2026-07-01` 수집 재현
- 성공 기준:
  - 검증 당시 45개였으나 공개 웹뷰는 삭제 글이 빠지므로 구현 시점에 줄어들 수 있음 — 45개는 참고치
  - 실제 기준: 40개 이상 수집 + 기존 임시 JSON(`butler_works_2026-07-01.json`)의 post_id와 대조해 누락분이 전부 "웹뷰에서 사라진 글"인지 확인
  - 주의: post_id gap이 전부 삭제 글은 아니다 — 앨범(사진 묶음)은 웹뷰에서 위젯 1개=post_id 1개로 렌더링되고 멤버 메시지 id들이 gap으로 남는다. gap=삭제로 단정하지 말 것
  - 재실행 시 중복 증가 없음

### 2단계 — Export

- `export_telegram_public.py` 작성
- JSON export가 기존 임시 JSON과 유사한 구조로 나오는지 확인
- Markdown export는 사람이 읽을 수 있게 간단히 생성

### 3단계 — 테스트

- HTML fixture 또는 최소 parser 단위 테스트 추가
- DB upsert 테스트 추가
- 날짜 KST 필터 테스트 추가

### 4단계 — ops 배치 편입

- `ops/batches/daily-telegram-public-channel.md` 추가
- `ops/scheduled-tasks/run-telegram-public-channel.ps1` 추가
- `ops/batches/openclaw-cron.registry.json` 반영 **필수** (README 규칙: registry 먼저 업데이트)
- XML은 손으로 만들지 않고 `Export-WindowsScheduledTaskSpecs.ps1`로 registry에서 export/register
- `Test-OpenClawBatchRegistry.ps1` 통과 확인

### 5단계 — 요약/전송 분리

- `summarize_telegram_public.py`는 DB를 읽어 요약 결과 생성
- `send_telegram_public_report.py` 또는 기존 `send_report_messages.py`로 전송
- LLM 요약은 이 단계 이후 별도 작업으로 판단

---

## 진행 상태 + 일반화 계획 (2026-07 갱신)

### 완료

- **1~3단계 완료**: `collect_telegram_public.py`, `export_telegram_public.py`, 단위테스트(실 fixture 포함). 파서는 검증 스크립트 포팅 중 발견한 마크업 순서 버그(footer `<time>`이 text div 뒤) 수정 — data-post 경계 블록 파싱으로 재작성.
- **첨부 다운로드 추가 완료(companyreport 하드코딩)**: `download_telegram_report_attachments.py`. 증권사 종목 리포트 PDF만 골라 `종목명_종목코드/일자_postid.pdf`로 저장. 실검증: 07-01(5개)/07-02(2개) 실 PDF 다운로드 성공(`%PDF-1.5` 확인). 저장경로 `etl/exports/telegram/stock_reports/`.
- **채널별 결과 차이 근거**: companyreport는 `[기업]종목명(코드.KS|KQ/의견)` 글만 종목코드 추출 가능 → 그 글만 다운로드 대상. `[시장]` 등 코드 없는 글은 폴더 못 만들어 자연 스킵(`skipped_no_ticker`).

### 일반화 계획 (다음 작업)

지금 `download_telegram_report_attachments.py`는 `CHANNEL`/`REPORT_LINK_RE`/`TICKER_RE` 상수를 하드코딩. 이를 채널별 config로 분리한다.

**config 파일**: `etl/scripts/telegram_channels.json` (스크립트 옆, 스크립트만 소비)

```json
{
  "companyreport": {
    "source_url": "https://t.me/s/companyreport",
    "attachments": {
      "link_pattern": "stockinfo7\\.com/stock/report/url/\\d+",
      "ticker_pattern": "([^()\\[\\]]+?)\\((\\d{6})\\.(?:KS|KQ)[^)]*\\)",
      "out_subdir": "stock_reports"
    }
  },
  "butler_works": {
    "source_url": "https://t.me/s/butler_works"
  }
}
```

- `attachments` 있으면 수집+다운로드, 없으면 수집만.
- **후처리 모델 = flag/패턴** (채널별 script 파일명 dispatch 아님). 지금 동작 2종뿐(수집+PDF / 수집만)이고 차이는 데이터(패턴 유무)지 로직 아님. 진짜 다른 로직(API기반·OCR 등) 나오면 그때 dispatch 도입. → YAGNI.

### 시간단위 배치 (일단위 아님)

- 수집 데이터는 이미 `posted_at_utc` 초 단위 저장 — 시간 정보 손실 없음.
- `crawl_date` 필터는 일단위지만, **시간단위 실행에 필터 추가 불필요**: `collect`은 post_id UNIQUE로 멱등, `download`은 파일 존재시 스킵. registry cron을 `0 * * * *`(매시)로 걸면 매시 `--date 오늘` 실행 → 새 글/PDF만 자동 수집. **fetcher 코드 무변경.**
- 시간범위 필터(예: "지난 1시간 새 글")가 진짜 필요한 곳은 요약/전송 단계 — fetcher가 아니라 DB 쿼리(`created_at`/최종 post_id 기준)로 푼다.
- 비용 천장: `crawl_date`는 매 실행마다 "오늘" 하루를 head부터 재크롤(멱등이나 페이지 재요청). companyreport ~100글/일 = 몇 페이지, 매시도 가벼움. 볼륨 커지면 "DB에 이미 있는 post_id 만나면 조기중단" 최적화 추가.

### 남은 작업 순서

1. `telegram_channels.json` 생성 (companyreport 실값 + butler_works 예시)
2. config 로더 함수 `load_channel_config(channel)` — 테스트 먼저(TDD)
3. `download_telegram_report_attachments.py` 상수 → config 읽기로 리팩터 (기존 테스트 유지)
4. 통합 러너 `run_telegram_channels.py --date`: config 순회 → 채널별 collect → attachments 있으면 download
5. ops registry에 job 추가 + `run-telegram-channels.ps1` + xml (기존 4 job 패턴 복제). 시간/간격은 여기 cron으로.

---

## 검증 명령 예시

프로젝트 루트 기준:

```bash
uv run python etl/scripts/collect_telegram_public.py --channel-url https://t.me/butler_works --date 2026-07-01
uv run python etl/scripts/export_telegram_public.py --channel butler_works --date 2026-07-01 --format json --out etl/exports/telegram/butler_works_2026-07-01.json
uv run pytest etl/tests/test_collect_telegram_public.py etl/tests/test_export_telegram_public.py
```

PowerShell registry 검증은 기존 README 기준:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Test-OpenClawBatchRegistry.ps1
```

---

## 주의사항

- 공개 채널 웹(`t.me/s/...`)에서 보이는 글만 수집한다.
- 비공개 채널, 로그인 필요 채널, 삭제된 글은 수집 대상이 아니다.
- Telegram HTML 구조가 바뀔 수 있으므로 parser 테스트를 둔다.
- KST 기준 날짜 필터를 지킨다.
- 원문 DB는 git에 커밋하지 않는다.
- 실제 bot token, chat id, webhook URL은 문서에 쓰지 않는다.
- 기존 ETF/키움 배치와 섞이지 않게 Telegram 공개 채널 배치는 독립 job으로 둔다.

---

## LLM 작업 지시

이 계획을 구현하는 LLM은 다음 원칙을 따른다.

1. 먼저 현재 repo의 `README.md`, `ops/batches/README.md`, 관련 `etl/scripts/*.py`를 읽고 기존 스타일을 맞춘다.
2. 새 기능은 최소 파일만 추가한다. 파서는 검증된 `C:/Users/mullu/AppData/Local/hermes/scripts/telegram_public_channel_scrape.py`를 포팅한다(새로 쓰지 않는다).
3. 수집 원문 저장과 요약/전송을 한 스크립트에 섞지 않는다.
4. 최초 검증 대상은 `butler_works` + `2026-07-01`. 검증 당시 45개였으나 삭제 글로 줄 수 있음 — 1단계의 완화된 성공 기준(40개 이상 + post_id 대조)을 따른다.
5. 테스트 없이 완료 처리하지 않는다.
6. secrets는 `.env`에서만 읽고 출력하지 않는다.
7. 구현 후 실제 명령을 실행해 결과를 보고한다.
