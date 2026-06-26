# PLAN — 테넌트 도커화 (broker + broker-web)

## 요약

테넌트 쌍(broker:8001 + broker-web:3000)을 Docker Compose(dev) 1세트로 띄운다.
api(:8000)와 etl DB는 **호스트에 그대로** 두고, 컨테이너가 `host.docker.internal`로 접근한다.
중앙(etl + 공용DB + api)은 이번 범위 밖(추후 리눅스 서버).

---

## 범위 / 비범위

- **범위**: `broker` 이미지, `broker-web` 이미지, repo 루트 `compose.yml`(dev), 문서.
- **비범위**: api 컨테이너화, prod compose, 리눅스 배포, CI. (다음 단계)

---

## 확정 결정 (4)

1. **api 연결**: api는 호스트 uvicorn(:8000) 유지. 컨테이너 → `http://host.docker.internal:8000`.
   - `localhost` 아님(컨테이너 안 localhost = 자기 자신). prod에선 실주소로 교체.
2. **시크릿 주입**: `env_file: ./.env` (repo-root). 키움 키가 거기 `KIWOON_MOCK_TR_APP_KEY/SECRET/ACCOUNT_NO` 로 있음(config.py가 이 별칭 수용). `broker/.env`는 없으며 만들지 않음. DART/KRX 등 broker 불필요 키도 같이 주입되나 무해.
3. **상태파일 영속**: dev는 broker 소스 전체를 bind mount(`./broker:/app`)하므로 `notes.db`·`.token_cache.json`도 자동 영속(호스트 파일 그대로). 별도 단일파일 마운트 불필요.
   - 토큰캐시는 호스트 etl 배치와 공유 → 호스트 `broker/.token_cache.json` 이 곧 컨테이너 것.
4. **dev only**: broker·web 둘 다 소스 bind mount + 핫리로드(broker `uvicorn --reload`, web `next dev`). prod(빌드 구워넣기)는 나중.

---

## 네트워크 / 포트

| 서비스 | 컨테이너 | 호스트 노출 | 호출 주체 | 주소 |
|--------|----------|-------------|-----------|------|
| broker | 8001 | `8001:8001` | 브라우저(NEXT_PUBLIC) | `http://localhost:8001` |
| broker-web | 3000 | `3000:3000` | 사용자 브라우저 | `http://localhost:3000` |
| api (호스트, 컨테이너 밖) | — | 호스트 8000 | broker-web **서버**(FASTAPI_BASE_URL) | `http://host.docker.internal:8000` |

- `NEXT_PUBLIC_BROKER_API_URL=http://localhost:8001` — 브라우저가 호스트 노출 포트로 접근(OK, broker 컨테이너가 8001 publish).
- `FASTAPI_BASE_URL=http://host.docker.internal:8000` — Next 서버(컨테이너)가 호스트 api 접근. **`.env.local` 값 override 필수.**
- 리눅스 호환 위해 compose에 `extra_hosts: ["host.docker.internal:host-gateway"]` 추가.

---

## 생성 파일

```
broker/Dockerfile
broker/.dockerignore
broker-web/Dockerfile          # dev: node + npm run dev
broker-web/.dockerignore
compose.yml                    # repo 루트
```

`.env.local`은 건드리지 않고, web 컨테이너 env를 compose `environment:`로 덮어쓴다.

---

## Dockerfile 개요

### broker (`broker/Dockerfile`)
- base `python:3.12-slim`
- 의존성 설치: `uv sync` (uv.lock 확정 존재). pyproject deps: fastapi/uvicorn[standard]/fastapi-mcp/httpx/pydantic/python-dotenv/websockets/sse-starlette.
- `COPY` broker 소스(main.py, kiwoom/, notes/, routers/) — dev는 어차피 bind mount로 덮임(이미지 COPY는 prod 대비/캐시용).
- `CMD uvicorn main:app --host 0.0.0.0 --port 8001 --reload` (dev 핫리로드).
- WS매니저가 기동 시 키움 연결 시도 → **키움 앱 로그인 + 유효 토큰 전제**(컨테이너도 동일).

### broker-web (`broker-web/Dockerfile`, dev)
- base `node:22-slim` (Next 16/React 19, node ≥20)
- dev는 빌드 안 함: `npm ci` 후 `CMD npm run dev`(`next dev`, host 0.0.0.0)
- 소스는 compose bind mount, `node_modules`는 익명 볼륨으로 가림(호스트 것과 충돌 방지).
- ⚠ AGENTS.md: 이 Next는 커스텀. 빌드/standalone은 prod 단계에서 docs 확인 후.

---

## compose.yml 골자

```yaml
services:
  broker:
    build: ./broker
    ports: ["8001:8001"]
    env_file: ./.env            # repo-root, KIWOON_MOCK_TR_* 키 포함
    volumes:
      - ./broker:/app           # 소스+notes.db+.token_cache.json 영속(핫리로드)
      - /app/.venv              # uv venv는 익명볼륨으로 가림(호스트 OS 바이너리 충돌 방지)
      - /app/__pycache__
    extra_hosts: ["host.docker.internal:host-gateway"]

  broker-web:
    build: ./broker-web
    ports: ["3000:3000"]
    environment:
      NEXT_PUBLIC_BROKER_API_URL: http://localhost:8001
      FASTAPI_BASE_URL: http://host.docker.internal:8000
    volumes:
      - ./broker-web:/app
      - /app/node_modules
    extra_hosts: ["host.docker.internal:host-gateway"]
    depends_on: [broker]
```

(세부 — workdir, user, healthcheck — 구현 시 확정.)

---

## 사전 조건 (compose up 전, 호스트)

1. 호스트에서 api 기동: `cd api && uvicorn main:app --host 0.0.0.0 --port 8000`
   - **`--host 0.0.0.0` 필수.** 기본 `127.0.0.1` 바인딩이면 컨테이너가 `host.docker.internal:8000`(게이트웨이 IP, 루프백 아님)으로 못 붙음. (G3 실측 확인.)
2. etl DB 존재: `etl/db/etf_insight.sqlite3`, `watchlist.sqlite3`, `krx_ohlcv.duckdb`
3. repo-root `.env` 에 키움 키(`KIWOON_MOCK_TR_*`) + `KIWOOM_ENV=paper` 존재 (이미 있음).
4. 키움 로그인 + 토큰 유효(WS 연결용).
5. `broker/notes.db`, `broker/.token_cache.json` **파일** 존재(bind mount 대상). 없으면 Docker가 디렉토리로 생성하는 함정 → up 전 `touch`/초기화.

---

## 실행 단계 (3 게이트, 각 게이트 후 보고 → 확인 → 다음)

> 파일 작성은 쪼개지 않고 한 번에. 검증만 게이트로 나눔(서비스별 격리 진단).

- **G1. 작성 + 빌드** ✅: 파일 5개 작성 → `docker compose build`. 두 이미지 생성 확인(broker 348MB, broker-web 1.94GB).
- **G2. broker 거래경로 (핵심 게이트)** ✅: env_file(repo-root .env) 주입 → `env=paper`/account 로드, 키움 WS connected, `/account/balance` 실데이터, token_cache·notes.db 영속, 핫리로드 모두 실측 통과.
- **G3. 통합** ✅: 호스트 api(0.0.0.0:8000) + 전체 `up`. web `/watchlist` 200(api 로그 `GET /watchlist 200` 실측), `/api/corps`·`/trading`·`/watchlist/[code]` 200, FASTAPI_BASE_URL override 확인, broker :8001 도달. 브라우저 JS 클릭/WS 스트림은 별도 눈검사.

### 빌드 환경 주의 (실측, 중요)
- **`docker compose build`/`pull` 은 대화식 Windows 세션에서만 됨.** 원격 VSCode 터미널·에이전트(비대화식) 세션은 `docker-credential-desktop`/`wincred` 가 `A specified logon session does not exist`(Win32 ERROR_NO_SUCH_LOGON_SESSION)로 크래시 → 공개 이미지조차 pull 실패. **시작메뉴에서 직접 띄운 PowerShell**에서 빌드할 것.
- `up`/`down`/`logs`/`exec`/`curl` 등 **레지스트리 비접근 op는 어느 세션이든 OK.** → 빌드만 직접 PowerShell, 나머지는 무관.
- 리눅스 서버 배포 시엔 이 문제 없음(Desktop 자격헬퍼·Windows 로그온세션 의존 자체가 없음).

---

## 리스크 / 미결

- ~~**키움 WS from 컨테이너**~~ ✅ 해결: WS connected + `/account/balance` 실데이터 + token_cache 영속 G2에서 실측.
- **`TOKEN_CACHE_PATH` 미설정**: 어디에도 set 안 됨 → config가 `broker/` 기준 상대경로 default → 컨테이너 `/app/.token_cache.json`. 소스 bind mount와 경로 일치(OK). 절대경로로 바꾸면 마운트 깨짐 주의.
- **의존성 설치 방식**: `uv sync` 확정(uv.lock 존재). `.venv`는 익명볼륨으로 가려 호스트(Windows) 빌드 venv가 컨테이너(linux)로 새는 것 차단 필수.
- **broker-web dev 핫리로드 filewatch**: Windows 호스트 bind mount면 느림/누락 가능(기존 메모). 동작은 하나 dev 편의 이슈.
- **`.env.local`의 FASTAPI_BASE_URL=localhost:8000**: 반드시 compose env로 override(안 하면 컨테이너가 자기 자신 호출).
- **prod 이식**: `host.docker.internal`은 dev 전용 배선 → prod에서 실주소/서비스명 교체.
