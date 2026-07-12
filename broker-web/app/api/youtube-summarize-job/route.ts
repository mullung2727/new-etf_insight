import { NextRequest, NextResponse } from "next/server";

const BASE = process.env.FASTAPI_BASE_URL ?? "http://localhost:8000";

/** 단일 영상 요약 잡 시작 (백그라운드) */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const res = await fetch(`${BASE}/youtube/summarize-job`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
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
      { error: err instanceof Error ? err.message : "summarize-job proxy failed" },
      { status: 502 },
    );
  }
}
