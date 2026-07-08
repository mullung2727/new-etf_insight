# 설정 관리 페이지 — 기술문서 (개발용)

> **상태**: 구현 완료 (2026-07-08). **짝 문서**: 방향/화면은 [settings_admin_plan.md](./settings_admin_plan.md)
> LLM 개발용. 파일경로·API·스키마·로더·단계 체크리스트. 착수 직전 갱신.
> 원칙: **기존 자산 최대 재사용, 신규 최소**(ponytail). TDD 단계별.

## 재사용 자산 (확인 완료)

| 필요 | 기존 자산 | 위치 |
|---|---|---|
| 파일기반 config lib 패턴 | `telegram-channels.ts` (read/write/validate + `repoRoot()=cwd()/..`) | `broker-web/lib/telegram-channels.ts` |
| config API route 패턴 | `/api/telegram-channels` (GET/PUT/POST/DELETE, try/catch, `{error}` 400) | `broker-web/app/api/telegram-channels/` |
| 채널 편집 UI | 기존 텔레그램 페이지 전체 | `broker-web/app/admin/telegram/page.tsx` |
| 폼 요소 | `Button`/`Input`/`Checkbox`/`Badge` | `@/components/ui/*` |
| 배치 config 로드 지점 | `run_close_bet.py` argparse + `_BUDGET_BY_COUNT`, `run_close_bet_exit.py` `--tp/--sl` | 아래 |

## config 파일 (신규, 단일 소스)

**경로**: `etl/scripts/close_bet.json` (기존 `telegram_channels.json`과 같은 디렉터리 — 신규 디렉터리 없음)
```json
{
  "score_threshold": 50,
  "tp": 0.05,
  "sl": 0.03,
  "budget_by_count": { "1": 3000000, "2": 2000000, "3": 1666666 }
}
```
- 매수 배치(`run_close_bet.py`)와 청산 워커(`run_close_bet_exit.py`)가 **같은 파일**을 읽음.
- `budget_by_count` 키는 문자열(JSON 제약) → py에서 int 변환.

## 신규 코드 (이것만 새로 씀)

### 1. py 로더 — `etl/scripts/close_bet_config.py`
- `load() -> dict`: 파일 읽기 → **범위검증**(threshold 0~100 int, tp/sl 0~1 float, budget 양수 int) → 실패 시 `ValueError`(배치 abort, 깨진 값으로 주문 금지).
- 파일 없거나 키 누락 시 **하드코딩 기본값** 폴백(현재값과 동일). 즉 config 없어도 기존 동작 보존.
- 셀프체크: `if __name__=="__main__"` 에 정상/범위초과/음수예산 assert.

### 2. 배치 py 배선 (기존 파일 수정, 최소 diff)
- `run_close_bet.py`
  - `--score-threshold` default `70` → **`None`**. `None`이면 `close_bet_config.load()["score_threshold"]` 사용(CLI 주면 override 유지).
  - `_BUDGET_BY_COUNT` 상수 → `load()["budget_by_count"]`(int 변환) 참조.
- `run_close_bet_exit.py`: `--tp`/`--sl` default → 동일 패턴(`None`→config).
- `report_close_bet_order.py`: `--score-threshold` default → config (리포트 표시 정합).

### 3. ps1 정리 (하드코딩 인자 제거)
- `run-close-bet-order.ps1`: `--score-threshold 50` **줄 삭제**(config가 소스).
- `run-close-bet-exit.ps1`: `--tp 0.05` `--sl 0.03` **삭제**.
- `run-close-bet-order-report.ps1`: 이전에 추가한 `--score-threshold 50` **삭제**.
- 남기는 인자: `--dry-run false`(안전 스위치, 웹 제외), `--broker-url`, 시각 인자(전략 고정).
- **건드리지 않는 파일**: `run_close_bet_force_exit.py` / `run-close-bet-force-exit.ps1`(15:19:30 전량청산 백스톱). tp/sl 등 전략 노브 없음 → config·웹 대상 아님. 정리 단계에서 손대지 말 것.

### 4. broker-web config lib — `broker-web/lib/close-bet-config.ts`
- `telegram-channels.ts` 패턴 복제: `PATH()=repoRoot()/etl/scripts/close_bet.json`, `read()`, `write()`, `validate(cfg)`(범위검증, 실패 throw).
- `read()`는 파일 없으면 기본값 반환(py 로더와 동일 기본값).

### 5. API — `broker-web/app/api/close-bet-config/route.ts`
- `GET` → `{score_threshold, tp, sl, budget_by_count}`
- `PUT {…}` → `validate()` 실패 시 400·파일 불변, 성공 시 `.bak` 저장 후 write.

### 6. 페이지 — `/admin/settings` (탭)
- `broker-web/app/admin/settings/page.tsx` (client)
  - `const [tab, setTab] = useState<"telegram"|"closebet">("telegram")` — 탭 프레임워크 無.
  - 탭바(버튼 2개) + 조건부 렌더.
- **텔레그램 탭**: 기존 `admin/telegram/page.tsx` 본문을 컴포넌트로 추출해 이식(로직 그대로).
- **종가베팅 탭**: 아래 **필드 UI 카피 표**대로 라벨·도움말·단위 표기(비개발 사용자 전제) + 클라 범위검증(에러 인라인) + 저장(PUT).

#### 필드 UI 카피 (비개발 사용자용 — 개발 키명 노출 금지)

| json 키 | 화면 라벨 | 도움말(필드 하단 1줄) | 입력 UI | 유효범위(화면) | 저장 변환 |
|---|---|---|---|---|---|
| `score_threshold` | 매수 기준점수 | 이 점수 이상인 종목만 매수 후보에 올립니다 | 숫자 + `점` 접미 | 0~100 정수 | 그대로 |
| `tp` | 익절 (수익 실현) | 이 수익률에 도달하면 자동으로 매도합니다 | 숫자 + `%` 접미 | 0~100 | **÷100 후 저장**(5 → 0.05) |
| `sl` | 손절 (손실 제한) | 이 손실률에 도달하면 자동으로 매도합니다 | 숫자 + `%` 접미 | 0~100 | **÷100 후 저장**(3 → 0.03) |
| `budget_by_count` | 종목당 예산 (매수 종목 수별) | 매수 종목 수에 따라 한 종목에 배분되는 금액입니다 | **3칸**(1/2/3종목) 각 숫자 + `원` 접미, 천단위 콤마 | 각 칸 0 초과 정수 | 그대로 |

- **tp/sl 표기·변환**: 화면은 항상 `%` 단위(입력칸 우측에 `%` 고정 표기). GET 시 `0.05 → 5` 로 ×100 표시, PUT 전 `÷100`. **변환은 프론트 폼 계층에서만** — json/py loader/ts validate 는 소수 `0~1` 그대로(로직 불변).
- **에러 문구**: 개발용("out of range") 금지. 사용자 문구로 — 예 `0~100 사이 숫자를 입력하세요`, `0보다 큰 금액을 입력하세요`.
- 기존 `app/admin/telegram/` 는 제거 또는 `/admin/settings`로 redirect.

### 7. nav 교체
- `broker-web/components/common/nav.tsx`: `/admin/telegram` "텔레그램" → `/admin/settings` "설정".

## 라우팅/경로 주의

- `repoRoot() = path.join(process.cwd(), "..")` — dev 서버가 `../etl/scripts`에 fs write(기존 telegram_channels.json 쓰기 경로와 동일). 로컬 전용(PLAN_JSON_ADMIN 결정 #5·8과 동일 제약).
- broker-web은 **Next 변형** — 코드 전 `node_modules/next/dist/docs/` 확인(`broker-web/AGENTS.md`).
- 색: semantic 토큰만(하드코딩색 hook 차단), `broker-web/DESIGN.md`.

## 단계별 체크리스트 (각 단계 = 테스트+보고+확인)

- [x] **1. 백엔드 config** — `close_bet.json` + `close_bet_config.py` 로더(범위검증·기본값폴백·셀프체크). 테스트: `test_close_bet_config.py` 8건 통과 + 셀프체크.
- [x] **2. 배치 배선** — run_close_bet/exit/report 가 config 읽게(CLI override 유지, default None→config). ps1 3개 하드코딩 인자 제거. 테스트: `test_close_bet_config_wiring.py` + close_bet 스위트 102건 통과.
- [x] **3. 프론트 셸+텔레그램 탭** — `/admin/settings` 탭 셸(useState 2탭) + `TelegramPanel` 컴포넌트 추출·이식. 검증: telegram-admin e2e 통과, 브라우저 육안.
- [x] **4. 종가베팅 탭** — `lib/close-bet-config.ts`+API+`CloseBetPanel`(3칸 예산, tp/sl %입력·÷100 저장, 콤마)+클라/서버 범위검증. 테스트: `close-bet-config-api.spec.ts` 4건, 브라우저 인라인검증 확인.
- [x] **5. 정리** — nav "채널"→"설정"(/admin/settings), `/admin/telegram` → redirect, e2e goto 갱신, PLAN/tech done. 커밋은 사용자.

## 착수 시 확인 필요 (미검증)

- `broker-web/components/common/nav.tsx` 링크 배열 형태·라벨 위치
- `/api/telegram-channels` route 파일 구조(POST action=add/restore 분기) — 이식 시 그대로 둠
- `run_close_bet.py` `load_dotenv`/DEFAULT 상수 로드 시점 — config 로드를 그 흐름에 맞출 것

## 범위 밖 (이번 안 함)

- 후보필터(N_TOP/PENNY_MAX/LOOKBACK) 웹화 — build_intraday·build_watchlist 상수 단일화 선행 필요. 별도 과제.
- `/admin/json` 흡수 — 유지.
- dry-run 웹 노출 — 안전상 제외.
