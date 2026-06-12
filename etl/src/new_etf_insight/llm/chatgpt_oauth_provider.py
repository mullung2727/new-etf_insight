from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests


CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_URL = "https://auth.openai.com/oauth/token"
DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
DEFAULT_PROFILE_ID = "openai:default"
TOKEN_REFRESH_MARGIN_MS = 60_000
LOCK_TIMEOUT_SECONDS = 30
STALE_LOCK_SECONDS = 600


class ChatGptOAuthProvider:
    def generate_json(
        self,
        prompt: str,
        *,
        output_schema_path: Path,
        search: bool = False,
    ) -> str:
        token, account_id = self._ensure_access_token()
        schema = json.loads(output_schema_path.read_text(encoding="utf-8"))
        model = os.getenv("ETF_OPENAI_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-5.5"

        body: dict[str, Any] = {
            "model": model,
            "store": False,
            "stream": True,
            "instructions": "Output only valid JSON matching the provided schema.",
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": output_schema_path.stem.replace("-", "_"),
                    "schema": schema,
                    "strict": True,
                }
            },
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
        # chatgpt.com/backend-api/codex/responses currently rejects the public
        # Responses API web_search_preview tool name. Keep the prompt path in
        # Python/OAuth and let callers provide retrieved context explicitly.

        response = requests.post(
            self._codex_responses_url(),
            headers=self._headers(token, account_id),
            json=body,
            stream=True,
            timeout=180,
        )
        if not response.ok:
            raise RuntimeError(f"ChatGPT OAuth responses call failed ({response.status_code}): {response.text[:1000]}")

        return self._extract_json_from_sse(response)

    def _ensure_access_token(self) -> tuple[str, str]:
        auth_path = self._auth_profiles_path()
        profile_id = os.getenv("ETF_OAUTH_PROFILE_ID") or DEFAULT_PROFILE_ID

        with _file_lock(auth_path.with_name(auth_path.name + ".lock")):
            store = self._read_store(auth_path)
            profile = store.get("profiles", {}).get(profile_id)
            if not isinstance(profile, dict):
                raise RuntimeError(f"OAuth profile not found: {profile_id}")
            if profile.get("type") != "oauth":
                raise RuntimeError(f"OAuth profile {profile_id} is not an oauth profile")

            expires = int(profile.get("expires") or 0)
            now_ms = int(time.time() * 1000)
            if profile.get("access") and expires > now_ms + TOKEN_REFRESH_MARGIN_MS:
                return str(profile["access"]), self._account_id(profile, str(profile["access"]))

            refresh = profile.get("refresh")
            if not refresh:
                raise RuntimeError(f"OAuth profile {profile_id} has no refresh token")
            refreshed = self._refresh_token(str(refresh))
            profile["access"] = refreshed["access"]
            profile["refresh"] = refreshed["refresh"]
            profile["expires"] = refreshed["expires"]
            self._write_store(auth_path, store)
            return refreshed["access"], self._account_id(profile, refreshed["access"])

    def _refresh_token(self, refresh_token: str) -> dict[str, Any]:
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if not response.ok:
            raise RuntimeError(f"OpenAI OAuth token refresh failed ({response.status_code}): {response.text[:1000]}")
        data = response.json()
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires_in = data.get("expires_in")
        if not access or not refresh or not expires_in:
            raise RuntimeError("OpenAI OAuth refresh response missing access_token, refresh_token, or expires_in")
        return {
            "access": access,
            "refresh": refresh,
            "expires": int(time.time() * 1000) + int(expires_in) * 1000,
        }

    def _headers(self, token: str, account_id: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id,
            "originator": "openclaw",
            "User-Agent": "openclaw (python batch)",
            "OpenAI-Beta": "responses=experimental",
            "accept": "text/event-stream",
            "content-type": "application/json",
        }

    def _extract_json_from_sse(self, response: requests.Response) -> str:
        completed: dict[str, Any] | None = None
        text_parts: list[str] = []
        for raw_line in response.iter_lines(decode_unicode=True):
            if isinstance(raw_line, bytes):
                raw_line = raw_line.decode("utf-8", errors="replace")
            if not raw_line or not raw_line.startswith("data:"):
                continue
            payload = raw_line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            event = json.loads(payload)
            event_type = event.get("type")
            if event_type in {"response.output_text.delta", "response.text.delta"}:
                delta = event.get("delta")
                if isinstance(delta, str):
                    text_parts.append(delta)
            if event_type in {"response.completed", "response.done", "response.incomplete"}:
                completed = event.get("response") if isinstance(event.get("response"), dict) else event

        if text_parts:
            return "".join(text_parts).strip()
        if completed:
            text = _extract_text_from_response(completed)
            if text:
                return text.strip()
        raise RuntimeError("ChatGPT OAuth response did not contain output text")

    def _auth_profiles_path(self) -> Path:
        configured = os.getenv("OPENCLAW_AUTH_PROFILES_PATH") or os.getenv("ETF_AUTH_PROFILES_PATH")
        if configured:
            return Path(configured)
        return Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"

    def _codex_responses_url(self) -> str:
        base = (os.getenv("ETF_CHATGPT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        if base.endswith("/codex/responses"):
            return base
        if base.endswith("/codex"):
            return f"{base}/responses"
        return f"{base}/codex/responses"

    def _read_store(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_store(self, path: Path, store: dict[str, Any]) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _account_id(self, profile: dict[str, Any], token: str) -> str:
        account_id = profile.get("accountId") or _extract_account_id_from_jwt(token)
        if not account_id:
            raise RuntimeError("OAuth profile is missing accountId and token account claim")
        return str(account_id)


class _file_lock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "_file_lock":
        deadline = time.time() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if self._is_stale():
                    self.path.unlink(missing_ok=True)
                    continue
                if time.time() >= deadline:
                    raise RuntimeError(f"OAuth profile lock timeout for {self.path}")
                time.sleep(0.2)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)

    def _is_stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > STALE_LOCK_SECONDS
        except FileNotFoundError:
            return False


def _extract_account_id_from_jwt(token: str) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        import base64

        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
        parsed = json.loads(decoded)
    except Exception:
        return None
    claim = parsed.get("https://api.openai.com/auth")
    if isinstance(claim, dict) and isinstance(claim.get("chatgpt_account_id"), str):
        return claim["chatgpt_account_id"]
    return None


def _extract_text_from_response(response: dict[str, Any]) -> str | None:
    output = response.get("output")
    if not isinstance(output, list):
        return None
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict):
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "\n".join(parts) if parts else None
