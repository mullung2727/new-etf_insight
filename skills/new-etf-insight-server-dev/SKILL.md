---
name: new-etf-insight-server-dev
description: Use when starting, restarting, stopping, checking, or debugging local new-etf-insight development servers: api on 8000, broker on 8001, broker-web on 3000, PowerShell server scripts, port conflicts, health checks, or browser verification.
---

# new_etf_insight Server Dev

Use this skill for local server lifecycle work in `new-etf_insight`.

## Core Rules

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight`.
- Read `README.md` before changing server commands, ports, or security boundaries.
- Do not expose `broker(:8001)` outside local development.
- Treat `api(:8000)` as public-read oriented unless the local code has added write endpoints.
- Do not run `broker-web` with Turbopack. Use the existing `npm run dev` script if developing, or `npm run build` then `npm run start` for verification.
- Do not manually activate Python virtual environments in scripts. Prefer `.\.venv\Scripts\python.exe -m ...`.
- Do not print secrets from `.env`.

## Services

| Service | Path | Port | Start command |
|---|---|---:|---|
| api | `api/` | 8000 | `.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000` |
| broker | `broker/` | 8001 | `.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8001` |
| broker-web | `broker-web/` | 3000 | `npm run dev` |

## Restart All

For a clean local reset, use:

```powershell
.\scripts\restart_all_servers.ps1
```

This script should:

- stop known project launcher PIDs from `.server-pids/`;
- stop remaining listeners on ports `8000`, `8001`, and `3000`;
- start three visible PowerShell windows;
- write fresh PID files under `.server-pids/`;
- poll `http://localhost:8000/health`, `http://localhost:8001/health`, and `http://localhost:3000`.

## Debugging Checklist

1. Check listeners:

```powershell
Get-NetTCPConnection -LocalPort 8000,8001,3000 -ErrorAction SilentlyContinue |
  Where-Object { $_.State -eq "Listen" }
```

2. Check health:

```powershell
Invoke-WebRequest -UseBasicParsing -Uri http://localhost:8000/health
Invoke-WebRequest -UseBasicParsing -Uri http://localhost:8001/health
Invoke-WebRequest -UseBasicParsing -Uri http://localhost:3000
```

3. If a restart script is changed, inspect for:

- no use of `$pid` as a loop variable;
- no dependency on venv activation;
- no hard kill based only on stale PID files;
- no Turbopack command for `broker-web`;
- health checks after startup.

## Common Failure Modes

- `$pid` conflicts with PowerShell automatic variable `$PID`; use `$processId`.
- `uvicorn --reload` can leave parent/child processes; stop process trees when resetting.
- stale PID files can point to unrelated reused PIDs; verify command line contains this project root before killing PID-file processes.
- `next dev` may take longer than API servers; allow polling time before declaring failure.
