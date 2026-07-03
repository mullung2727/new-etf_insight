// api(:8000, ETF/데이터 게이트웨이)는 broker-web과 같은 호스트 가정.
// brokerBase(:8001)와 동일 패턴 — 브라우저 host에서 런타임 유도(localhost/LAN 무관).
// NEXT_PUBLIC_FASTAPI_URL 이 있으면 우선(다른 호스트/포트 강제 시만).
export function apiBase(): string {
  if (process.env.NEXT_PUBLIC_FASTAPI_URL) return process.env.NEXT_PUBLIC_FASTAPI_URL;
  if (typeof window !== "undefined") return `http://${window.location.hostname}:8000`;
  return "http://localhost:8000"; // SSR fallback
}
