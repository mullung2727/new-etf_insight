---
name: new-etf-insight-server-dev
description: Use when starting, restarting, stopping, checking, or debugging local new-etf-insight development servers: api on 8000, broker on 8001, broker-web on 3000, PowerShell server scripts, port conflicts, health checks, or browser verification.
---

# new_etf_insight Server Dev

Use this skill for local server lifecycle work in `new-etf_insight`.

## Mental model (필수)

api / broker / broker-web 은 **로컬 상시 서버**다.

- 배치·에이전트 턴·채팅 세션에 묶인 일회성 프로세스가 **아니다**.
- 한 번 띄우면 **작업이 끝나도 끄지 않는다**. 장중·개발 세션 동안 계속 떠 있어야 한다.
- 에이전트 도구의 `background=true` 셸에 uvicorn/npm 을 직접 걸면 **에이전트/잡 종료와 함께 죽는다**. 이건 서버 기동이 아니다.
- “서버 상태 알려줘 / 꺼져 있으면 켜줘” 요청 = **상시 서버 ensure**. 임시 기동 후 방치·회수 금지.

## Core Rules

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight`.
- Read `README.md` before changing server commands, ports, or security boundaries.
- Do not expose `broker(:8001)` outside local development.
- Treat `api(:8000)` as public-read oriented unless the local code has added write endpoints.
- Do not run `broker-web` with Turbopack. Use the existing `npm run dev` script if developing, or `npm run build` then `npm run start` for verification.
- Do not manually activate Python virtual environments in scripts. Prefer `.\.venv\Scripts\python.exe -m ...`.
- Do not print secrets from `.env`.

## Services

| Service | Path | Port | Start command (참고용 — 직접 실행 금지, 아래 Ensure 절차 사용) |
|---|---|---:|---|
| api | `api/` | 8000 | `.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000` |
| broker | `broker/` | 8001 | `.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8001` |
| broker-web | `broker-web/` | 3000 | `npm run dev` |

## Agent procedure — Ensure servers (기본 동작)

서버 확인·기동·재기동 요청이 오면 **무조건 이 순서**:

### 1) 상태 확인

```powershell
Get-NetTCPConnection -LocalPort 8000,8001,3000 -ErrorAction SilentlyContinue |
  Where-Object { $_.State -eq "Listen" }

Invoke-WebRequest -UseBasicParsing -Uri http://localhost:8000/health
Invoke-WebRequest -UseBasicParsing -Uri http://localhost:8001/health
Invoke-WebRequest -UseBasicParsing -Uri http://localhost:3000
```

- 3개 포트 Listen + health(web은 HTTP 응답) OK → **그대로 두고 상태만 보고. 재시작하지 말 것.**

### 2) 하나라도 다운이면 기동/재기동

**허용된 기동 방법 (이것만):**

```powershell
.\scripts\restart_all_servers.ps1
```

이 스크립트는:

- `.server-pids/` 및 포트 리스너 정리
- **Job/에이전트 세션에서 분리된(detached) 상시 프로세스**로 3개 기동
- PID → `.server-pids\*.pid`
- 로그 → `ops\logs\<name>-yyyyMMdd.(out|err).log`
- health + **기동 3초 후 durability 재확인**
- 성공 시 exit 0, 실패 시 exit 1

### 3) 기동 후 검증 (필수)

스크립트 종료 직후 **다시** health/Listen 확인.

- 3개 모두 OK → “상시 서버로 기동됨” 보고. **프로세스를 종료·회수하지 말 것.**
- 하나라도 실패 → `ops\logs\` 해당 out/err 로그 tail 후 원인 보고. 에이전트 백그라운드 uvicorn으로 때우지 말 것.

### 4) 금지 (에이전트)

| 금지 | 이유 |
|---|---|
| `background=true` 로 `uvicorn` / `npm run dev` 직접 실행 | 에이전트 세션 자식 → 세션 끝나면 죽음 |
| 턴 종료 시 서버 kill | 상시 서버 계약 위반 |
| 일부만 띄우기 (broker만 등) — 사용자가 특정 서비스만 지정한 경우 제외 | 배치/UI가 3개 전제를 가짐 |
| Turbopack 로 broker-web 기동 | node 폭주 이력 |
| PID 파일만 보고 kill (커맨드라인에 프로젝트 root 없는 PID) | 타인 프로세스 오살 |

사용자가 “꺼줘/재시작”을 **명시**한 경우에만 stop/restart.  
“상태 봐줘”만이면 다운일 때만 ensure, 살아 있으면 no-op.

## Restart All

```powershell
.\scripts\restart_all_servers.ps1
```

수동(사람) 실행과 에이전트 실행 **동일 진입점**. 다른 기동 경로를 새로 만들지 말 것.

## Debugging Checklist

1. Listeners on `8000`, `8001`, `3000`
2. Health URLs above
3. PID files under `.server-pids/` — process alive + command line contains project root
4. Logs under `ops\logs\`
5. If restart script is changed, inspect for:

- no use of `$pid` as a loop variable (use `$processId`);
- no dependency on venv activation;
- no hard kill based only on stale PID files;
- no Turbopack command for `broker-web`;
- **detached start** (`UseShellExecute` / job breakaway) — agent-attached child process 금지;
- health checks **and** post-start durability check.

## Common Failure Modes

- `$pid` conflicts with PowerShell automatic variable `$PID`; use `$processId`.
- `uvicorn --reload` can leave parent/child processes; stop process trees when resetting.
- stale PID files can point to unrelated reused PIDs; verify command line contains this project root before killing PID-file processes.
- `next dev` may take longer than API servers; allow polling time before declaring failure.
- **에이전트 툴 백그라운드에 직접 띄운 서버가 수 분 내 사망** → ensure 절차 미준수. `restart_all_servers.ps1` 로 다시 ensure.
- PC 절전/재부팅/사용자가 창 종료 → 정상적으로 다운. 다시 ensure.
- 배치(close-bet-exit 등)는 broker 상시를 **전제**한다. 배치 전에 broker health 확인.
