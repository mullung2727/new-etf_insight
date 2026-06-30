// broker(:8001)는 broker-web과 같은 호스트에 있다고 가정.
// 브라우저가 접속한 host에서 런타임 유도 → localhost/LAN IP 무관 자동 매칭.
// IP를 어디에도 박지 않아 네트워크/머신이 바뀌어도 무수정.
// NEXT_PUBLIC_BROKER_API_URL 이 있으면 그게 우선(다른 호스트/포트로 강제할 때만).
export function brokerBase(): string {
  if (process.env.NEXT_PUBLIC_BROKER_API_URL) return process.env.NEXT_PUBLIC_BROKER_API_URL;
  if (typeof window !== "undefined") return `http://${window.location.hostname}:8001`;
  return "http://localhost:8001"; // SSR fallback (브라우저 아닌 곳)
}
