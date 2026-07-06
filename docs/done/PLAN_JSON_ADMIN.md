# JSON 파일 편집 페이지 (broker-web admin)

## 한 줄 요약
broker-web에 admin 페이지 1개 추가 — 화이트리스트에 등록된 **서버 외부 관리 JSON 3개**를 raw 텍스트로 편집·검증·저장. 라이브러리 無(네이티브 `<textarea>` + `JSON.parse`), 파일은 제자리(이동 X), 키 기반 접근으로 경로 탈출 차단.

---

## 배경

| 현재 | 목표 |
|------|------|
| 관리 JSON을 에디터/터미널로 직접 편집 | 웹에서 목록·편집·검증·저장 |
| 파일이 3개 앱에 산재(etl, ops), 상대경로 하드코딩 리더 | 제자리 유지, UI만 broker-web에 집중 |
| 저장 실수 시 깨진 JSON 유입 위험 | 저장 전 `JSON.parse` 검증, 실패 거부 |

선행 완료: 고아 `stock_data.json` 삭제(커밋 `e9feafc`) → broker-web 소유 관리JSON 0개 → "서버 내/외" 페이지 분리 불필요, **단일 페이지**.

---

## 결정사항 (확정)

| # | 항목 | 결정 |
|---|------|------|
| 1 | 편집 방식 | raw JSON `<textarea>`. monaco/json-editor 라이브러리 안 씀(아쉬우면 나중) |
| 2 | 저장 검증 | 클라 + 서버 양쪽 `JSON.parse`. 실패 시 400, 파일 안 건드림 |
| 3 | 저장 내용 | 사용자 raw 텍스트 **그대로** 기록(재직렬화 X → 포맷·키순서·들여쓰기 보존) |
| 4 | 파일 지정 | 화이트리스트 레지스트리의 **key**로만. 클라가 경로 전달 X → traversal 원천 차단 |
| 5 | 경로 해석 | repo root = `path.join(process.cwd(), "..")` 기준 절대경로. dev 서버가 `../etl`, `../ops`에 fs 쓰기 |
| 6 | 백업 | 저장 시 `<파일>.bak` 1개 덮어쓰기(직전 버전). registry 등 위험 파일 롤백용 |
| 7 | 위험 표시 | 레지스트리에 `risk` 필드(안전/주의). registry=주의, UI에 경고 배지 |
| 8 | 인증 | 없음(로컬 관리 도구). 프로덕션 노출 시 별도 과제 — 지금 범위 밖 |

---

## 대상 파일 (화이트리스트)

| key | 경로(repo root 기준) | 역할 | risk |
|---|---|---|---|
| `holding_aliases` | `etl/data/holding_aliases.json` | 종목명↔티커 별칭 매핑 | 안전 |
| `telegram_channels` | `etl/scripts/telegram_channels.json` | 텔레그램 채널 목록 + feed_role | 안전 |
| `cron_registry` | `ops/batches/openclaw-cron.registry.json` | 배치 잡 레지스트리(cron/windowsTask) | **주의** |

---

## 구현 (TDD, 단계별 — 각 단계 후 테스트 결과 보고·확인 후 진행)

### STEP 1 — 레지스트리 + 경로 유틸
- `broker-web/lib/json-admin.ts`
  - `MANAGED_FILES: {key,label,relPath,risk}[]` 배열(위 3개)
  - `resolveManaged(key)` → key 검증 후 절대경로 반환. 미등록 key = throw
- 테스트: `resolveManaged("cron_registry")` 경로 확인, 미등록 key throw, `../` 주입 무의미(key 화이트리스트라)
- **검증**: `vitest run` (해당 파일만)

### STEP 2 — API route
- `broker-web/app/api/json-files/route.ts` (기존 route 패턴 준수: `NextRequest`/`NextResponse`, try/catch)
  - `GET` (no key) → 파일 목록(key,label,risk) 반환
  - `GET ?key=` → `{content: string, risk}` 원문 읽기(`fs/promises.readFile` utf-8)
  - `PUT` `{key, content}` → ① key 검증 ② `JSON.parse(content)` 실패 시 400 ③ `.bak` 저장 ④ 원문 write
- 테스트: 임시 파일로 read→PUT(정상)→재read 일치, 잘못된 JSON PUT→400·파일 불변, 미등록 key→400
- **검증**: `vitest run`

### STEP 3 — admin 페이지
- `broker-web/app/admin/json/page.tsx` (client)
  - 좌: 파일 목록(risk 배지), 우: `<textarea>` + 저장 버튼
  - 로드 시 `GET ?key=`, 저장 시 `PUT`. 클라에서 먼저 `JSON.parse` 검증 → 에러 인라인
  - registry 선택 시 "주의: 배치 스케줄, 잘못 저장 시 배치 실패" 경고
- `components/common/nav.tsx` `links[]`에 `{href:"/admin/json", label:"설정"}` 추가
- **검증**: `next dev`(--webpack, dev fork-bomb 회피) + Playwright e2e — 목록 표시, 로드, 잘못된 JSON 저장 거부, 정상 저장·재로드 일치

### STEP 4 — 정리
- PLAN done 이동(`docs/done/`), 커밋

---

## 보안 / 주의

- **경로 탈출**: 클라는 key만 전송. 서버가 화이트리스트→절대경로 매핑. 임의 경로 쓰기 불가(결정 #4)
- **repo 밖 쓰기**: dev 서버가 `../etl`, `../ops`에 write. 로컬 관리 전용 전제. **프로덕션/컨테이너 배포 시 경로 깨짐** — 배포 대상 아님(결정 #8)
- **동시성**: 단일 사용자 로컬 도구, 락 안 검. 다중 사용자 되면 그때(ponytail 상한)
- **registry**: `jobs[]` 구조 깨지면 배치 실패. `.bak` 롤백 + UI 경고로 완화

---

## 검증 요약
- STEP1~2: `vitest run` (broker-web)
- STEP3: `next dev --webpack` + Playwright e2e (목록·로드·저장거부·저장성공 4케이스)
- 신규 의존성 0, 파일 이동 0, 기존 리더 영향 0
