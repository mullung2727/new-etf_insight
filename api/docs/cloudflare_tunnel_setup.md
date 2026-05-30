# Cloudflare Tunnel Setup

Use this to expose the local FastAPI server (`http://localhost:8000`) to the
public internet so that a Vercel-deployed Next.js BFF (or a remote MCP
client) can reach it. No port forwarding required.

## Prerequisites

- A domain managed by Cloudflare (e.g. `example.com`).
- A free Cloudflare account.

## 1. Install cloudflared (Windows)

```powershell
winget install --id Cloudflare.cloudflared
# Verify
cloudflared --version
```

## 2. Authenticate

```powershell
cloudflared tunnel login
```

A browser opens — pick the domain you want to attach the tunnel to. A
certificate is saved to `%USERPROFILE%\.cloudflared\cert.pem`.

## 3. Create the tunnel

```powershell
cloudflared tunnel create etf-insight-api
```

This prints a tunnel **UUID** and writes `<UUID>.json` (credentials) into
`%USERPROFILE%\.cloudflared\`.

## 4. Configure routing

Create `%USERPROFILE%\.cloudflared\config.yml`:

```yaml
tunnel: <UUID>
credentials-file: C:\Users\<you>\.cloudflared\<UUID>.json

ingress:
  - hostname: api.example.com
    service: http://localhost:8000
  - service: http_status:404
```

## 5. DNS route

```powershell
cloudflared tunnel route dns etf-insight-api api.example.com
```

## 6. Run as a Windows service (auto-start on boot)

```powershell
# Run as administrator
cloudflared service install
```

Verify:

```powershell
Get-Service cloudflared
Invoke-WebRequest https://api.example.com/health -UseBasicParsing
```

## 7. Update FastAPI CORS

Add the Vercel deploy URL to `api/.env`:

```env
CORS_ORIGINS=http://localhost:3000,https://<your-vercel-app>.vercel.app
```

Restart the uvicorn process so the new origins take effect.

## 8. Wire Vercel to the tunnel

In the Vercel dashboard add an env var:

```
FASTAPI_BASE_URL=https://api.example.com
```

Redeploy the Next.js app. Pages now fetch through the tunnel.

## Verification checklist

- [ ] `curl https://api.example.com/health` returns 200
- [ ] Vercel preview deploy renders ETF data
- [ ] FastAPI `uvicorn.out` shows requests originating from Cloudflare IPs
- [ ] After a PC reboot the tunnel comes back up automatically
