# 유튜브 요약 UI — 기획

> **상태**: DONE — 빌드 1·2·3 완료 (2026-07-12)
> **상위**: [youtube_plan.md](../youtube_plan.md) · 스펙: [youtube_tech.md](../youtube_tech.md)
> 이 문서는 **`/youtube` 화면 흐름·탭·가져오기·필터**만. 스키마·TR·LangGraph 상세는 tech.

## 목적

등록 채널 영상을 **가져와서 DB에 쌓고**, 대본 있는 건 **한 건씩 요약**한 뒤,
완료분만 **기간·채널로 조회**한다.

한 줄: **가져오기 → 대기(DB) → 요약 → 요약완료 조회**.

## 사용자 시나리오

1. (설정) 유튜브 채널 등록 — 기존 `/admin/settings` 유튜브 탭
2. **가져오기** — 아래 둘 중 하나(또는 배치)로 영상+대본을 DB에 넣음 → **대기**에 보임
3. **대기**에서 대본 있고 미요약인 행을 클릭 → **요약** 실행
4. 요약이 끝나면 **요약완료** 탭으로 이동해 내용 확인
5. 요약완료에서 **기간·채널 필터**로 과거 요약 조회

## 가져오기 (둘 다 DB 저장 → 대기)

| 방식 | 트리거 | 동작 | 구현 |
|---|---|---|---|
| **최근 수집** | 배치 또는 UI 버튼 | 등록 채널 RSS **최근 ~15개** + 대본 | 있음 (`/youtube/collect`) |
| **주소로 가져오기** | 사용자가 영상 URL 입력 | 해당 1건 메타+대본 수집 후 DB | 있음 (`POST /youtube/collect-url`) |

### 주소로 가져오기 — API 계약 (신규)

기존 `/youtube/collect`(채널·기간)·`/youtube/collect-selected`(catalog에서 온 video dict 필요)로는
**임의 URL 1건**을 못 넣는다(채널/제목/게시일 미상). 그래서 신규 1개 추가.

| Method | Path | Body | 응답 |
|---|---|---|---|
| POST | `/youtube/collect-url` | `{ "url": "<watch/shorts/youtu.be URL>" }` | `{ video_id, channel_id, date_kst, title, status }` |

- 백엔드 동작: URL → `video_id` 파싱 → watch 페이지 HTML에서 `channelId`·`title`·`published` 추출 → 대본 fetch → `youtube_videos` upsert. (channel URL·`@handle`은 거부 = 영상 아님, 400 한글.)
- `status` ∈ `inserted` | `updated` | `already_summarized`
  - `already_summarized`: 해당 video_id에 `youtube_video_summaries` 행 이미 있음 → 대기 아닌 **요약완료**에 있음(§미결 반영)
- 프록시: `broker-web/app/api/youtube-collect-url/route.ts` — 기존 `youtube-collect/route.ts` 그대로 복제(FastAPI 프록시).

공통 규칙:

- 저장 대상: `youtube_videos` (요약 전)
- 가져온 직후 UI 위치: **대기** (단, 필터 노출 규칙 아래)
- 이 단계에서는 LLM 요약 안 함
- STT 없음 (유튜브 자막 API만). 자막 없으면 대본 NULL → 대기에는 남되 요약 불가

**가져온 영상 필터 노출 규칙 (설계 구멍 방지):**

- 대기 목록은 조회 기간(`from`/`to`, date_kst)으로 필터됨.
- URL로 가져온 영상의 `date_kst`가 현재 조회기간 밖이면 **대기에 안 보임** → 사용자 혼란.
- 처리: 가져오기 성공 응답의 `date_kst`로 **조회 from/to를 그 날짜 포함하게 자동 확장**(또는 그 날짜로 점프) 후 대기 갱신.
- `status=already_summarized`면 "이미 요약됨 — 요약완료 탭 확인" 토스트, 탭 이동은 사용자 선택.

## 탭 구조 (2개)

| 탭 | 이름 | 의미 | 데이터 |
|---|---|---|---|
| 1 | **대기** | 아직 요약 안 한 영상 | DB · 요약 행 없음 |
| 2 | **요약완료** | 요약이 끝난 영상 조회 | DB · `youtube_video_summaries` 있음 |

### 제거·흡수

- 옛 **목록** 탭(RSS 카탈로그 전용 화면): 탭으로 두지 않음.  
  가져오기는 상단 **작업 영역**(최근 수집 / URL 입력)으로 흡수.
- 옛 **결과** 이름: **요약완료**로 교체 (조회 전용 의미).
- **영상길이** 표시: 삭제 (대본 글자 수 추정은 의미 없음). 실제 손댈 곳:
  - `api/routers/youtube.py` — `_duration_from_transcript`(chars//3) 및 호출부, `YoutubeVideoSummary.duration_sec`/`transcript_chars` 채우는 라인
  - `api/schemas.py` — `YoutubeVideoSummary.duration_sec`/`transcript_chars` 필드 (catalog의 `duration_sec`은 공식값이라 **catalog는 유지**)
  - `broker-web/components/youtube/summary-table.tsx` — `영상길이` th(약 36) · `formatDuration(v.duration_sec)`(65) · 추정 안내문(131)
  - catalog-panel의 `duration_sec`은 catalog(RSS 공식 lengthSeconds) 기반 → **요약 표만 제거**, catalog 표는 논외

### 대기

- 행 상태:
  - **미요약** — 대본 있음 → 요약 가능
  - **자막없음** — 대본 없음 → 요약 불가
- **미요약 행 클릭** → 해당 1건 선택 → **요약** 버튼 활성화
- 상단 고정형 “선택 요약(1건)” 문구/UX는 단순화 (행 선택 → 요약)
- **자막없음 행 누적 방지**: 기본은 **접기/숨김**(요약 영구 불가라 목록 오염). "자막없음도 보기" 토글로만 표시.
- **요약 이동 타이밍**: 요약은 백그라운드 잡(`/youtube/summarize-job` + 폴링). 잡 **시작**만으로 이동하면 요약완료 탭에 행이 아직 없다.
  - 확정: 잡 시작 → 진행 표시(대기 유지) → **완료 폴링 성공 후** 요약완료 탭으로 이동.
- 일괄 요약 없음 (1건씩). **API는 배치(`video_ids[]`/기간) 유지, UI만 단건 노출.**

### 요약완료

- 이미 요약된 것만 표시 (펼침: headline / issues / bullets / risk 등)
- **채널·기간 필터가 여기서 핵심** — 조회 조건 변경 시 이 목록에 반영
- 읽기 전용 (재요약은 force 등 후순위; 이번 범위 밖)

## 필터

조회 필터와 수집 옵션은 **다른 것** — 표 분리(혼동 방지).

**조회 필터** (페이지 상단, 탭 목록에 반영):

| 필터 | 대기 | 요약완료 |
|---|---|---|
| 기간 (`from`/`to`) | 적용 (일관 조회) | **필수 적용** |
| 채널 | 적용 | **필수 적용** |

**수집 옵션** (가져오기 작업 영역 전용, 조회 필터와 무관):

| 옵션 | 최근 수집 | 주소로 가져오기 |
|---|---|---|
| 기간 | 기본 어제~오늘 (조회 필터와 분리; 고급 토글로 조회기간 재사용 가능) | 없음 (URL 1건) |
| 채널 | 선택 채널만 (없으면 등록 전체) | 없음 (URL이 채널 결정) |

- 페이지 상단 날짜·채널 = **조회 조건** (표에 뭐 보일지)
- 최근 수집 기본 기간(어제~오늘) = **수집 옵션** (뭘 새로 넣을지). 문구로 명확히 구분.

## 확정된 결정

- 흐름: 가져오기 → 대기 → 요약 → 요약완료
- 탭 2개: `대기` / `요약완료`
- URL 직접 입력 가져오기 추가 (1건, DB 적재)
- 요약은 대기에서 미요약 1건 선택 후 실행
- 영상길이(추정) UI·응답 필드 삭제
- 목록(RSS 카탈로그) 탭 폐기 → 작업 영역으로 흡수
- 일괄 요약·STT 없음

## 빌드 순서

1. **정리 UI**
   - 영상길이 컬럼·추정 제거
   - 탭 라벨 `대기` / `요약완료`
   - 페이지 부제·빈 상태 문구를 위 흐름에 맞게 수정
   - 대기: 행 선택 → 요약 버튼 UX 단순화
2. **주소로 가져오기**
   - API/etl: `POST /youtube/collect-url` — video URL → video_id + watch HTML(channelId/title/published) → 대본 → `youtube_videos` upsert. 프록시 route 1개 복제.
   - UI: 작업 영역에 URL 입력 + 가져오기
   - 성공 시: 응답 `date_kst`로 조회 필터 자동 확장 → 대기 갱신. `already_summarized`면 요약완료 안내.
3. **필터·이동 다듬기**
   - 요약완료(및 대기) 기간·채널 반영 확인
   - 요약 완료 후 요약완료 탭 이동 유지

단계마다 결과 보고 → 확인 후 다음.

## 의도적 비범위 (이번)

- 대기+요약완료 단일 테이블 통합
- 청크(map) 중간 요약 DB 저장·UI
- 일괄 요약 / STT
- discovery 종목 추출 UX 개편 (백엔드 기존 경로 유지)
- Discord digest

## 미결 / 나중

- 요약 실패 행 재시도 UI (force)
- catalog용 공식 duration을 대기 표시에 쓸지 여부 (필요 시 수집 시점에 저장)

(해결됨) 가져오기 시 이미 요약된 video_id → `collect-url` 응답 `status=already_summarized`로 처리(요약완료 안내). §가져오기 계약 참조.
