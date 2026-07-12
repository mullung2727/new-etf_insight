import { NextRequest, NextResponse } from "next/server";

const BASE = process.env.FASTAPI_BASE_URL ?? "http://localhost:8000";

/** 브라우저 same-origin 프록시 → api(:8000) 수집 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const res = await fetch(`${BASE}/youtube/collect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      // 수집은 네트워크 I/O — 여유 타임아웃
      signal: AbortSignal.timeout(1_800_000),
    });
    const text = await res.text();
    let data: unknown = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { error: text || `upstream ${res.status}` };
    }
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "collect proxy failed" },
      { status: 502 },
    );
  }
}
