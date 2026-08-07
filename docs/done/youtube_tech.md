# 유튜브 채널 수집·요약 — 기술문서 (개발용)

> **상태**: 운영 설계 freeze (수집 최근만·요약 채널별 auto/manual·결과/대기 분리) · 구현 진행 (2026-07-09).
> **짝 문서**: [youtube_plan.md](./youtube_plan.md)
> LLM·인간 개발용. 파일경로·스키마·API 계약·함수 시그니처·**실패 테스트 명세**·단계 체크리스트.
> 원칙: **기존 자산 최대 재사용, 신규 최소**(ponytail). **스펙 주도 TDD** — 각 단계 테스트 먼저.
> plan과 충돌 시 plan의 제품 결정을 따르고, 구현 디테일은 이 문서를 고친 뒤 코드.

---

## 0. TDD 규칙 (이 작업 공통)

1. 단계 착수 시 **해당 단계의 테스트를 먼저 추가**하고 실패를 확인한다.
2. 최소 구현으로 통과시킨다. 다음 단계 코드·추상화를 미리 넣지 않는다.
3. 단계 완료 조건 = 아래 **Acceptance tests** 전부 녹 + 수동 확인 항목 체크.
4. 테스트 위치:
   - Python: `etl/tests/test_youtube_*.py` (unittest 또는 기존 telegram 스타일)
   - broker-web API: `broker-web/__tests__/youtube-channels-api.spec.ts` (Playwright request, telegram 복제)
   - UI e2e(선택): settings 탭 전환·추가 1건 — 1차 완료 후 여유 시
5. Python 실행: `etl` cwd, `uv run` / 프로젝트 관례. 맨몸 `python` 금지.
6. Windows cp949: stdout reconfigure / 테스트 내 한글 assert는 utf-8 전제.

---

## 1. 재사용 자산 (확인 완료)

| 필요 | 기존 자산 | 위치 | 복제 시 주의 |
|---|---|---|---|
| 채널 JSON lib | `telegram-channels.ts` | `broker-web/lib/telegram-channels.ts` | active + deleted 사이드카, `repoRoot()=cwd()/..` |
| 채널 API route | `/api/telegram-channels` | `broker-web/app/api/telegram-channels/route.ts` | GET/POST/PUT/DELETE, `fail()` 400 `{error}` |
| 채널 패널 UI | `TelegramPanel` | `broker-web/components/admin/telegram-panel.tsx` | dirty 일괄 저장, soft-delete, discovery 체크 |
| 설정 탭 셸 | `admin/settings/page.tsx` | `useState` 탭 2개 | 탭 프레임워크 금지, **3번째 탭만 추가** |
| py 채널 로더 | `telegram_channels.py` | `etl/scripts/telegram_channels.py` | `load_all` / `load_discovery` |
| py 로더 테스트 | `test_telegram_channels.py` | `etl/tests/` | tempfile json fixture |
| API 통합 테스트 | `telegram-channels-api.spec.ts` | 스냅샷 후 복원 | 실파일 touch → afterAll 복원 필수 |
| 수집 멱등 패턴 | `collect_telegram_public.py` | UNIQUE + upsert | date_kst, query_only 읽기 |
| 파이프라인 오케스트레이션 | `run_telegram_pipeline.py` | 단계별 subprocess | 수집 이후 단계에 참고 |
| LLM JSON | `new_etf_insight.llm.generate_json` | etl 패키지 | LangGraph 노드에서 호출 |
| LangGraph 패턴 | telegram / pdf langgraph | `etl/scripts/*_langgraph/` | prompts/*.md + schema json + StateGraph |
| 종목명→코드 | `parse_extract` 패턴 | telegram_analysis_langgraph | LLM은 이름만, 마스터로 코드 확정 |
| notify | `notify.py` | P5 선택 | 1~4차 호출 금지 |

**복제하지 말 것**: 텔레그램 HTML 스크래퍼, 세션(morning/close/evening) 키, Discord digest 배선(1~4차).

---

## 2. 단계 지도

| Phase | 이름 | 산출물 | 의존 | 상태 |
|---|---|---|---|---|
| **1** | 채널 관리 | json + py loader + ts lib + API + settings 탭 | 없음 | 완료 |
| **2** | 수집 | sqlite 스키마 + collect/run + skill | Phase 1 json | 완료 |
| **3** | 영상 LangGraph 요약 | 청크→통합 이슈 요약 저장 | Phase 2 transcript | **재설계 freeze · 구 1샷 폐기** |
| **4** | 종목 정리 (그래프 후단) | 통합 요약 기준 종목 추출·저장 | Phase 3 통합 요약 | **재설계 freeze · 구 대본직스캔 폐기** |
| **5** | 소비 | api 읽기 · 결과/대기 UI · 수동 수집·요약 | Phase 3~4 | 진행 |
| **6** | 스케줄 | 일 1회 01:00 수집(전 채널) + auto 채널 요약 | Phase 5 | 완료 (Windows Task) |

수집 3단(HTML resolve → RSS → transcript-api)은 §4.0 실측 **확정**.  
P3/P4 제품 결정은 §5·§6 **2026-07-09 freeze**.  
운영 정책은 **§13** (2026-07-09).

---

## 3. Phase 1 — 채널 관리 (완전 스펙)

### 3.1 Config 파일 (single source of truth)

| 파일 | 역할 |
|---|---|
| `etl/scripts/youtube_channels.json` | 활성 채널. **최상위 key = channel_id (`UC…`)** |
| `etl/scripts/youtube_channels.deleted.json` | 소프트삭제 보관. 동일 스키마. 없으면 `{}` 취급 |

**Entry 스키마** (JSON object value):

```json
{
  "UCxxxxxxxxxxxxxxxxxxxxxx": {
    "source_url": "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx",
    "handle": "@example",
    "label": "참고용 이름",
    "feed_role": "discovery_source"
  }
}
```

| 필드 | 필수 | 규칙 |
|---|---|---|
| (object key) | 예 | **`^UC[a-zA-Z0-9_-]{22}$`** (표준 channel_id 24자: `UC`+22). 이 regex가 유일한 검증 규칙 — 길이만 보지 말 것(charset 포함). |
| `source_url` | 예 | canonical: `https://www.youtube.com/channel/{channel_id}` |
| `handle` | 아니오 | `@` 포함 가능. 표시/디버그용. key 아님. |
| `label` | 아니오 | 없으면 UI에서 handle 또는 channel_id 표시 |
| `feed_role` | 아니오 | 값 `"discovery_source"` 만 의미 있음. 없으면 수집 전용 |

- 리더(py)는 **최상위 key 전부**를 활성 채널로 순회 → soft-delete 항목을 active 파일에 남기지 말 것 (텔레그램과 동일).
- 빈 active 파일 `{}` 허용 (채널 0개 = 수집 0).

**초기 커밋 내용**: `youtube_channels.json` = `{}` (또는 샘플 없이 빈 객체). 실채널은 UI/수동 추가.

### 3.2 신원 정규화 규칙

입력 문자열 → `channel_id` 해석. **동일 함수를 ts(추가 시)와 py(수집 시)에서 계약 공유.**

| 입력 예 | 결과 |
|---|---|
| `UCxxxxxxxxxxxxxxxxxxxxxx` (24자) | 그대로 key |
| `https://www.youtube.com/channel/UCxxx…` | path의 UC id |
| `https://www.youtube.com/@handle` | **resolve 필요** → UC id |
| `https://youtu.be/…` / `watch?v=` | **거부** (영상 URL, 채널 아님) |
| 빈 문자열 / 쓰레기 | 거부 |

**Phase 1 resolve 정책 (실측 반영 후 채택):**

- UI/API **추가 시점**에 channel_id를 확정해야 한다 (json key = UC).
- 로컬 파싱: `UC…` / `…/channel/UC…` → 즉시 key.
- `@handle` / `https://www.youtube.com/@handle`:
  - **방법 확정** (실측): GET 채널 페이지 HTML → **canonical 신호 우선**(`link rel=canonical` / `og:url`의 `/channel/UC…`) → 없을 때만 선두 `"channelId"` 폴백. 상세 규칙 §3.3 HTML 추출 패턴.
  - **P1 구현 채택**: API `add` 시 서버(Next route 또는 호출 lib)에서 **동일 HTML resolve 1회** 허용. 실패 시 400 한글 메시지.
  - 단위 테스트: `parse_channel_id`는 네트워크 없이 UC만. `resolve_channel_id`는 **fixture HTML**로 mock (live 네트워크 테스트 아님).
- `watch?v=` / `youtu.be` / `shorts/…` 영상 URL → **거부** (채널 아님).

→ 제품 문장: “채널 URL·`@handle`·`UC…` 추가 가능. 저장 key는 항상 `UC…`. 영상/Shorts 링크는 채널 추가에 쓰지 않음.”

**공용 test vector (py `parse_channel_id`·ts `extractChannelId` 둘 다 통과 필수 — drift 방지):**

| 입력 | 기대 |
|---|---|
| `UCeN2YeJcBCRJoXgzF_OU3qw` | 그대로 |
| `https://www.youtube.com/channel/UCeN2YeJcBCRJoXgzF_OU3qw` | `UCeN2YeJcBCRJoXgzF_OU3qw` |
| `https://www.youtube.com/@unrealtech` | throw/ValueError (로컬 파서 범위 밖, resolve 필요) |
| `https://youtu.be/F-UgZE6QZiQ` | throw/ValueError |
| `https://www.youtube.com/watch?v=F-UgZE6QZiQ` | throw/ValueError |
| `https://www.youtube.com/shorts/F-UgZE6QZiQ` | throw/ValueError |
| `` (빈문자열) | throw/ValueError |

같은 값을 A1~A4(py)와 B(ts) 테스트가 공유한다. 한쪽만 고치면 이 표와 어긋나므로 표를 기준으로 맞춘다.

### 3.3 Python 로더 — `etl/scripts/youtube_channels.py`

```python
DEFAULT_CONFIG: Path  # .../etl/scripts/youtube_channels.json

def load_all_channels(config_path: Path | str = DEFAULT_CONFIG) -> dict[str, dict]:
    """active json 전체. 파일 없으면 {} 반환.
    (텔레그램 로더는 FileNotFoundError지만, 여기선 신규 설치 친화로 의도적 divergence.)"""

def load_channel_config(channel_id: str, config_path: Path | str = DEFAULT_CONFIG) -> dict:
    """없으면 KeyError."""

def load_discovery_channels(config_path: Path | str = DEFAULT_CONFIG) -> dict[str, dict]:
    """feed_role == 'discovery_source' 만."""
```

`load_channel_config` 없는 id → `KeyError` (텔레그램 대칭).

헬퍼 (같은 파일 `youtube_channels.py` — 추가 모듈 금지):

```python
def parse_channel_id(raw: str) -> str:
    """UC /channel/UC 만(네트워크 없음). 실패 시 ValueError."""

def resolve_channel_id(raw: str, *, opener=urllib.request.urlopen) -> str:
    """parse 성공 시 그대로. @handle URL이면 GET HTML 후 channelId 추출.
    실패 시 ValueError. opener 주입으로 테스트 mock."""
```

HTML 추출 패턴 (실측 유효):

**주의**: `@handle` 페이지 HTML엔 추천/관련 채널의 `"channelId"`가 **여러 개** 섞여 있다.
"첫 매치"나 "아무 UC 24자"를 고르면 **엉뚱한 채널 UC를 저장**할 수 있다. 반드시
페이지 주인을 가리키는 **canonical 신호**를 우선 채택한다.

- **1순위 (canonical, 주인 확정)**:
  - `<link rel="canonical" href="https://www.youtube.com/channel/(UC[0-9A-Za-z_-]{22})">`
  - 또는 `<meta property="og:url" content=".../channel/(UC[0-9A-Za-z_-]{22})">`
  - 둘 중 먼저 매치되는 UC.
- **2순위 (canonical 없을 때만 폴백)**: `"channelId":"(UC[^\"]+)"` — 단, HTML **선두**(예: 첫 등장 or `<head>` 범위) 것만. 문서 뒷부분 추천 채널 블록 매치는 버린다.
- 모든 후보는 `UC` + 길이 24 형식 검증 통과 필수.
- canonical/og:url 둘 다 없고 선두 channelId도 없으면 `ValueError` (조용히 오답 저장 금지).

### 3.4 broker-web lib — `broker-web/lib/youtube-channels.ts`

텔레그램 lib 대칭 API:

```ts
export type Channel = {
  key: string;       // channel_id UC…
  url: string;       // source_url
  label: string;
  handle?: string;
  discovery: boolean;
};

export const ACTIVE_PATH = () => path.join(repoRoot(), "etl", "scripts", "youtube_channels.json");
export const DELETED_PATH = () => path.join(repoRoot(), "etl", "scripts", "youtube_channels.deleted.json");

export function extractChannelId(input: string): string; // 로컬 UC 파싱만. throw Error
/** @handle 이면 fetch 채널 HTML → UC (P1 add 경로). 테스트는 mock fetch. */
export async function resolveChannelId(input: string, fetchImpl?: typeof fetch): Promise<string>;
export function listChannels(): Promise<{ active: Channel[]; deleted: Channel[] }>;
export function addChannel(input: { url: string; label?: string; handle?: string; discovery: boolean }): Promise<string>;
export function editChannel(input: { key: string; label?: string; handle?: string; discovery: boolean }): Promise<void>;
export function softDelete(key: string): Promise<void>;
export function restoreChannel(key: string): Promise<void>;
```

규칙 (텔레그램 동일):

- 이미 active → throw `이미 있는 채널: {key}`
- deleted에 있음 → throw `삭제목록에 있음. 복구하세요: {key}`
- softDelete: active→deleted 이동
- restore: 반대
- `channelToEntry`: discovery true일 때만 `feed_role: "discovery_source"` 기록
- label === key 이면 label 필드 생략 가능 (텔레그램 패턴)

### 3.5 API — `broker-web/app/api/youtube-channels/route.ts`

| Method | Body | 성공 | 실패 |
|---|---|---|---|
| `GET` | — | `{ active: Channel[], deleted: Channel[] }` 200 | — |
| `POST` | `{ action:"add", url, label?, handle?, discovery }` | listChannels() 200 | 400 `{ error }` |
| `POST` | `{ action:"restore", key }` | 200 list | 400 |
| `PUT` | `{ key, label?, handle?, discovery }` | 200 list | 400 |
| `DELETE` | `{ key }` | 200 list | 400 |

- 인증 없음 (로컬 전제, 텔레그램과 동일).
- `fail(err)` 메시지 = `Error.message` 그대로 (한글).

### 3.6 UI

| 경로/파일 | 내용 |
|---|---|
| `broker-web/components/admin/youtube-panel.tsx` | `TelegramPanel` UX 복제. 플레이스홀더: `채널 URL · @handle · UC…` |
| `broker-web/app/admin/settings/page.tsx` | `Tab = "telegram" \| "closebet" \| "youtube"`, 탭 버튼 **유튜브** |

UI 카피:

- 제목: `유튜브 채널 관리`
- 설명: `수집 대상 유튜브 채널 목록.`
- discovery 설명 박스: 텔레그램 문구를 유튜브 맥락으로 치환  
  (“원문 스캔” → “영상 대본·요약 스캔”, insights 테이블명은 2차 이후라 **‘이후 종목탐색 배치’** 로 표현)

표시 컬럼:

| 컬럼 | 내용 |
|---|---|
| 채널이름 | label Input |
| URL | source_url 링크 (새 탭) |
| ID | key 모노스페이스 축소 표시 (읽기 전용) |
| 종목탐색 소스 | Checkbox |
| 삭제 | Button |

nav 변경 없음 (`설정` 유지).

### 3.7 Phase 1 Acceptance tests (TDD)

#### A. `etl/tests/test_youtube_channels.py`

| # | 테스트 | expect |
|---|---|---|
| A1 | `parse_channel_id` 길이 24 `UC…` | 동일 반환 |
| A2 | `parse_channel_id("https://www.youtube.com/channel/UC…")` | UC 추출 |
| A3 | `parse_channel_id("https://www.youtube.com/@foo")` | `ValueError` (로컬 파서 범위 밖) |
| A4 | `parse_channel_id` watch/shorts URL | `ValueError` |
| A5 | `resolve_channel_id("@x", opener=mock)` HTML fixture에 canonical UC | canonical UC 반환 |
| A5b | fixture에 canonical UC + **뒤쪽 추천채널 decoy `"channelId"`** | canonical UC 반환 (decoy 아님) |
| A5c | canonical/og:url 없고 channelId도 없는 HTML | `ValueError` |
| A6 | tempfile json load_all / load_channel / missing KeyError | 텔레그램 대칭 |
| A7 | load_discovery_channels | discovery만 |
| A8 | DEFAULT_CONFIG 존재 (빈 `{}`) | name == youtube_channels.json |

#### B. `broker-web/__tests__/youtube-channels-api.spec.ts`

텔레그램 스펙 복제. **실파일 스냅샷 복원 필수.**

| # | 시나리오 | expect |
|---|---|---|
| B1 | GET | 200, `active`/`deleted` 배열 |
| B2 | POST add 유효 UC URL + label + discovery | 200, active에 key |
| B3 | 중복 add | 400 |
| B4 | PUT label/discovery | 반영 |
| B5 | DELETE | active 제거, deleted 포함 |
| B6 | restore | active 복귀 |
| B7 | POST add watch/shorts 영상 URL | 400 |
| B8 | (선택) POST add `@handle` — mock 또는 스킵; live resolve는 수동 | 200 + UC key 또는 문서화 스킵 |

테스트용 channel_id: `UCe2etestChannelId_zzzz1` 처럼 **실존 불필요·형식만 맞는 24자**(`UC`+22, regex `^UC[a-zA-Z0-9_-]{22}$` 통과 확인).

#### C. 수동

- [ ] `/admin/settings` → 유튜브 탭 전환
- [ ] 추가 → 목록 반영 → `youtube_channels.json` 파일 내용 육안
- [ ] 삭제 → deleted 파일 / 복구

### 3.8 Phase 1 체크리스트

> 전체 단계 롤업은 §10. 여기는 그 P1 항목의 세부.

- [x] **1a** `youtube_channels.json` (`{}`) + `youtube_channels.py` + **A 테스트 먼저** → 통과
- [x] **1b** `lib/youtube-channels.ts` + API route + **B 테스트 먼저** → 통과
- [x] **1c** `YoutubePanel` + settings 3탭 (코드 완료). 수동 C는 사용자 확인
- [x] plan/tech 상태 줄 갱신, 커밋은 사용자

---

## 4. Phase 2 — 수집 (계약 스펙 · 실측 채택)

### 4.-1 대본 확보 우선순위 (2026-07-14 개정 — STT 폴백 도입)

> **배경**: 실측 결과 수집 영상의 ~79%가 `youtube-transcript-api`로 대본을
> 못 얻음(자막 트랙 없음 또는 YouTube 차단). 요약 불가 영상이 다수 →
> **키 없는 우회를 최우선**으로 하되, 막히면 **Gemini STT 폴백**으로 대본을 확보한다.

**우선순위 (요약 전 선행, 위→아래 순):**

1. **키 없이 우회 (최우선)**: `youtube-transcript-api` (자막, 무료·키 불필요). §4.1.1 `fetch_transcript`.
2. **막힌 경우만 — Gemini STT**: yt-dlp 오디오(mp3) → Gemini 전사. 키(`GEMINI_API_KEY`) 필요. `youtube_stt.py`.
3. **둘 다 실패**: 대본 없음 → 요약 skip.

- STT는 **요약 대상 영상에만** 발동(수집 전건 아님) → 비용/시간 안전. LangGraph load 단계에서 폴백(§5.1a).
- STT로 얻은 대본은 `youtube_videos.transcript`에 캐시(`transcript_source='stt:<model>'`) → 재-STT 금지.
- 모델: `gemini-flash-latest`(현행 GA 별칭; `gemini-2.5-flash`는 신규 사용자 404). AI Studio 무료티어(15 RPM · 1,500/일).

### 4.0 실측 스모크 (2026-07-09, 확정)

| 항목 | 결과 |
|---|---|
| 타겟 | `https://www.youtube.com/@unrealtech` |
| channel_id | `UCeN2YeJcBCRJoXgzF_OU3qw` (채널 HTML `"channelId"`) |
| RSS | `…/feeds/videos.xml?channel_id=UCeN2YeJcBCRJoXgzF_OU3qw` → **15 entries** |
| 48h 필터 | `published` UTC 기준 당시 **5건** within |
| 최신 영상 | `F-UgZE6QZiQ` · 제목「도시가 물에 잠기지 않게 하는 방법」· Shorts URL · `2026-07-09T03:30:23+00:00` |
| 대본 | `youtube-transcript-api` · available `ko generated=True` · **642자** plain text 성공 |
| 대본 앞부분 샘플 | `숲은 비가 내려도 대부분을 품어…` (자동생성 문장 경계 줄바꿈 포함) |
| 사용 안 함 | YouTube Data API key, yt-dlp, STT |

**채택 파이프라인 (구현이 이 순서·이 도구를 벗어날 때 tech 개정 필요):**

```text
raw channel ref
  → resolve_channel_id  (stdlib urllib + regex HTML)     # @handle 시
  → list_channel_videos_rss (stdlib urllib + ElementTree) # channel_id
  → filter by date_kst / published window
  → fetch_transcript (youtube-transcript-api)             # per video_id
  → upsert youtube_videos
```

### 4.1 의존·도구 (키 없음) — 확정

| 용도 | **채택 (1순위)** | 비채택/나중 |
|---|---|---|
| `@handle` → `UC…` | 채널 페이지 HTML + regex (`channelId`) | Data API |
| 업로드 목록·메타 | **RSS** `https://www.youtube.com/feeds/videos.xml?channel_id={id}` | yt-dlp (RSS 15건 초과 backfill 시 재검토) |
| 대본 (1순위) | **`youtube-transcript-api`** (`YouTubeTranscriptApi().fetch` / `list`) | — |
| 대본 폴백 (2순위) | **yt-dlp 오디오 → Gemini 전사** (`youtube_stt.py`) | STT 로컬 whisper(비채택: 품질↓·CPU) |
| HTTP | `urllib.request` + `User-Agent: Mozilla/5.0 …` | — |
| XML | `xml.etree.ElementTree` | — |

- **YouTube Data API key / OAuth 사용 금지** (plan). Gemini STT는 별개(`GEMINI_API_KEY`)로 대본 폴백 전용.
- 의존성: `etl/pyproject.toml` — `youtube-transcript-api`(1.2.x), `yt-dlp`, `google-genai`. ffmpeg 시스템 설치 필요(mp3 변환).
- yt-dlp: **대본 폴백(오디오 추출) 용도로만** 허용. 목록/backfill 수집엔 여전히 미사용(RSS 유지).

### 4.1.1 함수 계약 (수집 코어 — `collect_youtube.py` 권장)

```python
def fetch_url(url: str, *, timeout: int = 40, opener=...) -> bytes: ...

def list_channel_videos_rss(channel_id: str, *, opener=...) -> list[dict]:
    """각 dict: video_id, title, published_at_utc (ISO), url
    Atom ns: yt videoId, atom title/published/link.
    순서: RSS 등장 순(보통 최신 먼저). 실측 상한 ~15.
    """

def fetch_transcript(video_id: str) -> tuple[str | None, str | None, str | None]:
    """(text, lang, source) source in {'manual','auto', None}.
    언어 우선: ko → en → list() 첫 성공.
    실패/없음: (None, None, None) — 예외로 채널 전체를 죽이지 않음.
    plain text = snippet text 를 '\\n'.join (타임코드 저장 안 함).
    """

def published_to_date_kst(published_at_utc: str) -> str:
    """YYYY-MM-DD Asia/Seoul."""
```

RSS 네임스페이스 (실측):

- `http://www.w3.org/2005/Atom` (`entry`, `title`, `published`, `link`)
- `http://www.youtube.com/xml/schemas/2015` (`videoId`)

URL 저장: RSS `link@href` 그대로 허용 (`/watch?v=` 또는 `/shorts/{id}`). canonical 강제 불필요.

### 4.2 DB

**경로**: `etl/db/youtube_public.sqlite3` (git-ignore 관례, telegram_public과 동일)  
**env** (api 연동 시): `YOUTUBE_DB_PATH` 예약. Phase 2 배치는 기본 경로 상수.

채널 목록은 **JSON 단일 소스**(`youtube_channels.json`, plan §7). sqlite에 채널 테이블 두지 않음 —
수집기는 JSON 읽고 `youtube_videos`만 쓴다. (title/handle 캐시가 api용으로 필요해지면 Phase 5에서 추가.)

```sql
CREATE TABLE IF NOT EXISTS youtube_videos (
  channel_id       TEXT NOT NULL,
  video_id         TEXT NOT NULL,  -- 11자
  title            TEXT NOT NULL,
  published_at_utc TEXT NOT NULL,  -- ISO8601
  date_kst         TEXT NOT NULL,  -- YYYY-MM-DD (published 기준 KST)
  url              TEXT NOT NULL,  -- RSS link 그대로 (watch 또는 shorts)
  transcript       TEXT,           -- 전체 대본 plain text. 없으면 NULL
  transcript_lang  TEXT,           -- 언어코드만: ko, en (auto여부는 transcript_source에)
  transcript_source TEXT,          -- manual | auto | null
  raw_json         TEXT,           -- RSS entry 메타 원본 dict 덤프(디버그/재처리용). 대본 아님
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  UNIQUE(channel_id, video_id)
);

CREATE INDEX IF NOT EXISTS idx_youtube_videos_date
  ON youtube_videos(channel_id, date_kst);
```

**멱등**: `UNIQUE(channel_id, video_id)` upsert. 재실행 시 transcript/title 갱신 허용.

### 4.3 CLI

```text
# cwd=etl
uv run python scripts/run_youtube_channels.py --date 2026-07-09
uv run python scripts/run_youtube_channels.py --date 2026-07-09 --channel UCxxxx…
uv run python scripts/collect_youtube.py --channel-id UCxxxx… --date 2026-07-09
```

| 스크립트 | 책임 |
|---|---|
| `youtube_channels.py` | config only (Phase 1) |
| `collect_youtube.py` | 단일 채널: 목록→필터 date_kst→메타+대본→upsert |
| `run_youtube_channels.py` | active 전체(또는 `--channel`) 순회, 채널 실패 시 정책 아래 |

**실패 정책**: 한 채널 실패해도 다음 채널 계속. 마지막에 실패 목록 로그. **exit code: 1건 이상 실패 시 exit 1** (텔레그램 세션 전체 실패와 유사하게 알림 가능).

**자막 없음**: `transcript=NULL`, 카운터 `skipped_no_transcript += 1`. 예외로 전체 실패 만들지 않음.

**대본 언어 우선순위 (채택)**: `fetch(..., languages=['ko'])` 시도 → `['en']` → `list()` 순회 첫 성공.  
`is_generated`면 `transcript_source='auto'`, 아니면 `'manual'`. 전부 실패 → NULL.

**길이**: 대본 전체 저장 (컷 없음). LLM 단계는 입력 시 청킹/컷.

**RSS 한도**: 피드가 주는 최근 N개만 (실측 15). `--date`가 피드 밖이면 **0건이 정상** (에러 아님). 로그에 `rss_entries` / `matched_date` 카운트.

### 4.4 date_kst

- 영상 `published_at` UTC → Asia/Seoul 날짜.
- `--date` 와 `date_kst` 일치 행만 **신규 수집 대상**.  
  (이미 DB에 다른 날짜로 있는 id는 스킵하거나 upsert — upsert 채택.)

### 4.5 Phase 2 Acceptance tests

| # | 테스트 | expect |
|---|---|---|
| D1 | schema ensure 후 tables 존재 | youtube_videos 등 |
| D2 | upsert 동일 video_id 2회 | row count 1, 최신 title |
| D3 | date 필터: 다른 날짜 published 제외 | 0 insert |
| D4 | `fetch_transcript` mock snippets → `"\\n".join` plain text | 본문만, 타임코드 컬럼 없음 |
| D5 | mock RSS XML fixture로 list + collect 1채널 | N rows, UNIQUE 유지 |
| D6 | 자막 실패 mock | transcript NULL, 채널 수집 exit 성공 |
| D7 | RSS fixture에 shorts 링크 | url 저장, video_id 정상 |

네트워크 **live** 테스트는 기본 스위트 **제외**. 회귀 수동:

```text
# 참고 커맨드 (구현 후). 타겟 실측과 동일 채널.
uv run python scripts/run_youtube_channels.py --date <KST오늘> --channel UCeN2YeJcBCRJoXgzF_OU3qw
```

### 4.6 Phase 2 체크리스트

- [x] 의존성·경로 실측 (§4.0) — HTML + RSS + youtube-transcript-api
- [x] `youtube-transcript-api`를 etl 의존성에 추가 (1.2.4)
- [x] D 테스트 먼저 → collect/run 구현 → 통과
- [x] skill `skills/new-etf-insight-youtube-collect/SKILL.md` (방법론 3단 명시)
- [ ] README 데이터 저장소 표 1줄 추가 (사용자 확인 후)

### 4.7 실측 체크리스트 (완료)

- [x] RSS 최근 개수 → **15** (`@unrealtech`)
- [x] 자동생성 한글 자막 → 해당 채널 최신 Shorts **있음**
- [x] transcript 라이브러리 채택 → **youtube-transcript-api** (yt-dlp 불필요)
- [x] API key 불필요 확인
- [ ] 다채널 rate limit / 403 빈도 — 운영 중 관찰

---

## 5. Phase 3+4 — 영상 LangGraph 요약·종목 (**freeze** · 2026-07-09)

> **폐기(구현 시 삭제)**:  
> - `summarize_youtube_videos.py` (1샷 요약) + `youtube_video_summary_schema.json` (구 스키마) + `test_summarize_youtube_videos.py`  
> - `discover_youtube_stock_candidates.py` (대본 직접 규칙 스캔) + 구 F 테스트의 “대본 직스캔” 가정  
> 구 `youtube_video_summaries` 1샷 스키마·구 discover 경로는 **쓰지 않는다**. 테이블명은 재사용 가능하나 **JSON 계약은 아래 freeze로 교체**.

### 5.0 한 줄

**영상 1건 = LangGraph 1 run.**  
긴 대본을 나눠 요약 → 합쳐 이슈 정리(저장) → **그 다음에** 종목 추출(저장).  
텔레그램 DB와 물리 통합 없음. 조회 시 `date_kst + ticker`로 같이 봄(P5).

### 5.1 파이프라인

```text
load video (title, transcript [+ snippet timestamps if available])
  → [transcript 없으면 STT 폴백으로 확보·캐시 → 그래도 없으면 skip]  # §5.1a
  → chunk          # ① 타임코드 10분 우선, 없으면 글자 수
  → map summarize  # ② 청크마다 페이지 요약 (중간 결과; DB 필수 저장 아님)
  → reduce issues  # ③ 페이지들 통합: 중복 제거 + 이슈 판별 → youtube_video_summaries 저장
  → extract stocks # ④ 통합 결과만 입력, 텔레그램식 LLM 종목명 → 마스터 코드 → youtube_stock_insights 저장
  → END
```

| 단계 | 담당 | LLM? | 저장 |
|---|---|---|---|
| load | 파이썬 | 아니오 | — |
| chunk | 파이썬 | 아니오 | 아니오 (state only) |
| map summarize | LLM × N | 예 | 아니오 (기본). 디버그용 남기면 선택 |
| reduce issues | LLM × 1 | 예 | **예** `youtube_video_summaries` |
| extract stocks | LLM 이름 + 파이썬 마스터 | 예(이름) | **예** `youtube_stock_insights` |

### 5.1a load 단계 STT 폴백 (2026-07-14)

- `node_load`가 DB `transcript`를 읽는다.
- 비었으면(NULL/공백): **STT 폴백**(`youtube_stt.fetch_transcript_stt`) 1회 시도 → 성공 시
  `youtube_videos.transcript` upsert(캐시) 후 요약 계속.
- STT 실패(키 없음/다운로드 실패/빈 결과): `skip=True`, `warnings += ['no_transcript']`.
- STT는 요약 대상(= 이 그래프에 진입한 영상)에만 발동 → 비용은 요약 트리거에 종속.
- `run_youtube_analysis.list_videos`의 `transcript IS NOT NULL` 필터는 **STT 폴백 활성 시 완화**
  (없는 영상도 그래프에 넣어야 폴백이 돈다). §7 참고.

### 5.2 청킹 규칙

1. **1순위**: 자막 구간 시작 시각(초) 기준 **10분(600s) 윈도우**로 자름.  
   - 수집 시 snippet 타임코드가 없거나 plain text만 있으면 → 2순위.
2. **2순위**: 글자 수. 권장 상수 `CHUNK_CHARS ≈ 4000` (구현 시 한 값으로 freeze, 테스트 fixture 고정).  
3. 빈 청크 없음. 마지막 조각은 짧아도 1청크.
4. 각 청크 메타: `chunk_index`, `start_sec`/`end_sec`(가능 시), `text`.

### 5.3 실행 단위·러너

- **그래프 run 키**: `(channel_id, video_id)`.
- 날짜 배치: 바깥 루프가 `date_kst`로 영상 목록을 돌며 **영상마다** 그래프 1회.
- `transcript IS NULL` / 빈 문자열 → 그래프 스킵 (LLM 0).
- 세션(morning/close/evening) **없음**. 워터마크 불필요 — 멱등은 아래 §5.6.

### 5.4 저장 테이블 (youtube_public.sqlite3)

#### A. 통합 이슈 요약 (영상 1행)

```sql
CREATE TABLE IF NOT EXISTS youtube_video_summaries (
  channel_id   TEXT NOT NULL,
  video_id     TEXT NOT NULL,
  date_kst     TEXT NOT NULL,
  model        TEXT,
  summary_json TEXT NOT NULL,  -- §5.5 reduce 스키마
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  UNIQUE(channel_id, video_id)
);
```

#### B. 종목별 정리 (날짜+종목, 여러 영상 누적 가능)

```sql
CREATE TABLE IF NOT EXISTS youtube_stock_insights (
  date_kst TEXT NOT NULL,
  ticker TEXT NOT NULL,
  name TEXT NOT NULL,
  mention_channels TEXT NOT NULL,   -- JSON list of channel_id
  source_video_ids TEXT NOT NULL,   -- JSON list of video_id
  discovery_reason TEXT NOT NULL,
  analysis TEXT,                   -- 종목 맥락 요약(통합 결과 기반). 없으면 NULL
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(date_kst, ticker)
);
```

- upsert 시 **analysis를 불필요하게 지우지 않는 규칙**은 텔레그램과 동일 정신 유지.  
  (P4 후단이 analysis를 채우는 주체면 ON CONFLICT 시 analysis 갱신 허용 — 구현 시 “채우는 노드만 갱신”으로 통일.)

### 5.5 JSON 계약 (구현 전 파일로 고정)

위치 예정: `etl/scripts/youtube_langgraph/`

| 파일 | 용도 |
|---|---|
| `prompts/chunk_summary.md` | 청크(페이지) 요약 |
| `prompts/reduce_issues.md` | 페이지 통합·중복제거·이슈 |
| `prompts/stock_extract.md` | 통합 결과에서 중요 종목 (텔레그램 stock_extract 톤) |
| `chunk_summary_schema.json` | map 출력 (state용) |
| `reduce_issues_schema.json` | **DB summary_json** |
| `stock_extract_schema.json` | stocks: `[{name, note}]` — 코드 없음 |

**reduce_issues_schema (summary_json) 초안 — freeze 방향:**

```json
{
  "headline": "영상 한 줄",
  "issues": [
    {"title": "이슈 제목", "summary": "설명", "time_hint": "선택: 10:00~ 또는 null"}
  ],
  "bullets": ["중복 제거된 핵심 포인트"],
  "risk_or_caveat": "과장·광고성 한 줄 또는 null"
}
```

**chunk_summary_schema 초안 (미저장 가능):**

```json
{
  "chunk_index": 0,
  "headline": "이 구간 한 줄",
  "bullets": ["…"]
}
```

**stock_extract**: 텔레그램과 같이 `name` + `note`만. 코드는 파이썬 마스터.

### 5.6 종목 추출 (텔레그램식)

1. 입력 = **reduce 결과 텍스트**(headline/issues/bullets 직렬화). 원문 대본 전량 스캔 **금지**.
2. LLM → 중요 종목명 목록.
3. `load_name_to_code`로 코드 확정. 마스터 없으면 버림 + warning.
4. 파이썬이 `mention_channels` / `source_video_ids` / `discovery_reason` 집계.
5. discovery 채널만 돌릴지: **영상 요약 그래프는 수집된 전 영상 가능**.  
   종목 추출 노드만 `feed_role=discovery_source` 채널로 제한 (채택).

### 5.7 멱등

- `youtube_video_summaries`에 행 있고 `--force` 없으면 **해당 영상 그래프 스킵**.
- `--force` 시 map+reduce+stock 재실행 후 upsert.
- 같은 `date_kst+ticker`에 여러 영상 → `source_video_ids` 병합 upsert.

### 5.8 코드 배치·CLI

```text
etl/scripts/youtube_langgraph/
  youtube_analysis_langgraph.py   # StateGraph
  prompts/
  *_schema.json

# 영상 1건
uv run python scripts/youtube_langgraph/youtube_analysis_langgraph.py \
  --channel-id UCxxx --video-id xxxxxxxxxxx [--force]

# 날짜 일괄 (루프 래퍼; 그래프 바깥)
uv run python scripts/run_youtube_analysis.py --date 2026-07-09 [--force]
```

- LLM: `generate_json` only. `ETF_LLM_PROVIDER` (기본 codex; `chatgpt_oauth`면 gpt-5.5).
- 패턴: telegram/pdf와 동일 — `make_*_prompt` / `call_*` / `parse_*` / `persist`.

### 5.9 Acceptance tests (교체 예정)

구 E/F 스위트는 **이 계약으로 교체**. 방향:

| # | 테스트 | expect |
|---|---|---|
| G1 | transcript NULL → LLM 0, 저장 0 | skip |
| G2 | 타임코드 fixture → 10분 경계 청크 수 | chunk N |
| G3 | 타임코드 없음 → 글자 수 청크 | fallback |
| G4 | map mock N회 + reduce mock 1회 → summaries 1행 | issues 스키마 |
| G5 | reduce 후 stock extract: 마스터 없는 이름 버림 | warning |
| G6 | 기존 summary + force 없음 → LLM 0 | skip |
| G7 | force → 재요약·stock upsert | 갱신 |
| G8 | 비-discovery 채널 → stock 노드 스킵(또는 0 candidates) | 정책 준수 |

테스트: `etl/tests/test_youtube_analysis_langgraph.py` (mock `generate_json`).

### 5.10 Phase 3+4 체크리스트

- [x] 구 P3/P4 스크립트·테스트·구 스키마 파일 **삭제**
- [x] `youtube_langgraph/` 스캐폴드 + 프롬프트/스키마
- [x] G 테스트 먼저 → 그래프 구현 → 통과
- [x] `run_youtube_analysis.py` 날짜 루프
- [ ] skill/문서 CLI 경로 갱신 (수집 skill과 분리 가능)

---

## 6. (구 Phase 4 단독 discover — **폐기**)

> §5로 흡수됨. 대본 직접 규칙 스캔 경로는 사용 금지.

---

## 7. Phase 5 — 소비 (조회·알림·스케줄)

| 항목 | 상태 | 메모 |
|---|---|---|
| `api/routers/youtube.py` | **완료** | `YOUTUBE_DB_PATH`, query_only |
| `GET /youtube/mentions/{ticker}` | **완료** | from/to, session 없음 |
| `GET /youtube/summaries?date=` | **완료** | 영상 통합 요약 + title 조인 |
| 종목 허브 탭 `youtube` | **완료** | 텔레그램 탭 옆, ticker 조회 |
| **텔레그램 join** | 부분 | DB 분리 유지. 같은 종목 허브에서 탭으로 각각 조회 (한 카드 합침 UI는 미함) |
| Discord digest | 미착수 | 선택 |
| Windows Task | 미착수 | 수집 후 `run_youtube_analysis` 하루 1회 |

테스트: `api/test_youtube_api.py`

---

## 8. 보안·운영

- broker-web fs write: 로컬 전용 (PLAN_JSON_ADMIN과 동일 제약).
- 수집 User-Agent/부하: PAGE_DELAY 류 상수. 공격적 병렬 금지.
- 대본·요약에 개인정보 거의 없으나 sqlite는 git 제외.
- API 키를 문서/코드에 추가하는 변경은 plan 위반 → PR 거부.

---

## 9. 디렉터리·파일 총괄

```text
etl/scripts/youtube_channels.json
etl/scripts/youtube_channels.deleted.json   # runtime
etl/scripts/youtube_channels.py             # P1
etl/scripts/collect_youtube.py              # P2
etl/scripts/run_youtube_channels.py         # P2
etl/scripts/youtube_langgraph/              # P3+4 (신)
  youtube_analysis_langgraph.py
  prompts/
  *_schema.json
etl/scripts/run_youtube_analysis.py         # P3+4 날짜 루프
etl/scripts/youtube_stt.py                  # 대본 STT 폴백 (yt-dlp+Gemini) — 2026-07-14
etl/scripts/youtube_stock_insights.py       # 테이블 헬퍼 (유지·스키마 §5.4에 맞춤)
etl/db/youtube_public.sqlite3               # runtime
etl/tests/test_youtube_channels.py          # P1
etl/tests/test_collect_youtube.py           # P2
etl/tests/test_youtube_analysis_langgraph.py  # P3+4
broker-web/lib/youtube-channels.ts          # P1
broker-web/app/api/youtube-channels/route.ts
broker-web/components/admin/youtube-panel.tsx
broker-web/app/admin/settings/page.tsx
broker-web/__tests__/youtube-channels-api.spec.ts
skills/new-etf-insight-youtube-collect/SKILL.md  # P2
docs/youtube_plan.md
docs/youtube_tech.md

# 삭제 예정 (구 P3/P4)
etl/scripts/summarize_youtube_videos.py
etl/scripts/youtube_video_summary_schema.json
etl/scripts/discover_youtube_stock_candidates.py
etl/tests/test_summarize_youtube_videos.py
etl/tests/test_discover_youtube_stock_candidates.py
```

---

## 10. 단계별 체크리스트 (요약)

- [x] **P1a** py parse/loader + A tests
- [x] **P1b** ts lib + API + B tests
- [x] **P1c** YoutubePanel + settings 탭 (수동 UI 확인만 남음)
- [x] **P2** DB+collect+run + D tests + skill
- [x] **P3/P4 재설계 freeze** (본 문서 §5, 2026-07-09)
- [x] **P3+4 구현** 구코드 삭제 + LangGraph + G tests
- [x] **P5a** api 읽기 + 종목 허브 유튜브 탭
- [ ] **P5b** digest / Task 스케줄 (선택)

---

## 11. 결정 로그 (설계)

| 날짜 | 결정 |
|---|---|
| 2026-07-09 | P1 채널관리, P2 RSS+transcript-api |
| 2026-07-09 | 구 P3=1샷 generate_json, 구 P4=대본 규칙 discover (이후 폐기) |
| 2026-07-09 | **P3+4 LangGraph**: 10분 타임코드 청크(폴백 글자수) → map 요약 → reduce 이슈 저장 → 종목은 **후단** 텔레그램식. 영상 1건=1 run. 구 P3/P4 삭제. DB 분리, 조회 join만 |
| 2026-07-14 | **STT 폴백 도입**: 대본 확보 우선순위 = ① youtube-transcript-api(키 없음, 최우선) → ② Gemini STT(yt-dlp 오디오, 막힌 경우만). 요약은 대본 확보 후 진행. LangGraph load에 STT 폴백 노드 삽입. `youtube_stt.py` 신규. 실측: 79% 무자막, Gemini 전사 품질 양호(11.6k자/편, ~40s). daily-youtube 배치는 검토 위해 잠정 Disabled |

### 착수 시 확인 (과거)

- [x] settings 탭 useState 3탭
- [x] telegram API 실파일 복원 패턴
- [x] `@handle` HTML resolve
- [x] Phase 2 RSS + youtube-transcript-api
- [x] youtube-transcript-api pyproject 고정 (1.2.4)
- [x] 청킹: 타임코드 우선 / 글자 수 폴백
- [x] 종목: 오케스트레이션 **이후**, 텔레그램식
- [x] A: 구 P3/P4 지우고 LangGraph
- [x] B: DB 분리 + 화면/API join

---

## 12. 범위 밖 (구현 금지 목록)

- ~~STT~~ → **범위 내** (2026-07-14, §4.-1 대본 폴백. Gemini STT 전용)
- YouTube Data API key / OAuth
- **장기 backfill** (1년 전 등 RSS 밖 소급 수집) — 당분간 금지
- ~~Phase 2에서 yt-dlp 도입~~ → yt-dlp는 **대본 폴백 오디오 추출 용도로만** 허용(2026-07-14). 목록/backfill 수집엔 여전히 금지
- `/admin/youtube` 단독 페이지 (settings 탭으로 충분; 목록은 `/youtube`)
- **텔레그램 DB에 유튜브 테이블 혼재** (join은 조회 계층만)
- 원문 대본 전량 종목 직스캔 (구 P4 방식 부활 금지)
- 1샷 전체 대본 요약 (구 P3 방식 부활 금지)
- Phase 1에서 LLM·수집 코드 동시 추가
- 인증/멀티유저

---

## 13. 운영 설계 freeze (2026-07-09)

### 13.0 한 줄

최근 RSS(~15)만 적립·소급 없음. 수집은 저비용이라 자동+수동.  
요약은 **영상 단위**, 채널별 `summary_mode=auto|manual`.  
**수동 요약은 모든 채널 가능**. 화면은 **결과(완료)** / **대기(미요약)** 분리.

### 13.1 채널 JSON 필드

```json
{
  "UCxxxxxxxxxxxxxxxxxxxxxx": {
    "source_url": "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx",
    "handle": "@example",
    "label": "이름",
    "feed_role": "discovery_source",
    "summary_mode": "manual"
  }
}
```

| 필드 | 값 | 기본 |
|---|---|---|
| `summary_mode` | `auto` \| `manual` | **`manual`** (LLM 비용 안전) |

- `auto`: 스케줄/일배치가 미요약 자동 처리 + 수동도 가능  
- `manual`: 자동 요약 안 함, **수동만** (전 채널 수동 가능 원칙과 합치: auto 채널도 수동 가능)

### 13.2 목록 이원화

> UI 라벨은 [done/youtube_ui_plan.md](./done/youtube_ui_plan.md) 확정을 따른다: **목록** 탭 폐기(작업 영역 흡수), **결과 → 요약완료**로 개명. 아래 소스 정의는 동일.

| 탭(UI 라벨) | 소스 | 의미 |
|---|---|---|
| 요약완료 | `youtube_video_summaries` | 요약 **완료** (있음 = 끝) |
| 대기 | `youtube_videos` LEFT JOIN summaries IS NULL, transcript 있음 | **미요약** (수동/자동 대상) |

### 13.3 API (쓰기·대기)

| Method | Path | Body/Query | 역할 |
|---|---|---|---|
| GET | `/youtube/pending` | from, to, channel? | 미요약 목록 |
| POST | `/youtube/collect` | `{ from, to, channel_ids? }` | RSS+대본 수집 (LLM 없음) |
| POST | `/youtube/collect-url` | `{ url }` | **URL 1건** 수집. video_id 파싱 → watch HTML(channelId/title/published) → 대본 → upsert. 응답 `{ video_id, channel_id, date_kst, title, status }`, `status ∈ inserted\|updated\|already_summarized`. 채널/@handle URL 거부(400) |
| POST | `/youtube/summarize` | `{ from, to, channel_ids?, force?, video_ids? }` | 미요약(또는 지정) LangGraph. **모든 채널 수동 가능**. 배치 계약 유지, UI는 단건만 노출 |

- `collect-url`: 기존 `collect`(채널·기간)·`collect-selected`(catalog dict 필요)로 임의 URL 1건 불가 → 신규. 프록시 `broker-web/app/api/youtube-collect-url/route.ts`는 `youtube-collect/route.ts` 복제.
- 자동 스케줄: `ops/scheduled-tasks/run-youtube-daily.ps1` 매일 **01:00 KST**.
  - 대상일 = **어제**(D-1). 수집 = 등록 채널 전부. 요약 = `summary_mode=auto` 미요약만 (`--lookback-days 2`).
  - 문서: `ops/batches/daily-youtube.md` · Task: `\new-etf_insight\daily-youtube`
- 통합 시장 브리핑: `ops/scheduled-tasks/run-youtube-digest-session.ps1` 매일 **10:00 / 18:00 KST**.
  - 영상별 요약(`youtube_video_summaries`)을 읽어 이슈별 통합 → `youtube_digest.py` LLM 1회 → Discord.
  - 입력 선별 = **미전송 + cutoff 48시간 이내**. 전송 성공 후에만 `youtube_digest_deliveries` 기록.
  - 문서: `ops/batches/youtube-twice-daily-digest.md` · Task: `\new-etf_insight\daily-youtube-digest-{morning,evening}`

### 13.4 UI /youtube

> 화면 흐름·탭·필터 상세는 [done/youtube_ui_plan.md](./done/youtube_ui_plan.md)가 단일 소스. 아래는 요지.

- 필터: from/to + 채널 멀티 → **조회(표) 전용** (기본 최근 1개월)
- 탭: **요약완료** | **대기** (구 `목록` 탭 폐기 → 작업 영역 흡수)
- 작업 분리:
  - **최근 수집**: 기본 어제~오늘 + 선택 채널(없으면 등록 전체). RSS 최근분만
  - **주소로 가져오기**: URL 1건 (`collect-url`). 성공 시 응답 date_kst로 조회 필터 자동 확장 후 대기 갱신
  - **고급**: 체크 시 수집도 조회 필터 기간 사용
  - **대기 요약**: 조회 필터 기간의 미요약만, **1건씩**(백그라운드 잡 완료 후 요약완료 이동). 전 채널 수동 가능
  - **자막없음 행**: 기본 숨김/접기(요약 영구 불가)
- 영상길이(대본 추정) 삭제: 요약 응답 `duration_sec`/`transcript_chars` 제거(catalog 공식 duration은 유지)
- 설정: 채널별 요약모드 + 종목탐색

### 13.5 일자

- 조회 필터 키 = date_kst (게시 KST), 기본 최근 1개월
- 수집 기본 키 = 어제~오늘 date_kst (조회 필터와 분리)
- DB에 있는 과거 구간 조회 OK; **없는 과거 채우기 안 함**
