# 유튜브 채널 수집·요약 — 기획

> **상태**: 수집·요약 UI·API 완료 · digest/Task 선택 남음 (2026-07-12)
> **짝 문서**: 개발 상세·스펙·TDD는 [youtube_tech.md](./youtube_tech.md) · UI 흐름 DONE: [done/youtube_ui_plan.md](./done/youtube_ui_plan.md)
> 이 문서는 **목적·화면·기능·흐름·결정**만. 코드/경로/스키마는 기술문서로.

## 목적

텔레그램 공개채널과 같이, **유튜브 채널을 등록·관리**하고 해당 채널 영상을
**대본 단위로 수집한 뒤 LLM으로 요약**한다. 최종 목표는 텔레그램과 같은
**종목 시그널(크로스채널 insights)** 까지지만, 유튜브는 단위가 포스트가 아니라
**영상+대본**이라 단계적으로 올린다.

한 줄: **채널 관리 → 영상 대본 수집 → 영상 요약 → (이후) 종목 시그널**.

## 텔레그램과의 관계

| 축 | 텔레그램 (현행) | 유튜브 (이번) |
|---|---|---|
| 관리 UI | `/admin/settings` 텔레그램 탭 | 같은 설정 페이지에 **유튜브 탭** 추가 |
| 목록 소스 | `telegram_channels.json` | `youtube_channels.json` (별도 파일) |
| 원문 단위 | 채널 포스트 | 영상 1개 (+ 대본) |
| 원문 확보 | t.me 공개 미리보기 스크랩 | **API 키 없이** HTML resolve + RSS + 자막 API (아래 방법론) |
| 분석 1차 | 크로스채널 종목 추출·세션 digest | **영상 단위 요약** 먼저 |
| 분석 2차 | (이미 있음) | 종목 후보·insights·(선택) digest |
| DB | `telegram_public.sqlite3` | `youtube_public.sqlite3` (분리) |

합치지 않는 이유: 원문 스키마·수집 주기·실패 모드가 다름. 종목 허브 등 소비 측에서
나중에 소스 배지로 병렬 조회하면 된다.

## 사용자 시나리오

1. 설정 → 유튜브 탭에서 채널 URL/`@handle` 붙여넣기 → 등록
2. (배치) 등록 채널의 신규 영상을 찾아 대본을 저장
3. (배치) 대본이 있는 영상을 LLM으로 요약 저장
4. (이후) discovery 채널 대본·요약에서 종목 언급을 뽑아 텔레그램식 insights
5. (이후, 선택) Discord 등 알림 / api 읽기 / 종목 허브 연동

**1차 사용자 터치포인트는 4의 이전이 아니라 1(채널 관리)까지.**  
수집·요약은 배치/CLI로 돌리고, 조회 UI·알림은 후순위.

## 화면 (1차)

### `/admin/settings` — 탭 3개

| 탭 | 관리대상 | UI 성격 |
|---|---|---|
| 텔레그램 | 기존 그대로 | 기존 |
| 종가베팅 | 기존 그대로 | 기존 |
| **유튜브** | 수집 대상 유튜브 채널 목록 + 종목탐색 소스 | 텔레그램 탭과 **같은 UX** |

### 유튜브 탭 — 기능

- **목록**: 활성 채널 (참고용 이름, 채널 URL, channel_id, 종목탐색 체크)
- **추가**: URL 또는 `@handle` 붙여넣기 + 이름(선택) + 종목탐색 체크
- **수정**: 이름·종목탐색 여부 (신원 key는 불변)
- **삭제**: 소프트 삭제 (복구 가능, 삭제 목록 토글)
- **저장**: dirty 행 일괄 저장 (텔레그램 패널과 동일 패턴)

### 종목탐색 소스 (feed_role) 의미

- **체크** → 이후 종목 시그널 단계에서 이 채널 대본/요약을 스캔해 종목 후보 발굴
- **해제** → 원문·영상 요약 수집만 (리포트성·매크로 채널 등)
- 1차(채널 관리·수집·영상 요약)에서는 플래그만 저장. 추출 로직은 2차.

## 수집 방법론 (실측 확정, 2026-07-09)

대상 스모크: `https://www.youtube.com/@unrealtech`  
→ **API 키·yt-dlp 없이** 아래 3단으로 최근 업로드 목록 + 최신 영상 대본까지 확인.

| 단계 | 하는 일 | 수단 | 실측 |
|---|---|---|---|
| 1. 채널 해석 | `@handle` / 채널 URL → 안정 `channel_id` (`UC…`) | 채널 페이지 HTML에서 `"channelId":"UC…"` 등 파싱 | `@unrealtech` → `UCeN2YeJcBCRJoXgzF_OU3qw` |
| 2. 업로드 목록 | 최근 영상 id·제목·게시시각·링크 | 공개 RSS `https://www.youtube.com/feeds/videos.xml?channel_id={id}` | 15건 수신, `published`로 48h 필터 가능 (당시 5건) |
| 3. 대본 | 영상 1건 plain text | `youtube-transcript-api` (수동/자동 자막, **STT 아님**) | 최신 Shorts `F-UgZE6QZiQ` · `ko` 자동생성 · 642자 수집 성공 |

추가 관찰:

- **Shorts = 일반 영상과 동일 취급** (video_id 동일, RSS·대본 경로 동일). 별도 Shorts 파이프라인 없음.
- RSS는 **최근 소량(실측 15)** 만 줌 → 일배치·최근 lookback에는 충분, 장기 backfill은 나중(대안: yt-dlp 등).
- 자막 없으면 대본 실패 가능 → 메타만 저장·요약 스킵 (기존 결정 유지).
- Google YouTube Data API **불필요** (이 경로로 대체).

제품 흐름에 넣는 순서: **등록된 channel_id 기준 RSS → date 필터 → 건별 transcript → DB**.

## 데이터·배치 흐름 (전체 목표)

```text
youtube_channels.json (관리 UI가 쓰기, key=UC…)
        │
        ▼
 수집 배치 (date_kst / lookback 기준)
  1) (필요 시) @handle → UC : 채널 HTML 파싱
  2) RSS로 업로드 목록 (video_id, title, published, url)
  3) 게시시각 필터 (KST date 또는 최근 N시간)
  4) youtube-transcript-api 로 대본 (ko 우선). 없으면 transcript=NULL
        │
        ▼
 youtube_public.sqlite3 / youtube_videos
        │
        ▼
 요약 배치 (대본 있는 영상, 멱등)
  - LLM 영상 단위 요약
        │
        ▼
 youtube_video_summaries (tech 확정)
        │
        ▼ (2차)
 종목 시그널 (discovery 채널만)
  - 텔레그램 insights와 유사한 ticker 단위 저장
  - (선택) Discord digest / api 읽기
```

## 확정된 결정

1. **목표 범위 = C, 단계적**  
   - 1차: 채널 관리  
   - 2차: 대본 수집  
   - 3차: 영상 LLM 요약  
   - 4차: 종목 시그널 (+ 선택 알림/api)

2. **원문 = 대본 전부 → LLM 요약**  
   - 유튜브 자막(수동/자동생성)을 `youtube-transcript-api`로 가져온다.  
   - **STT(음성→글)는 범위 밖.** 자막 없는 영상은 메타만 두고 대본/요약 스킵.

3. **YouTube Data API 키 없음**  
   - 공식 API·OAuth 안 씀.  
   - **채택 스택**: 채널 HTML resolve + **RSS 목록** + **youtube-transcript-api** (실측).  
   - yt-dlp는 1차 스택 아님 (RSS 한계 backfill 시에만 재검토).

4. **1차 UI = 채널 관리만**  
   - 영상 목록·요약 조회 페이지·종목 허브 연동은 나중.

5. **관리 진입점**  
   - 새 톱레벨 페이지(`/admin/youtube`) 만들지 않음.  
   - 기존 `/admin/settings`에 **유튜브 탭** 추가 (설정 한곳 유지).

6. **채널 신원 key**  
   - 안정 id = **`channel_id` (`UC…`)**  
   - `@handle` → UC 는 **채널 페이지 HTML 파싱**으로 가능(실측). 저장 key는 항상 UC.  
   - handle은 캐시 필드. key로 쓰지 않음.

7. **저장소 분리**  
   - `etl/db/youtube_public.sqlite3`  
   - 텔레그램 DB에 테이블 얹지 않음.

8. **스케줄**  
   - 유튜브 영상 빈도 낮음 → **하루 1회** 배치 가정 (텔레그램 3세션 복제 안 함).  
   - 시각·Task 등록은 수집 배치 안정화 후.

9. **알림**  
   - 1~3차에서 Discord 등 전송 없음. 4차 선택.

10. **보안 경계**  
    - 채널 JSON 쓰기 = broker-web 로컬 fs (텔레그램과 동일, 로컬 전용).  
    - 읽기 api는 추후 `api(:8000)` 패턴. 수집/요약은 `etl` 배치.

11. **Shorts**  
    - 제외하지 않음. RSS·대본 경로가 일반 영상과 같음 (실측).

## 비목표 (이번 시리즈에서 안 함 — 명시)

- STT로 무자막 영상 받아쓰기
- YouTube Data API 키·OAuth 연동
- 실시간/웹훅 수집
- Shorts 전용 파이프라인 (일반 업로드와 동일 취급, 제외 필터 없음 — 필요 시 나중)
- 멀티유저·인증
- 텔레그램 insights와 DB 스키마 통합

## 빌드 순서 (단계마다 결과 보고 → 확인 후 다음)

스펙 주도 TDD: **각 단계 = 실패 테스트 먼저 → 구현 → 통과 → 보고**.

1. **채널 config + 로더 + admin 탭** — JSON single source, CRUD, 설정 탭
2. **수집 배치** — 채널별 신규 영상 메타 + 대본 upsert (키 없음)
3. **영상 LLM 요약** — 대본 있는 행만, 멱등
4. **종목 시그널 (discovery)** — 텔레그램 패턴 축소판
5. **(선택)** api 읽기 · digest · 스케줄 Task · 종목 허브 소스 배지

## 성공 기준

### 1차 (채널 관리) — 여기까지가 “페이지/데이터 추가”의 첫 완료선
- 설정 화면에서 유튜브 채널 추가·수정·소프트삭제·복구
- 목록 파일이 etl 수집이 읽을 **유일한 채널 소스**
- 잘못된 URL/중복/삭제목록 충돌 시 저장 거부 + 한글 에러

### 2차 (수집)
- `--date` 기준 해당일 게시 영상 upsert, 같은 video_id 재실행 시 중복 없음
- 자막 있으면 대본 저장, 없으면 NULL + 스킵 카운트 로그

### 3차 (요약)
- 대본 있는 영상만 요약 저장, 재실행 시 불필요 재호출 최소화(멱등/워터마크 규칙 tech)

### 4차 (시그널)
- discovery 채널에서 종목 후보·간단 분석 저장 (텔레그램과 소비자 계약은 별도 협의)

## 미해결 / 나중

- Phase 1 UI에서 `@handle` 추가 시 **네트워크 resolve를 넣을지**, UC/`/channel/UC` 로컬만 받을지 — 해석 **방법**은 HTML로 확정, **언제 돌릴지**만 tech/착수 시 선택
- 요약 스키마 필드 확정 (한줄/불릿/언급종목 후보 등) — 3차 착수 전 tech freeze
- 종목 시그널을 텔레그램 `telegram_stock_insights`와 **합쳐 보여줄지** 별도 테이블로 둘지
- 조회 UI / Discord / 종목 허브 탭 연동
- RSS 15건 넘는 장기 backfill 수단
- 자막 없는 영상 STT 옵션

## 결정 로그

| 날짜 | 결정 |
|---|---|
| 2026-07-09 | 목표 C, 대본→LLM, 1차 UI=채널관리, API 키 없음, settings 탭, DB 분리, STT 제외 |
| 2026-07-09 | 수집 스택 실측 채택: HTML channelId resolve + RSS 목록 + youtube-transcript-api. 타겟 `@unrealtech`, 48h 내 5건, 최신 Shorts 대본(ko auto) OK. yt-dlp/Data API 1차 제외 |
