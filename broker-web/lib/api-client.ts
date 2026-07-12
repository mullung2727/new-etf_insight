const BASE_URL = process.env.FASTAPI_BASE_URL ?? "http://localhost:8000";

type ParamValue = string | number | boolean | null | undefined | readonly (string | number)[];

function buildSearch(params?: Record<string, ParamValue>): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const v of value) sp.append(key, String(v));
    } else {
      sp.append(key, String(value));
    }
  }
  const qs = sp.toString();
  return qs ? `?${qs}` : "";
}

export async function apiGet<T>(path: string, params?: Record<string, ParamValue>): Promise<T> {
  const url = `${BASE_URL}${path}${buildSearch(params)}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`FastAPI ${res.status} at ${url}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export async function apiGetOrNull<T>(path: string, params?: Record<string, ParamValue>): Promise<T | null> {
  const url = `${BASE_URL}${path}${buildSearch(params)}`;
  const res = await fetch(url, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`FastAPI ${res.status} at ${url}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`FastAPI ${res.status} at ${url}: ${text}`);
  }
  return res.json() as Promise<T>;
}
